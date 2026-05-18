import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from groq import Groq
from oauth2client.service_account import ServiceAccountCredentials
from huggingface_hub import InferenceClient

# =========================
# CONFIG
# =========================
SHEET_ID     = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"
OUTPUT_SHEET = "consolidated"

# =========================
# GOOGLE SHEETS AUTH
# =========================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gc    = gspread.authorize(creds)
ss    = gc.open_by_key(SHEET_ID)

# =========================
# HELPERS
# =========================
def sheet_to_records(ws):
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    headers = [h.strip().upper() for h in rows[0]]
    return [dict(zip(headers, r)) for r in rows[1:] if any(r)]

def normalise_ticker(x):
    return str(x).strip().upper()

def open_or_create(title):
    try:
        return ss.worksheet(title)
    except:
        return ss.add_worksheet(title=title, rows="200", cols="10")

# =========================
# CLIENTS
# =========================
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

hf_client = InferenceClient(
    provider="auto",
    api_key=os.environ.get("HF_TOKEN")
)

def finbert_sentiment(text):
    try:
        result = hf_client.text_classification(
            text,
            model="ProsusAI/finbert"
        )
        return result[0]["label"].upper(), float(result[0]["score"])
    except:
        return "NEUTRAL", 0.5

# =========================
# READ NSE + BSE
# =========================
nse_rows = sheet_to_records(ss.worksheet("nse"))
bse_raw  = ss.worksheet("bse").get_all_values()

all_rows = []   # ✅ define FIRST

# ---------- BSE (index-based) ----------
for row in bse_raw[1:]:

    if len(row) < 3:
        continue

    ticker = normalise_ticker(row[1])   # Column B
    text   = str(row[2])                # Column C

    if ticker and text:
        all_rows.append({
            "ticker": ticker,
            "text": text
        })

# ---------- NSE ----------
for r in nse_rows:
    ticker = normalise_ticker(r.get("SYMBOL", ""))
    text   = str(r.get("DETAILS", ""))

    if ticker and text:
        all_rows.append({
            "ticker": ticker,
            "text": text
        })

# ✅ correct print
print(f"✅ NSE: {len(nse_rows)} | BSE: {len(bse_raw)-1}")
print(f"✅ Total rows: {len(all_rows)}")
# =========================
# MAIN LOGIC
# =========================
final_results = []

for r in all_rows:

    ticker = r["ticker"]
    text   = r["text"]

    if len(text) < 30:
        continue

    if "compliance" in text.lower():
        continue

    sentiment, conf = finbert_sentiment(text)

    if sentiment == "NEUTRAL" and conf < 0.3:
        continue

    try:
        prompt = f"""
You are a professional stock analyst specializing in Indian markets (NSE/BSE).

Your task: analyze the announcement and determine if it is likely to move stock price in the short term (1–2 days).

STRICT RULES:

1. ONLY consider PRICE-MOVING EVENTS:
   - Order wins / contracts / MoUs
   - Strong earnings / weak earnings
   - Fundraising / stake sale / acquisition
   - Promoter buying or selling
   - Regulatory penalties / bans
   - Management resignation (especially CEO/CFO)
   - Large expansion or capacity addition

2. IGNORE COMPLETELY (return NO TRADE):
   - Compliance filings
   - Scrutinizer reports
   - AGM notices / board meetings
   - Newspaper publications
   - Routine disclosures
   - Certificates / approvals without financial impact

3. SENTIMENT LOGIC:
   - Strong positive business event → BUY
   - Strong negative event → SELL
   - Weak / unclear / mixed → NO TRADE

4. CONFIDENCE SCORING (STRICT):
   - 80–100 → strong, clear impact
   - 65–79 → moderate impact
   - <65 → weak → NO TRADE

5. DO NOT GUESS.
   If moderately positive → BUY (confidence 55–70)
   If moderately negative → SELL (confidence 55–70)
6. Keep reasoning SHORT and factual (1 line only).
IMPORTANT FILTER:

- DO NOT give BUY only because of "acquisition"
- If acquisition impact is unclear, long-term, or already expected → NO TRADE
- If no immediate financial impact (revenue/profit visibility) → NO TRADE

always dont blindly by comparing to the words only. 
---

ANNOUNCEMENT:
{text}

---

Return ONLY valid JSON (no explanation outside JSON):

{{
  "action": "BUY | SELL | NO TRADE",
  "confidence": <integer 0-100>,
  "reason": "<short factual reason>"
}}
"""

        resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

        data = json.loads(resp.choices[0].message.content.replace("```",""))

        action = data["action"].upper()
        confidence = int(data["confidence"])

        if action in ("BUY","SELL") and confidence >= 55:
            final_results.append({
                "ticker": ticker,
                "action": action,
                "confidence": confidence,
                "reason": data["reason"]
            })

        print(f"{ticker} → {action} ({confidence})")

    except Exception as e:
        print("❌", ticker, e)

# =========================
# WRITE OUTPUT (APPEND MODE)
# =========================
out = open_or_create(OUTPUT_SHEET)

# If sheet is empty → add header
existing_data = out.get_all_values()

if not existing_data:
    out.append_row(["Ticker","Action","Confidence","Reason"])

# Append new rows + timestamp in one request
rows_to_append = []
for r in final_results:
    rows_to_append.append([
        r["ticker"],
        r["action"],
        r["confidence"],
        r["reason"]
    ])

print(f"\n✅ Appended: {len(final_results)} signals")

ist_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
rows_to_append.append([])
rows_to_append.append(["Last Updated", ist_time])

if rows_to_append:
    out.append_rows(rows_to_append)

# optional formatting
last_row = len(out.get_all_values())
out.format(f"A{last_row}:B{last_row}", {
    "backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.7},
    "textFormat": {"bold": True}
})

print(f"⏰ IST Time: {ist_time}")
