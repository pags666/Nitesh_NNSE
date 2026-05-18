"""
MULTI-AI STOCK SIGNAL ENGINE v3 — 5 MODEL EDITION
===================================================
Combines 6 AI models for high-accuracy consensus trading signals:
  1. Groq Llama 3.3 70B    - PRIMARY (smartest, best reasoning)
  2. Groq Llama 3.1 8B     - SECONDARY (fast cross-check)
  3. Groq Llama 4 Scout    - TERTIARY (newest Llama 4)
  4. Groq Qwen3 32B        - QUATERNARY (strong reasoning)
  5. HuggingFace FinBERT   - NLP financial sentiment
  6. Google Gemini          - OPTIONAL bonus (auto-disables on quota)

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

# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# =========================================================
# CONFIG
# =========================================================
SHEET_ID     = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"
INPUT_SHEETS = ["nse", "bse"]
OUTPUT_WS    = "multi_ai"

# 6-model weights (with Gemini)
W_70B     = 0.25
W_8B      = 0.10
W_SCOUT   = 0.15
W_QWEN    = 0.15
W_FINBERT = 0.15
W_GEMINI  = 0.20

# 5-model weights (no Gemini — most common)
WF_70B     = 0.30
WF_8B      = 0.15
WF_SCOUT   = 0.20
WF_QWEN    = 0.20
WF_FINBERT = 0.15

# Thresholds (tuned for 5-model mode)
STRONG_THRESHOLD = 75
NORMAL_THRESHOLD = 50
MIN_MODELS_AGREE = 2

# Rate limiting
REQUEST_DELAY = 2.5  # seconds between announcements

# =========================================================
# GLOBAL STATE
# =========================================================
gemini_disabled = False  # auto-set to True on daily quota exhaust

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
# NOISE FILTER
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
]

def is_noise(text):
    t = text.lower()
    return any(kw in t for kw in IGNORE_KEYWORDS)

# =========================================================
# ANALYSIS PROMPT
# =========================================================
PROMPT = """You are an elite Indian stock market analyst (NSE/BSE).
Analyze this corporate announcement - will it MOVE the stock price in 1-2 days?

PRICE-MOVING EVENTS (BUY or SELL):
- Order wins / large contracts / LOA / work orders worth significant amount
- Strong earnings surprise / record profit / revenue surge
- Buyback / bonus / stock split / rights issue
- Acquisition / merger / strategic partnership with clear financial impact
- Promoter buying significant stake
- SEBI action / fraud / auditor resignation / insolvency / NCLT / default
- Major capacity expansion / new plant

IGNORE (return NO TRADE):
- Compliance filings, board meetings, AGM, voting results
- Appointments, newspaper ads, investor presentations
- Routine disclosures without financial impact

CONFIDENCE: 90-100=very strong, 75-89=strong, 60-74=moderate, <60=NO TRADE

COMPANY: {ticker}
ANNOUNCEMENT: {text}

Return ONLY valid JSON:
{{"action": "BUY"|"SELL"|"NO TRADE", "confidence": <0-100>, "reason": "<1 line>"}}"""

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
# MODEL 3: LLAMA 4 SCOUT 17B (newest Llama 4 via Groq)
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
# MODEL 4: QWEN3 32B (strong reasoning via Groq)
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
# MODEL 4: GEMINI (optional bonus, auto-disables on quota)
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
            print(f"  [GEMINI] Quota exhausted — continuing with 4 models")
        else:
            print(f"  [GEMINI ERR] {str(e)[:80]}")
    return {"action": "NO TRADE", "confidence": 0, "reason": "gemini unavailable"}

# =========================================================
# MODEL 6: FINBERT (NLP sentiment)
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
# CONSENSUS ENGINE — 5 MODEL
# =========================================================
def compute_consensus(r70b, r8b, rscout, rqwen, rgemini, rfb):
    global gemini_disabled

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

    # Majority bonus
    majority = len(active)
    if buy_votes >= majority:  buy_score += 15
    if sell_votes >= majority: sell_score += 15
    if buy_votes >= majority - 1 and buy_votes >= 3: buy_score += 8
    if sell_votes >= majority - 1 and sell_votes >= 3: sell_score += 8

    conflict = buy_votes >= 1 and sell_votes >= 1
    if conflict:
        buy_score  *= 0.6
        sell_score *= 0.6

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
print("=" * 60)
print("  MULTI-AI STOCK SIGNAL ENGINE v3 -- 6 MODEL")
print("  70B + 8B + Llama4Scout + Qwen3-32B + Gemini + FinBERT")
print("=" * 60)

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
print(f"\n  Total: {len(all_rows)} | Unique: {len(unique)} | Filtered: {len(filtered)}")
print("-" * 60)

# =========================================================
# ANALYSIS LOOP
# =========================================================
results = []
skipped = 0

for i, r in enumerate(filtered):
    ticker, text, source = r["ticker"], r["text"], r["source"]
    print(f"\n[{i+1}/{len(filtered)}] {ticker} ({source})")

    r70b    = analyze_groq_70b(ticker, text)
    r8b     = analyze_groq_8b(ticker, text)
    rscout  = analyze_scout(ticker, text)
    rqwen   = analyze_qwen(ticker, text)
    rgemini = analyze_gemini(ticker, text)
    rfb     = analyze_finbert(text)

    mode = "5-MODEL" if gemini_disabled else "6-MODEL"
    print(f"  70B={r70b['action'][:1]}{r70b['confidence']} | 8B={r8b['action'][:1]}{r8b['confidence']} | SCT={rscout['action'][:1]}{rscout['confidence']} | QWN={rqwen['action'][:1]}{rqwen['confidence']} | GEM={rgemini['action'][:1]}{rgemini['confidence']} | FB={rfb['action'][:1]}{rfb['confidence']} [{mode}]")

    consensus = compute_consensus(r70b, r8b, rscout, rqwen, rgemini, rfb)

    if consensus["action"] == "NO TRADE":
        skipped += 1
    else:
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
print("  FINAL SIGNALS")
print("=" * 90)

if not results:
    print("  No actionable signals today.")
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
try:
    ss.batch_update({"requests": [{"unmergeCells": {
        "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 2000,
                  "startColumnIndex": 0, "endColumnIndex": 26}
    }}]})
except: pass
out.clear()

# ── Paint entire visible area dark ──
try:
    ss.batch_update({"requests": [{
        "repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 200,
                      "startColumnIndex": 0, "endColumnIndex": 26},
            "cell": {"userEnteredFormat": {
                "backgroundColor": C_BG,
                "textFormat": {"foregroundColor": C_LGRAY, "fontFamily": "Inter", "fontSize": 10}
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }
    }]})
except: pass

# ── Column widths (A-G data + H-Z narrow filler) ──
try:
    data_widths = [115, 85, 180, 75, 120, 65, 550]
    reqs = []
    for i, w in enumerate(data_widths):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i+1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"
        }})
    # Shrink columns H-Z so they don't show white
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 7, "endIndex": 26},
        "properties": {"pixelSize": 20}, "fields": "pixelSize"
    }})
    ss.batch_update({"requests": reqs})
except: pass

# ── Current run stats ──
buys   = sum(1 for r in results if "BUY" in r["action"])
sells  = sum(1 for r in results if "SELL" in r["action"])
s_buys = sum(1 for r in results if r["action"] == "STRONG BUY")
s_sells= sum(1 for r in results if r["action"] == "STRONG SELL")
avg_sc = int(sum(r["score"] for r in results) / len(results)) if results else 0
top_pk = results[0]["ticker"] if results else "—"
total_hist = len(old_signals) + len([r for r in results if "BUY" in r["action"] or "SELL" in r["action"]])

if buys > sells:     sentiment, s_col = "BULLISH ▲", C_GREEN
elif sells > buys:   sentiment, s_col = "BEARISH ▼", C_RED
else:                sentiment, s_col = "NEUTRAL ●", C_AMBER

mode_str = "5-MODEL" if gemini_disabled else "6-MODEL"

# =====================================================
# ROWS 1-9: LIVE DASHBOARD (refreshed each run)
# =====================================================

# Row 1: Title bar
with_sheets_backoff(lambda: out.update(values=[["  MULTI-AI SIGNAL ENGINE"]], range_name="A1"))
out.merge_cells("A1:G1", merge_type="MERGE_ALL")
qfmt("A1:G1", fmt(bg=C_TITLE, fg=C_WHITE, bold=True, size=14))
ctr("A1:G1")

# Row 2: Timestamp + run info
run_info = f"{ist_date}  ·  {ist_time} IST  ·  {mode_str}  ·  Scanned: {len(filtered)}  ·  Skipped: {skipped}"
with_sheets_backoff(lambda: out.update(values=[[run_info]], range_name="A2"))
out.merge_cells("A2:G2", merge_type="MERGE_ALL")
qfmt("A2:G2", fmt(bg=C_SUB_BG, fg=C_DGRAY, size=9))
ctr("A2:G2")

# Row 3: Spacer (tiny)
with_sheets_backoff(lambda: out.update(values=[[""]], range_name="A3"))

# Row 4: Stats row 1 — BUY / SELL / Sentiment
with_sheets_backoff(lambda: out.update(values=[["  BUY SIGNALS", str(buys), "", "  SELL SIGNALS", str(sells), "", f"  {sentiment}"]], range_name="A4:G4"))
qfmt("A4", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("B4", fmt(bg=C_CARD, fg=C_GREEN, bold=True, size=16))
qfmt("C4", fmt(bg=C_BG, size=4))
qfmt("D4", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("E4", fmt(bg=C_CARD, fg=C_RED, bold=True, size=16))
qfmt("F4", fmt(bg=C_BG, size=4))
qfmt("G4", fmt(bg=C_CARD, fg=s_col, bold=True, size=14))
ctr("A4:G4")

# Row 5: Stats row 2 — Avg Score / Top Pick / History count
with_sheets_backoff(lambda: out.update(values=[["  AVG SCORE", f"{avg_sc}%", "", "  TOP PICK", top_pk, "", f"  {total_hist} total signals"]], range_name="A5:G5"))
qfmt("A5", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("B5", fmt(bg=C_CARD, fg=C_GOLD, bold=True, size=16))
qfmt("C5", fmt(bg=C_BG, size=4))
qfmt("D5", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("E5", fmt(bg=C_CARD, fg=C_WHITE, bold=True, size=12))
qfmt("F5", fmt(bg=C_BG, size=4))
qfmt("G5", fmt(bg=C_CARD, fg=C_DGRAY, size=9))
ctr("A5:G5")

# Row 6: Strong signals detail
with_sheets_backoff(lambda: out.update(values=[["  STRONG BUY", str(s_buys), "", "  STRONG SELL", str(s_sells), "", ""]], range_name="A6:G6"))
qfmt("A6", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("B6", fmt(bg=C_CARD, fg=C_GREEN, bold=True, size=16))
qfmt("C6", fmt(bg=C_BG, size=4))
qfmt("D6", fmt(bg=C_CARD_LBL, fg=C_CYAN, bold=True, size=9))
qfmt("E6", fmt(bg=C_CARD, fg=C_RED, bold=True, size=16))
qfmt("F6:G6", fmt(bg=C_BG, size=4))
ctr("A6:G6")

# Row 7: Spacer
with_sheets_backoff(lambda: out.update(values=[[""]], range_name="A7"))

# Row 8: History section divider
with_sheets_backoff(lambda: out.update(values=[["SIGNAL HISTORY"]], range_name="A8"))
out.merge_cells("A8:G8", merge_type="MERGE_ALL")
qfmt("A8:G8", fmt(bg=C_DIVIDER, fg=C_GOLD, bold=True, size=11))
ctr("A8:G8")

# Row 9: Column headers
with_sheets_backoff(lambda: out.update(values=[["DATE", "TIME", "TICKER", "SOURCE", "ACTION", "SCORE", "REASON"]], range_name="A9:G9"))
qfmt("A9:G9", fmt(bg=C_HDR, fg=C_GOLD, bold=True, size=10))
ctr("A9:G9")

# Freeze top 9 rows
try:
    ss.batch_update({"requests": [{"updateSheetProperties": {
        "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 9}},
        "fields": "gridProperties.frozenRowCount"
    }}]})
except: pass

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

if all_signals:
    with_sheets_backoff(lambda: out.append_rows(all_signals))

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
    with_sheets_backoff(lambda: out.update(values=[[ist_date, ist_time, "—", "—", "NO SIGNALS", "0",
        f"{len(filtered)} analyzed — no actionable triggers found"]], range_name="A10:G10"))
    qfmt("A10:G10", fmt(bg=C_BG, fg=C_LGRAY, size=10))
    ctr("A10:F10")
    lft("G10")

flush_formats(out)

# ── Console ──
print(f"\n{'='*60}")
if results:
    print(f"  ✅ {len(results)} signals added to '{OUTPUT_WS}'")
    print(f"  📊 {buys} BUY | {sells} SELL | {skipped} skipped")
else:
    print(f"  ℹ️  No actionable signals this run")
    print(f"  📊 {len(filtered)} analyzed | {skipped} skipped")
print(f"  📜 {len(all_signals)} total signals in history")
print(f"  🕐 {ist_full}")
print(f"{'='*60}")
print("DONE")
