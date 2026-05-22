"""
MULTI-AI STOCK SIGNAL ENGINE v4 — ULTRA-RELIABLE EDITION
=========================================================
Combines 6 AI models + wordf keyword cross-validation + historical
price validation for high-accuracy consensus trading signals:
  1. Groq Llama 3.3 70B    - PRIMARY (smartest, best reasoning)
  2. Groq Llama 3.1 8B     - SECONDARY (fast cross-check)
  3. Groq Llama 4 Scout    - TERTIARY (newest Llama 4)
  4. Groq Qwen3 32B        - QUATERNARY (strong reasoning)
  5. HuggingFace FinBERT   - NLP sentiment (DEMOTED — tiebreaker only)
  6. Google Gemini          - OPTIONAL bonus (auto-disables on quota)

v4 Changes from v3:
- wordf v2 cross-validation (catches wrong BUY/SELL direction)
- Historical price validation (checks if pattern worked before)
- FinBERT demoted (weight 0.15→0.10, overridden when disagrees with all LLMs)
- Smart model conflict = hard suppress (70B vs Qwen disagree → NO TRADE)
- Raised thresholds (MIN_AGREE: 2→3, NORMAL: 50→65, STRONG: 75→80)
- Compliance/quality pre-filtering (skips junk BEFORE API calls)
- Improved prompt with Indian market edge cases
- Price trend context in output

Usage:
  $env:GROQ_API_KEY="..."; $env:GEMINI_API_KEY="..."; $env:HF_TOKEN="..."
  python multi_ai.py
"""

import os
import re
import json
import time
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from gspread.exceptions import APIError
from groq import Groq
from oauth2client.service_account import ServiceAccountCredentials
from huggingface_hub import InferenceClient
from google import genai

# ── Import wordf v2 filters for cross-validation ──
try:
    from words import (
        is_compliance_filing, text_quality_score,
        event_score as wordf_event_score,
        analyze_historical_patterns, fetch_price_change,
    )
    WORDF_AVAILABLE = True
except ImportError:
    WORDF_AVAILABLE = False
    print("[WARN] words.py not importable — running without wordf cross-validation")

# ── Import market intelligence + alerts ──
try:
    from market_utils import enrich_signal, check_freshness
    MARKET_UTILS_OK = True
except ImportError:
    MARKET_UTILS_OK = False

try:
    from alerts import send_alert, alert_from_multi_ai, send_summary
    ALERTS_OK = True
except ImportError:
    ALERTS_OK = False

# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# =========================================================
# CONFIG
# =========================================================
SHEET_ID     = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"
INPUT_SHEETS = ["nse", "bse"]
OUTPUT_WS    = "multi_ai"

# 6-model weights (with Gemini) — FinBERT DEMOTED
W_70B     = 0.25
W_8B      = 0.10
W_SCOUT   = 0.15
W_QWEN    = 0.20
W_FINBERT = 0.10   # was 0.15 — demoted (unreliable on Indian filings)
W_GEMINI  = 0.20

# 5-model weights (no Gemini) — FinBERT DEMOTED
WF_70B     = 0.30
WF_8B      = 0.15
WF_SCOUT   = 0.20
WF_QWEN    = 0.25   # was 0.20 — boost smartest models
WF_FINBERT = 0.10   # was 0.15 — demoted

# Thresholds (RAISED for reliability)
STRONG_THRESHOLD = 80    # was 75
NORMAL_THRESHOLD = 65    # was 50
MIN_MODELS_AGREE = 3     # was 2 — need REAL consensus

# Rate limiting
REQUEST_DELAY = 2.5

# =========================================================
# GLOBAL STATE
# =========================================================
gemini_disabled = False

# =========================================================
# GOOGLE SHEETS AUTH
# =========================================================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gc    = gspread.authorize(creds)
ss    = gc.open_by_key(SHEET_ID)

# =========================================================
# AI CLIENTS
# =========================================================
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
hf_client = InferenceClient(provider="auto", api_key=os.environ.get("HF_TOKEN"))

# =========================================================
# HELPERS
# =========================================================
def extract_json(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group()) if match else None
    except Exception:
        return None

def open_or_create(title):
    try:
        return ss.worksheet(title)
    except Exception:
        return ss.add_worksheet(title=title, rows="500", cols="15")

def sheet_to_records(ws):
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    headers = [h.strip().upper() for h in rows[0]]
    return [dict(zip(headers, r)) for r in rows[1:] if any(r)]

def normalise_ticker(x):
    return str(x).strip().upper()

def _is_write_quota_error(err):
    status = getattr(getattr(err, "response", None), "status_code", None)
    msg = str(err).lower()
    return status == 429 and ("write requests" in msg or "quota exceeded" in msg)

def with_sheets_backoff(op, max_attempts=6, base_delay=2.0):
    for attempt in range(max_attempts):
        try:
            return op()
        except APIError as err:
            if _is_write_quota_error(err) and attempt < max_attempts - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise

# =========================================================
# NOISE FILTER (EXPANDED — includes wordf v2 keywords)
# =========================================================
IGNORE_KEYWORDS = [
    "scrutinizer", "certificate", "postal ballot", "agm",
    "newspaper publication", "trading window", "shareholding pattern",
    "voting results", "analyst meeting", "investor presentation",
    "record date", "book closure", "committee meeting",
    "clarification", "corporate action", "re-appointment",
    "compliance certificate", "newspaper advertisement",
    "esop", "intimation", "closure of trading window",
    "loss of share certificate", "duplicate share certificate",
    "monthly reporting", "change in kmp", "public notice",
    "appointment", "cessation",
    # v4 additions — catch more noise BEFORE wasting API calls
    "pursuant to regulation", "under regulation",
    "submission of annual", "submission of quarterly",
    "annual return", "code of conduct",
    "composition of board", "composition of committee",
    "familiarization programme", "policy on related",
    "corporate governance report", "secretarial compliance",
    "transfer of shares", "transmission of shares",
    "reclassification of promoter",
    "statement of investor complaints",
    "e-voting", "scrutinizer report",
    "outcome of board meeting",
]

def is_noise(text):
    t = text.lower()
    return any(kw in t for kw in IGNORE_KEYWORDS)

# =========================================================
# ANALYSIS PROMPT (v4 — with edge cases)
# =========================================================
PROMPT = """You are an elite Indian stock market analyst specializing in NSE/BSE corporate announcements.
Analyze this announcement — will it MOVE the stock price in 1-2 trading days?

STRONG BUY SIGNALS (high confidence):
- Large order win / LOA / work order worth significant amount
- Record profit / earnings beat / revenue surge / EBITDA growth
- Buyback / bonus issue / stock split / rights issue
- NCLT APPROVES resolution plan (turnaround — can cause 100%+ gains)
- Debt-free announcement / deleveraging complete
- Acquisition of a COMPANY or BUSINESS (not routine share transfers)
- Promoter buying significant stake in open market
- Demerger / spin-off approval (value unlocking)
- Government / defence contract awarded

STRONG SELL SIGNALS (high confidence):
- NCLT ADMITS insolvency petition / CIRP initiated
- Fraud detected / accounting irregularities / forensic audit
- SEBI action / ban / penalty against company
- Auditor resignation / qualified audit opinion
- Loan default / NPA classification / wilful defaulter
- Pledge invocation / margin call triggered
- Production halt / plant shutdown

CRITICAL EDGE CASES — you MUST get these RIGHT:
- "acquisition of shares under regulation" → NO TRADE (routine compliance)
- "acquisition of XYZ company/business/subsidiary" → BUY (real deal)
- "NCLT admits insolvency" / "CIRP" → STRONG SELL
- "NCLT approves resolution plan" → STRONG BUY
- "share purchase agreement executed" → BUY (real deal)
- "transfer of shares under SEBI" → NO TRADE (routine)
- "rights entitlement" / "rights issue" → moderate BUY
- "pursuant to regulation 30/31/74" → NO TRADE (compliance)
- "outcome of board meeting" → NO TRADE (unless contains material event)
- "appointment/cessation of director" → NO TRADE (routine)
- "loss of share certificate" → NO TRADE
- "debt-free" / "zero debt" → BUY
- "auditor resignation" → SELL

MUST return NO TRADE for:
- Compliance filings, board meetings, AGM, voting results
- Appointments, cessations, newspaper ads, investor presentations
- Routine disclosures, share transfers, record dates, ESOP
- Any filing with "pursuant to regulation" language
- Annual reports, quarterly results (unless exceptional surprise)

CONFIDENCE RULES (be STRICT):
- 90-100: Crystal clear, strong, material event with large financial impact
- 75-89: Clear event with moderate-to-strong impact
- 60-74: Probable impact but some ambiguity
- <60: Unclear → you MUST return NO TRADE

COMPANY: {ticker}
ANNOUNCEMENT: {text}

Return ONLY valid JSON (no explanation outside JSON):
{{"action": "BUY"|"SELL"|"NO TRADE", "confidence": <0-100>, "reason": "<1 line factual>"}}"""

# =========================================================
# MODEL 1: LLAMA 3.3 70B (PRIMARY — smartest)
# =========================================================
def analyze_groq_70b(ticker, text):
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": PROMPT.format(ticker=ticker, text=text[:3000])}],
            temperature=0.1,
        )
        data = extract_json(resp.choices[0].message.content.strip())
        if data and data.get("action") in ("BUY", "SELL", "NO TRADE"):
            return {"action": data["action"].upper(), "confidence": min(100, max(0, int(data.get("confidence", 0)))), "reason": data.get("reason", "")}
    except Exception as e:
        print(f"  [70B ERR] {str(e)[:80]}")
    return {"action": "NO TRADE", "confidence": 0, "reason": "error"}

# =========================================================
# MODEL 2: LLAMA 3.1 8B (FAST cross-check)
# =========================================================
def analyze_groq_8b(ticker, text):
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": PROMPT.format(ticker=ticker, text=text[:2000])}],
            temperature=0.2,
        )
        data = extract_json(resp.choices[0].message.content.strip())
        if data and data.get("action") in ("BUY", "SELL", "NO TRADE"):
            return {"action": data["action"].upper(), "confidence": min(100, max(0, int(data.get("confidence", 0)))), "reason": data.get("reason", "")}
    except Exception as e:
        print(f"  [8B ERR] {str(e)[:80]}")
    return {"action": "NO TRADE", "confidence": 0, "reason": "error"}

# =========================================================
# MODEL 3: LLAMA 4 SCOUT 17B
# =========================================================
def analyze_scout(ticker, text):
    try:
        resp = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": PROMPT.format(ticker=ticker, text=text[:2500])}],
            temperature=0.15,
        )
        data = extract_json(resp.choices[0].message.content.strip())
        if data and data.get("action") in ("BUY", "SELL", "NO TRADE"):
            return {"action": data["action"].upper(), "confidence": min(100, max(0, int(data.get("confidence", 0)))), "reason": data.get("reason", "")}
    except Exception as e:
        print(f"  [SCOUT ERR] {str(e)[:80]}")
    return {"action": "NO TRADE", "confidence": 0, "reason": "error"}

# =========================================================
# MODEL 4: QWEN3 32B (strong reasoning)
# =========================================================
def analyze_qwen(ticker, text):
    try:
        resp = groq_client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[{"role": "user", "content": PROMPT.format(ticker=ticker, text=text[:2500])}],
            temperature=0.1,
        )
        data = extract_json(resp.choices[0].message.content.strip())
        if data and data.get("action") in ("BUY", "SELL", "NO TRADE"):
            return {"action": data["action"].upper(), "confidence": min(100, max(0, int(data.get("confidence", 0)))), "reason": data.get("reason", "")}
    except Exception as e:
        print(f"  [QWEN ERR] {str(e)[:80]}")
    return {"action": "NO TRADE", "confidence": 0, "reason": "error"}

# =========================================================
# MODEL 5: GEMINI (optional, auto-disables on quota)
# =========================================================
def analyze_gemini(ticker, text):
    global gemini_disabled
    if gemini_disabled:
        return {"action": "NO TRADE", "confidence": 0, "reason": "quota"}
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=PROMPT.format(ticker=ticker, text=text[:3000]),
            config={"temperature": 0.1, "max_output_tokens": 200},
        )
        data = extract_json(response.text.strip())
        if data and data.get("action") in ("BUY", "SELL", "NO TRADE"):
            return {"action": data["action"].upper(), "confidence": min(100, max(0, int(data.get("confidence", 0)))), "reason": data.get("reason", "")}
    except Exception as e:
        err = str(e).lower()
        if "429" in str(e) or "quota" in err:
            gemini_disabled = True
            print(f"  [GEMINI] Quota exhausted — continuing without Gemini")
        else:
            print(f"  [GEMINI ERR] {str(e)[:80]}")
    return {"action": "NO TRADE", "confidence": 0, "reason": "gemini unavailable"}

# =========================================================
# MODEL 6: FINBERT (NLP sentiment — DEMOTED to tiebreaker)
# =========================================================
def analyze_finbert(text):
    try:
        result = hf_client.text_classification(text[:512], model="ProsusAI/finbert")
        label = result[0]["label"].upper()
        score = float(result[0]["score"])
        if label == "POSITIVE":
            return {"action": "BUY", "confidence": int(score * 100), "reason": f"FinBERT: {label} ({score:.2f})"}
        elif label == "NEGATIVE":
            return {"action": "SELL", "confidence": int(score * 100), "reason": f"FinBERT: {label} ({score:.2f})"}
        else:
            return {"action": "NO TRADE", "confidence": int(score * 50), "reason": f"FinBERT: {label} ({score:.2f})"}
    except Exception as e:
        print(f"  [FINBERT ERR] {e}")
    return {"action": "NO TRADE", "confidence": 0, "reason": "error"}

# =========================================================
# FINBERT SANITY CHECK
# If ALL LLMs disagree with FinBERT, override it to NO TRADE
# =========================================================
def sanitize_finbert(rfb, r70b, r8b, rscout, rqwen):
    """Override FinBERT when it contradicts all LLMs."""
    if rfb["action"] == "NO TRADE":
        return rfb

    llm_results = [r70b, r8b, rscout, rqwen]
    llm_actions = [r["action"] for r in llm_results if r["action"] != "NO TRADE"]

    if not llm_actions:
        return rfb  # All LLMs say NO TRADE — FinBERT might be right

    # If FinBERT says BUY but no LLM says BUY (and some say SELL)
    if rfb["action"] == "BUY" and "BUY" not in llm_actions and "SELL" in llm_actions:
        return {"action": "NO TRADE", "confidence": 0, "reason": "FinBERT overridden (all LLMs disagree)"}

    # If FinBERT says SELL but no LLM says SELL (and some say BUY)
    if rfb["action"] == "SELL" and "SELL" not in llm_actions and "BUY" in llm_actions:
        return {"action": "NO TRADE", "confidence": 0, "reason": "FinBERT overridden (all LLMs disagree)"}

    return rfb

# =========================================================
# CONSENSUS ENGINE v4 — with smart conflict suppression
# =========================================================
def compute_consensus(r70b, r8b, rscout, rqwen, rgemini, rfb):
    global gemini_disabled

    # ── SMART MODEL CONFLICT CHECK ──
    # If 70B and Qwen disagree on direction → unreliable → NO TRADE
    if (r70b["action"] != "NO TRADE" and rqwen["action"] != "NO TRADE"
            and r70b["action"] != rqwen["action"]):
        parts = []
        for name, result in {"70B": r70b, "8B": r8b, "SCT": rscout, "QWN": rqwen}.items():
            tag = {"BUY": "+", "SELL": "-", "NO TRADE": "~"}[result["action"]]
            parts.append(f"{name}={tag}{result['confidence']}")
        return {
            "action": "NO TRADE", "score": 0,
            "reasoning": f"[{' | '.join(parts)}] SUPPRESSED: 70B ({r70b['action']}) vs Qwen ({rqwen['action']}) disagree",
            "buy_votes": 0, "sell_votes": 0, "conflict": True,
            "r70b": r70b, "r8b": r8b, "rscout": rscout, "rqwen": rqwen, "rgemini": rgemini, "rfb": rfb,
        }

    all_models = {"70B": r70b, "8B": r8b, "SCT": rscout, "QWN": rqwen, "GEM": rgemini, "FB": rfb}

    if gemini_disabled:
        weights = {"70B": WF_70B, "8B": WF_8B, "SCT": WF_SCOUT, "QWN": WF_QWEN, "GEM": 0, "FB": WF_FINBERT}
        active = {"70B": r70b, "8B": r8b, "SCT": rscout, "QWN": rqwen, "FB": rfb}
    else:
        weights = {"70B": W_70B, "8B": W_8B, "SCT": W_SCOUT, "QWN": W_QWEN, "GEM": W_GEMINI, "FB": W_FINBERT}
        active = all_models

    buy_votes  = sum(1 for m in active.values() if m["action"] == "BUY")
    sell_votes = sum(1 for m in active.values() if m["action"] == "SELL")

    buy_score = sell_score = 0
    for name, result in all_models.items():
        w = weights.get(name, 0)
        if result["action"] == "BUY":
            buy_score += result["confidence"] * w
        elif result["action"] == "SELL":
            sell_score += result["confidence"] * w

    # Majority bonus (only for strong consensus)
    majority = len(active)
    if buy_votes >= majority:  buy_score += 15
    if sell_votes >= majority: sell_score += 15
    if buy_votes >= majority - 1 and buy_votes >= 3: buy_score += 8
    if sell_votes >= majority - 1 and sell_votes >= 3: sell_score += 8

    # Conflict penalty — STRONGER than v3
    conflict = buy_votes >= 1 and sell_votes >= 1
    if conflict:
        buy_score  *= 0.5   # was 0.6 — harsher penalty
        sell_score *= 0.5

    buy_score  = min(100, int(buy_score))
    sell_score = min(100, int(sell_score))

    action = "NO TRADE"
    final_score = 0

    if buy_score >= sell_score and buy_votes >= MIN_MODELS_AGREE:
        final_score = buy_score
        action = "STRONG BUY" if final_score >= STRONG_THRESHOLD else ("BUY" if final_score >= NORMAL_THRESHOLD else "NO TRADE")
    elif sell_score > buy_score and sell_votes >= MIN_MODELS_AGREE:
        final_score = sell_score
        action = "STRONG SELL" if final_score >= STRONG_THRESHOLD else ("SELL" if final_score >= NORMAL_THRESHOLD else "NO TRADE")

    parts = []
    for name, result in active.items():
        tag = {"BUY": "+", "SELL": "-", "NO TRADE": "~"}[result["action"]]
        parts.append(f"{name}={tag}{result['confidence']}")

    best_reason = next((r["reason"] for r in [r70b, rscout, rqwen, r8b, rgemini, rfb] if r["action"] in ("BUY", "SELL") and r["reason"]), "")

    return {
        "action": action, "score": final_score,
        "reasoning": f"[{' | '.join(parts)}] {best_reason}",
        "buy_votes": buy_votes, "sell_votes": sell_votes, "conflict": conflict,
        "r70b": r70b, "r8b": r8b, "rscout": rscout, "rqwen": rqwen, "rgemini": rgemini, "rfb": rfb,
    }


# =========================================================
# LOAD DATA
# =========================================================
print("=" * 70)
print("  MULTI-AI STOCK SIGNAL ENGINE v4 -- ULTRA-RELIABLE")
print("  70B + 8B + Scout + Qwen3 + Gemini + FinBERT + wordf Cross-Val")
print("=" * 70)

all_rows = []
for sheet_name in INPUT_SHEETS:
    try:
        ws = ss.worksheet(sheet_name)
        raw = ws.get_all_values()
        if sheet_name == "nse":
            for r in sheet_to_records(ws):
                ticker = normalise_ticker(r.get("SYMBOL", ""))
                text = str(r.get("DETAILS", "")).strip()
                if ticker and text and len(text) > 30:
                    all_rows.append({"ticker": ticker, "text": text, "source": "NSE"})
        elif sheet_name == "bse":
            for row in raw[1:]:
                if len(row) < 3: continue
                ticker = normalise_ticker(row[1])
                text = str(row[2]).strip()
                if ticker and text and len(text) > 30:
                    all_rows.append({"ticker": ticker, "text": text, "source": "BSE"})
        print(f"  Loaded {sheet_name.upper()}: {len(raw)-1} rows")
    except Exception as e:
        print(f"  Error {sheet_name}: {e}")

# Deduplicate + filter
seen = set()
unique = []
for r in all_rows:
    key = (r["ticker"], r["text"][:100])
    if key not in seen:
        seen.add(key)
        unique.append(r)

filtered = [r for r in unique if not is_noise(r["text"])]

# ── v4: Pre-filter with wordf compliance/quality checks ──
if WORDF_AVAILABLE:
    pre_filtered = []
    skipped_quality = 0
    skipped_compliance = 0
    for r in filtered:
        quality = text_quality_score(r["text"])
        if quality == 0:
            skipped_quality += 1
            continue
        if is_compliance_filing(r["text"]):
            skipped_compliance += 1
            continue
        pre_filtered.append(r)
    print(f"  Pre-filtered: {skipped_quality} quality | {skipped_compliance} compliance")
    filtered = pre_filtered

print(f"\n  Total: {len(all_rows)} | Unique: {len(unique)} | Filtered: {len(filtered)}")

# ── Load historical signal data for price validation ──
ws_history_data = []
try:
    ws_hist = ss.worksheet("wordf")
    ws_history_data = ws_hist.get_all_values()[1:]
    print(f"  Historical: {len(ws_history_data)} past wordf signals loaded")
except Exception:
    print("  Historical: no wordf data found")

print("-" * 70)

# =========================================================
# ANALYSIS LOOP (v4 — with cross-validation)
# =========================================================
results = []
skipped = 0
suppressed_direction = 0
wordf_boosted = 0
hist_applied = 0

for i, r in enumerate(filtered):
    ticker, text, source = r["ticker"], r["text"], r["source"]
    print(f"\n[{i+1}/{len(filtered)}] {ticker} ({source})")

    # ── AI ANALYSIS (6 models) ──
    r70b    = analyze_groq_70b(ticker, text)
    r8b     = analyze_groq_8b(ticker, text)
    rscout  = analyze_scout(ticker, text)
    rqwen   = analyze_qwen(ticker, text)
    rgemini = analyze_gemini(ticker, text)
    rfb_raw = analyze_finbert(text)

    # ── FINBERT SANITY CHECK ──
    rfb = sanitize_finbert(rfb_raw, r70b, r8b, rscout, rqwen)
    if rfb["action"] != rfb_raw["action"]:
        print(f"  [FB OVERRIDE] {rfb_raw['action']} -> NO TRADE (disagrees with all LLMs)")

    mode = "5-MODEL" if gemini_disabled else "6-MODEL"
    print(f"  70B={r70b['action'][:1]}{r70b['confidence']} | 8B={r8b['action'][:1]}{r8b['confidence']} | SCT={rscout['action'][:1]}{rscout['confidence']} | QWN={rqwen['action'][:1]}{rqwen['confidence']} | GEM={rgemini['action'][:1]}{rgemini['confidence']} | FB={rfb['action'][:1]}{rfb['confidence']} [{mode}]")

    # ── CONSENSUS ──
    consensus = compute_consensus(r70b, r8b, rscout, rqwen, rgemini, rfb)

    if consensus["action"] == "NO TRADE":
        skipped += 1
        if consensus.get("conflict"):
            print(f"  >>> SUPPRESSED: smart model conflict")
        continue

    # ── WORDF CROSS-VALIDATION (THE BIG FIX for wrong direction) ──
    if WORDF_AVAILABLE:
        wordf_buy, wordf_sell, wordf_reasons = wordf_event_score(text)

        # DIRECTION CONFLICT: AI says BUY but wordf detects SELL keywords
        if "BUY" in consensus["action"] and wordf_sell <= -6:
            print(f"  >>> DIRECTION FIX: AI={consensus['action']} but wordf found SELL keywords: {[r for r in wordf_reasons if 'SELL' in r][:3]}")
            suppressed_direction += 1
            skipped += 1
            continue

        # DIRECTION CONFLICT: AI says SELL but wordf detects strong BUY keywords
        if "SELL" in consensus["action"] and wordf_buy >= 6:
            print(f"  >>> DIRECTION FIX: AI={consensus['action']} but wordf found BUY keywords: {[r for r in wordf_reasons if 'BUY' in r][:3]}")
            suppressed_direction += 1
            skipped += 1
            continue

        # CONFIDENCE BOOST: AI + wordf AGREE on direction
        if "BUY" in consensus["action"] and wordf_buy >= 6:
            consensus["score"] = min(100, consensus["score"] + 10)
            consensus["reasoning"] += " [wordf CONFIRMS BUY]"
            wordf_boosted += 1
        elif "SELL" in consensus["action"] and wordf_sell <= -6:
            consensus["score"] = min(100, consensus["score"] + 10)
            consensus["reasoning"] += " [wordf CONFIRMS SELL]"
            wordf_boosted += 1

        # ── HISTORICAL PRICE VALIDATION ──
        if wordf_reasons:
            hist_boost, hist_analysis = analyze_historical_patterns(ticker, wordf_reasons, ws_history_data)
            if hist_boost != 0:
                consensus["score"] = max(0, min(100, consensus["score"] + hist_boost))
                consensus["reasoning"] += f" [{hist_analysis}]"
                hist_applied += 1

    # ── MARKET INTELLIGENCE ENRICHMENT ──
    if MARKET_UTILS_OK:
        # Freshness check
        is_stale, today_change = check_freshness(ticker)
        if is_stale:
            if "BUY" in consensus["action"] and today_change > 3.0:
                print(f"  >>> STALE: already +{today_change:.1f}% today -- suppressed")
                skipped += 1
                continue
            elif "SELL" in consensus["action"] and today_change < -3.0:
                print(f"  >>> STALE: already {today_change:.1f}% today -- suppressed")
                skipped += 1
                continue

        # Full enrichment
        enrichment = enrich_signal(
            ticker, consensus["action"],
            wordf_reasons if WORDF_AVAILABLE else [],
            sheet_history=ws_history_data
        )
        adj = enrichment["total_adjustment"]
        if adj != 0:
            consensus["score"] = max(0, min(100, consensus["score"] + adj))
            for detail in enrichment["details"]:
                consensus["reasoning"] += f" [{detail}]"

    # ── RE-CHECK THRESHOLD after adjustments ──
    if consensus["score"] < NORMAL_THRESHOLD:
        skipped += 1
        continue

    # Re-classify after score adjustments
    if "BUY" in consensus["action"]:
        consensus["action"] = "STRONG BUY" if consensus["score"] >= STRONG_THRESHOLD else "BUY"
    elif "SELL" in consensus["action"]:
        consensus["action"] = "STRONG SELL" if consensus["score"] >= STRONG_THRESHOLD else "SELL"

    tag = {"STRONG BUY": "++", "BUY": "+", "STRONG SELL": "--", "SELL": "-"}.get(consensus["action"], "")
    print(f"  >>> {tag} {consensus['action']} | Score: {consensus['score']}")

    results.append({
        "ticker": ticker, "source": source,
        "action": consensus["action"], "score": consensus["score"],
        "r70b_action": r70b["action"], "r70b_conf": r70b["confidence"],
        "r8b_action": r8b["action"], "r8b_conf": r8b["confidence"],
        "rscout_action": rscout["action"], "rscout_conf": rscout["confidence"],
        "rqwen_action": rqwen["action"], "rqwen_conf": rqwen["confidence"],
        "rgemini_action": rgemini["action"], "rgemini_conf": rgemini["confidence"],
        "rfb_action": rfb["action"], "rfb_conf": rfb["confidence"],
        "reasoning": consensus["reasoning"],
        "buy_votes": consensus["buy_votes"], "sell_votes": consensus["sell_votes"],
        "conflict": consensus["conflict"],
    })

    time.sleep(REQUEST_DELAY)

# =========================================================
# FINAL RESULTS
# =========================================================
results.sort(key=lambda x: x["score"], reverse=True)

print("\n" + "=" * 90)
print("  FINAL SIGNALS (v4 — Ultra-Reliable)")
print("=" * 90)

if not results:
    print("  No actionable signals — this is EXPECTED with strict v4 filtering.")
    print(f"  {len(filtered)} analyzed | {skipped} filtered | {suppressed_direction} direction-fixed")
else:
    print(f"  {'STOCK':<18} {'ACTION':<13} {'SCORE':>5}  {'70B':>5} {'8B':>5} {'SCT':>5} {'QWN':>5} {'GEM':>5} {'FB':>5}")
    print(f"  {'-'*85}")
    for r in results:
        a = f"{r['r70b_action'][:1]}{r['r70b_conf']}"
        b = f"{r['r8b_action'][:1]}{r['r8b_conf']}"
        c = f"{r['rscout_action'][:1]}{r['rscout_conf']}"
        c2 = f"{r['rqwen_action'][:1]}{r['rqwen_conf']}"
        d = f"{r['rgemini_action'][:1]}{r['rgemini_conf']}"
        e = f"{r['rfb_action'][:1]}{r['rfb_conf']}"
        print(f"  {r['ticker']:<18} {r['action']:<13} {r['score']:>5}  {a:>5} {b:>5} {c:>5} {c2:>5} {d:>5} {e:>5}")

    buys  = sum(1 for r in results if "BUY" in r["action"])
    sells = sum(1 for r in results if "SELL" in r["action"])
    print(f"\n  BUY: {buys} | SELL: {sells} | Skipped: {skipped}")
    if suppressed_direction:
        print(f"  Direction-fixed: {suppressed_direction} (wordf caught wrong BUY/SELL)")
    if wordf_boosted:
        print(f"  wordf-confirmed: {wordf_boosted}")
    if hist_applied:
        print(f"  Historical validation: {hist_applied}")

print("=" * 90)

# =========================================================
# WRITE TO SHEET — PREMIUM DASHBOARD + SIGNAL HISTORY
# =========================================================

# --- Color Palette ---
C_BG       = {"red": 0.08, "green": 0.08, "blue": 0.11}   # main dark bg
C_TITLE    = {"red": 0.06, "green": 0.50, "blue": 0.42}   # teal title bar
C_SUB_BG   = {"red": 0.10, "green": 0.10, "blue": 0.16}   # subtitle bar
C_CARD     = {"red": 0.11, "green": 0.13, "blue": 0.21}   # stat card bg
C_CARD_LBL = {"red": 0.09, "green": 0.10, "blue": 0.17}   # stat label bg
C_HDR      = {"red": 0.07, "green": 0.09, "blue": 0.18}   # column header bg
C_DIVIDER  = {"red": 0.10, "green": 0.08, "blue": 0.18}   # history divider
C_WHITE    = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
C_LGRAY    = {"red": 0.75, "green": 0.78, "blue": 0.84}
C_DGRAY    = {"red": 0.45, "green": 0.47, "blue": 0.52}
C_GOLD     = {"red": 1.0,  "green": 0.84, "blue": 0.0}
C_CYAN     = {"red": 0.40, "green": 0.88, "blue": 0.98}
C_GREEN    = {"red": 0.30, "green": 1.0,  "blue": 0.50}
C_RED      = {"red": 1.0,  "green": 0.35, "blue": 0.35}
C_AMBER    = {"red": 1.0,  "green": 0.75, "blue": 0.20}
C_GRN_BG   = {"red": 0.04, "green": 0.22, "blue": 0.12}
C_RED_BG   = {"red": 0.28, "green": 0.04, "blue": 0.04}
C_SBUY_BG  = {"red": 0.0,  "green": 0.35, "blue": 0.18}
C_SSELL_BG = {"red": 0.42, "green": 0.0,  "blue": 0.0}

FULL = "A{r}:Z{r}"    # full width for bg painting

def fmt(bg=None, fg=None, bold=False, size=10, font="Inter"):
    f = {"textFormat": {"fontFamily": font, "fontSize": size, "bold": bold}}
    if fg: f["textFormat"]["foregroundColor"] = fg
    if bg: f["backgroundColor"] = bg
    return f

pending_formats = []

def qfmt(rng, style):
    pending_formats.append({"range": rng, "format": style})

def ctr(rng):
    qfmt(rng, {"horizontalAlignment": "CENTER"})

def lft(rng):
    qfmt(rng, {"horizontalAlignment": "LEFT"})

def flush_formats(ws):
    if pending_formats:
        with_sheets_backoff(lambda: ws.batch_format(pending_formats))
        pending_formats.clear()

out = open_or_create(OUTPUT_WS)
sid = out.id

ist_now   = datetime.now(ZoneInfo("Asia/Kolkata"))
ist_date  = ist_now.strftime("%d %b %Y")
ist_time  = ist_now.strftime("%H:%M:%S")
ist_full  = ist_now.strftime("%Y-%m-%d %H:%M:%S IST")

# ── Extract old signal rows (only actual BUY/SELL) ──
existing = out.get_all_values()
old_signals = []
for row in existing:
    if len(row) >= 7:
        a = str(row[4]).strip().upper()
        if a in ("BUY", "SELL", "STRONG BUY", "STRONG SELL"):
            old_signals.append(row[:7])

# ── Unmerge everything + clear ──
with_sheets_backoff(lambda: out.clear())

batch_requests = [{"unmergeCells": {
    "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 2000,
              "startColumnIndex": 0, "endColumnIndex": 26}
}}]

# ── Paint entire visible area dark ──
batch_requests.append({"repeatCell": {
    "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 200,
              "startColumnIndex": 0, "endColumnIndex": 26},
    "cell": {"userEnteredFormat": {
        "backgroundColor": C_BG,
        "textFormat": {"foregroundColor": C_LGRAY, "fontFamily": "Inter", "fontSize": 10}
    }},
    "fields": "userEnteredFormat(backgroundColor,textFormat)"
}})

# ── Column widths (A-G data + H-Z narrow filler) ──
data_widths = [115, 85, 180, 75, 120, 65, 550]
for i, w in enumerate(data_widths):
    batch_requests.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
        "properties": {"pixelSize": w}, "fields": "pixelSize"
    }})
# Shrink columns H-Z so they don't show white
batch_requests.append({"updateDimensionProperties": {
    "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 7, "endIndex": 26},
    "properties": {"pixelSize": 20}, "fields": "pixelSize"
}})

# ── Current run stats ──
buys   = sum(1 for r in results if "BUY" in r["action"])
sells  = sum(1 for r in results if "SELL" in r["action"])
s_buys = sum(1 for r in results if r["action"] == "STRONG BUY")
s_sells= sum(1 for r in results if r["action"] == "STRONG SELL")
avg_sc = int(sum(r["score"] for r in results) / len(results)) if results else 0
top_pk = results[0]["ticker"] if results else "---"
total_hist = len(old_signals) + len([r for r in results if "BUY" in r["action"] or "SELL" in r["action"]])

if buys > sells:     sentiment, s_col = "BULLISH", C_GREEN
elif sells > buys:   sentiment, s_col = "BEARISH", C_RED
else:                sentiment, s_col = "NEUTRAL", C_AMBER

mode_str = "5-MODEL" if gemini_disabled else "6-MODEL"

# =====================================================
# ROW 10+: SIGNAL HISTORY (accumulates over runs)
# =====================================================

new_signals = []
for r in results:
    reason = r["reasoning"]
    if "]" in reason:
        reason = reason.split("]", 1)[-1].strip()
    new_signals.append([
        ist_date, ist_time, r["ticker"], r["source"],
        r["action"], r["score"], reason[:400],
    ])

# Newest on top
all_signals = new_signals + old_signals

# =====================================================
# ROWS 1-9: LIVE DASHBOARD (refreshed each run)
# =====================================================

# Merge title, run info, and history divider
batch_requests.append({"mergeCells": {
    "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
              "startColumnIndex": 0, "endColumnIndex": 7},
    "mergeType": "MERGE_ALL"
}})
batch_requests.append({"mergeCells": {
    "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2,
              "startColumnIndex": 0, "endColumnIndex": 7},
    "mergeType": "MERGE_ALL"
}})
batch_requests.append({"mergeCells": {
    "range": {"sheetId": sid, "startRowIndex": 7, "endRowIndex": 8,
              "startColumnIndex": 0, "endColumnIndex": 7},
    "mergeType": "MERGE_ALL"
}})

# Freeze top 9 rows
batch_requests.append({"updateSheetProperties": {
    "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 9}},
    "fields": "gridProperties.frozenRowCount"
}})

with_sheets_backoff(lambda: ss.batch_update({"requests": batch_requests}))

run_info = f"{ist_date}  |  {ist_time} IST  |  v4 {mode_str}  |  Scanned: {len(filtered)}  |  Skipped: {skipped}  |  Dir-Fixed: {suppressed_direction}"
dashboard_updates = [
    {"range": "A1", "values": [["  MULTI-AI v4 ULTRA-RELIABLE"]]},
    {"range": "A2", "values": [[run_info]]},
    {"range": "A3", "values": [[""]]},
    {"range": "A4:G4", "values": [["  BUY SIGNALS", str(buys), "", "  SELL SIGNALS", str(sells), "", f"  {sentiment}"]]},
    {"range": "A5:G5", "values": [["  AVG SCORE", f"{avg_sc}%", "", "  TOP PICK", top_pk, "", f"  {total_hist} total signals"]]},
    {"range": "A6:G6", "values": [["  STRONG BUY", str(s_buys), "", "  STRONG SELL", str(s_sells), "", ""]]},
    {"range": "A7", "values": [[""]]},
    {"range": "A8", "values": [["SIGNAL HISTORY"]]},
    {"range": "A9:G9", "values": [["DATE", "TIME", "TICKER", "SOURCE", "ACTION", "SCORE", "REASON"]]},
]

if all_signals:
    dashboard_updates.append({"range": "A10", "values": all_signals})
else:
    dashboard_updates.append({"range": "A10:G10", "values": [[
        ist_date, ist_time, "---", "---", "NO SIGNALS", "0",
        f"{len(filtered)} analyzed with v4 strict filtering"
    ]]})

with_sheets_backoff(lambda: out.batch_update(dashboard_updates))

qfmt("A1:G1", fmt(bg=C_TITLE, fg=C_WHITE, bold=True, size=14))
ctr("A1:G1")

qfmt("A2:G2", fmt(bg=C_SUB_BG, fg=C_DGRAY, size=9))
ctr("A2:G2")

qfmt("A4", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("B4", fmt(bg=C_CARD, fg=C_GREEN, bold=True, size=16))
qfmt("C4", fmt(bg=C_BG, size=4))
qfmt("D4", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("E4", fmt(bg=C_CARD, fg=C_RED, bold=True, size=16))
qfmt("F4", fmt(bg=C_BG, size=4))
qfmt("G4", fmt(bg=C_CARD, fg=s_col, bold=True, size=14))
ctr("A4:G4")

qfmt("A5", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("B5", fmt(bg=C_CARD, fg=C_GOLD, bold=True, size=16))
qfmt("C5", fmt(bg=C_BG, size=4))
qfmt("D5", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("E5", fmt(bg=C_CARD, fg=C_WHITE, bold=True, size=12))
qfmt("F5", fmt(bg=C_BG, size=4))
qfmt("G5", fmt(bg=C_CARD, fg=C_DGRAY, size=9))
ctr("A5:G5")

qfmt("A6", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("B6", fmt(bg=C_CARD, fg=C_GREEN, bold=True, size=16))
qfmt("C6", fmt(bg=C_BG, size=4))
qfmt("D6", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("E6", fmt(bg=C_CARD, fg=C_RED, bold=True, size=16))
qfmt("F6:G6", fmt(bg=C_BG, size=4))
ctr("A6:G6")

qfmt("A8:G8", fmt(bg=C_DIVIDER, fg=C_GOLD, bold=True, size=11))
ctr("A8:G8")

qfmt("A9:G9", fmt(bg=C_HDR, fg=C_GOLD, bold=True, size=10))
ctr("A9:G9")

if all_signals:
    for idx, sig in enumerate(all_signals):
        rn = 10 + idx
        rng = f"A{rn}:G{rn}"
        action = str(sig[4]).strip().upper()

        if action == "STRONG BUY":
            qfmt(rng, fmt(bg=C_SBUY_BG, fg=C_GREEN, bold=True, size=10))
        elif action == "BUY":
            qfmt(rng, fmt(bg=C_GRN_BG, fg=C_GREEN, size=10))
        elif action == "STRONG SELL":
            qfmt(rng, fmt(bg=C_SSELL_BG, fg=C_RED, bold=True, size=10))
        elif action == "SELL":
            qfmt(rng, fmt(bg=C_RED_BG, fg=C_RED, size=10))
        else:
            qfmt(rng, fmt(bg=C_BG, fg=C_LGRAY, size=10))

        qfmt(f"E{rn}", {"textFormat": {"bold": True, "fontSize": 11}})
        qfmt(f"F{rn}", {"textFormat": {"bold": True, "fontSize": 11}})
        ctr(f"A{rn}:F{rn}")
        lft(f"G{rn}")
else:
    qfmt("A10:G10", fmt(bg=C_BG, fg=C_LGRAY, size=10))
    ctr("A10:F10")
    lft("G10")

flush_formats(out)

# ── Telegram Alerts ──
alerts_sent = 0
if ALERTS_OK and results:
    for r in results:
        if alert_from_multi_ai(r):
            alerts_sent += 1
    if alerts_sent:
        top_picks = [r["ticker"] for r in results[:5]]
        send_summary(buys, sells, top_picks, source="multi_ai v4")

# ── Console summary ──
print(f"\n{'='*70}")
if results:
    print(f"  SIGNALS: {len(results)} added to '{OUTPUT_WS}'")
    print(f"  BUY: {buys} | SELL: {sells} | Skipped: {skipped}")
else:
    print(f"  No actionable signals (v4 strict filtering)")
    print(f"  {len(filtered)} analyzed | {skipped} skipped")
if suppressed_direction:
    print(f"  DIRECTION FIXES: {suppressed_direction} (wordf caught wrong BUY/SELL)")
if wordf_boosted:
    print(f"  WORDF CONFIRMED: {wordf_boosted} signals")
if hist_applied:
    print(f"  HISTORICAL VALIDATION: {hist_applied} signals")
if MARKET_UTILS_OK:
    print(f"  MARKET ENRICHMENT: active")
if alerts_sent:
    print(f"  TELEGRAM ALERTS: {alerts_sent} sent")
print(f"  HISTORY: {len(all_signals)} total signals")
print(f"  TIME: {ist_full}")
print(f"{'='*70}")
print("DONE")
