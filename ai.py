import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
import json
import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from groq import Groq
from oauth2client.service_account import ServiceAccountCredentials

# =========================================================
# JSON EXTRACTOR
# =========================================================
def extract_json(text):

    try:

        match = re.search(r'\{.*\}', text, re.DOTALL)

        if not match:
            return None

        return json.loads(match.group())

    except Exception:
        return None


# =========================================================
# CONFIG
# =========================================================
SHEET_ID = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"

INPUT_SHEETS = [
    "nse",
    "bse"
]

OUTPUT_WS = "groq"


# =========================================================
# GOOGLE SHEETS AUTH
# =========================================================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_name(
    "credentials.json",
    scope
)

client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SHEET_ID)


# =========================================================
# OUTPUT SHEET
# =========================================================
try:

    output_ws = spreadsheet.worksheet(OUTPUT_WS)

except:

    output_ws = spreadsheet.add_worksheet(
        title=OUTPUT_WS,
        rows="1000",
        cols="20"
    )


# =========================================================
# GROQ
# =========================================================
groq = Groq(
    api_key=os.environ["GROQ_API_KEY"]
)


# =========================================================
# READ DATA
# =========================================================
all_rows = []

for sheet_name in INPUT_SHEETS:

    try:

        ws = spreadsheet.worksheet(sheet_name)

        data = ws.get_all_records()

        print(f"✅ Loaded {sheet_name}: {len(data)} rows")

        for row in data:

            row["EXCHANGE"] = sheet_name.upper()

            all_rows.append(row)

    except Exception as e:

        print(f"❌ Error loading {sheet_name}: {e}")


# =========================================================
# GROUP NEWS
# =========================================================
company_news = defaultdict(list)

seen = set()

for row in all_rows:

    company = ""
    news = ""

    # =====================================================
    # NSE
    # =====================================================
    if "DETAILS" in row:

        company = str(
            row.get("SYMBOL", "")
        ).strip().upper()

        news = str(
            row.get("DETAILS", "")
        ).strip()

    # =====================================================
    # BSE
    # =====================================================
    elif "ANNOUNCEMENT" in row:

        company = str(
            row.get("COMPANY NAME", "")
        ).strip().upper()

        news = str(
            row.get("ANNOUNCEMENT", "")
        ).strip()

    else:
        continue

    if not company:
        continue

    if not news:
        continue

    key = (company, news)

    if key not in seen:

        company_news[company].append(news)

        seen.add(key)


# =========================================================
# IGNORE KEYWORDS
# =========================================================
IGNORE_KEYWORDS = [

    "scrutinizer",
    "certificate",
    "postal ballot",
    "agm",
    "newspaper publication",
    "trading window",
    "shareholding pattern",
    "voting results",
    "analyst meeting",
    "investor presentation",
    "record date",
    "book closure",
    "committee meeting",
    "clarification",
    "corporate action",
    "re-appointment",
    "appointment",
    "cessation",
    "board meeting",
    "outcome of board meeting",
    "financial statement",
    "compliance certificate",
    "newspaper advertisement",
    "postal ballot notice",
    "esop",
    "intimation",
    "updates",
    "closure of trading window",
]


# =========================================================
# EVENT MAP
# =========================================================
EVENT_MAP = {

    # =====================================================
    # BUY EVENTS
    # =====================================================
    "received order": "ORDER",
    "bagging order": "ORDER",
    "work order": "ORDER",
    "letter of award": "ORDER",
    "large order": "ORDER",
    "major order": "ORDER",
    "export order": "ORDER",
    "contract": "ORDER",

    "acquisition": "ACQUISITION",

    "buyback": "BUYBACK",

    "capacity expansion": "EXPANSION",
    "commercial production": "EXPANSION",
    "commissioning": "EXPANSION",

    "strategic partnership": "PARTNERSHIP",

    "profit increase": "EARNINGS",
    "margin expansion": "EARNINGS",
    "revenue growth": "EARNINGS",
    "ebitda growth": "EARNINGS",

    "approval received": "APPROVAL",

    # =====================================================
    # SELL EVENTS
    # =====================================================
    "auditor resignation": "AUDITOR_RISK",

    "insolvency": "INSOLVENCY",

    "default": "DEFAULT",

    "fraud": "FRAUD",

    "sebi action": "REGULATORY",

    "bankruptcy": "BANKRUPTCY",

    "loss increase": "WEAK_EARNINGS",

    "pledged shares": "PLEDGE_RISK",

    "downgrade": "DOWNGRADE",
}


# =========================================================
# CLASSIFY NEWS
# =========================================================
def classify_news(news_list):

    text = " ".join(news_list).lower()

    # =====================================================
    # IGNORE NOISE
    # =====================================================
    for word in IGNORE_KEYWORDS:

        if word in text:

            return "IGNORE"

    # =====================================================
    # EVENT DETECTION
    # =====================================================
    for keyword, event_type in EVENT_MAP.items():

        if keyword in text:

            return event_type

    return "UNKNOWN"


# =========================================================
# AI ANALYSIS
# =========================================================
def analyze(company, news_list):

    combined_news = "\n".join(news_list[:3])

    prompt = f"""
You are a professional stock market analyst.

Analyze the following company news very carefully.

Company:
{company}

News:
{combined_news}

Your job:

1. Detect whether this news is strongly bullish or strongly bearish.
2. Ignore useless compliance and routine filing news.
3. Focus ONLY on important price-moving events.

Examples of bullish events:
- very large order
- major acquisition
- strong earnings growth
- major margin expansion
- export order
- strategic expansion
- buyback
- major approval

Examples of bearish events:
- fraud
- insolvency
- default
- bankruptcy
- auditor resignation
- major regulatory action
- severe losses

IMPORTANT:

Do NOT guess.

Do NOT hallucinate.

Do NOT generate paragraphs.

Return ONLY valid JSON.

Use probability 100 ONLY if the event is extremely strong and obvious.

Examples:
- fraud
- insolvency
- huge order
- major acquisition
- strong earnings surprise
- bankruptcy

Return ONLY this format:

{{
    "probability": 100,
    "action": "BUY",
    "reason": "short factual reason"
}}

OR

{{
    "probability": 100,
    "action": "SELL",
    "reason": "short factual reason"
}}

OR

{{
    "probability": 0,
    "action": "NO TRADE",
    "reason": "no strong trigger"
}}
"""

    response = groq.chat.completions.create(

        model="llama-3.1-8b-instant",

        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],

        temperature=0
    )

    content = response.choices[0].message.content.strip()

    content = content.replace("```json", "")
    content = content.replace("```", "")
    content = content.strip()

    return content


# =========================================================
# PROCESS
# =========================================================
results = []

for company, news_list in company_news.items():

    if not news_list:
        continue

    signal = classify_news(news_list)

    # =====================================================
    # IGNORE
    # =====================================================
    if signal == "IGNORE":
        continue

    try:

        # =================================================
        # AI ANALYSIS
        # =================================================
        ai_output = analyze(
            company,
            news_list
        )

        if not ai_output:
            continue

        data = extract_json(ai_output)

        if not data:

            print(f"❌ Invalid JSON for {company}")

            print(ai_output)

            continue

        probability = data.get(
            "probability",
            0
        )

        action = data.get(
            "action",
            "NO TRADE"
        )

        reason = data.get(
            "reason",
            ""
        )

        # =================================================
        # ONLY EXTREME CONFIDENCE
        # =================================================
        if probability != 100:
            continue

        if action not in ["BUY", "SELL"]:
            continue

        # =================================================
        # APPEND
        # =================================================
        results.append([
            company,
            probability,
            action,
            reason
        ])

    except Exception as e:

        print(f"❌ Error for {company}: {e}")


# =========================================================
# SORT RESULTS
# =========================================================
results.sort(
    key=lambda x: x[1],
    reverse=True
)

results = results[:10]


# =========================================================
# WRITE OUTPUT
# =========================================================
existing_data = output_ws.get_all_values()

if not existing_data:

    output_ws.append_row([
        "Company",
        "Probability %",
        "Action",
        "Reason"
    ])


# =========================================================
# APPEND RESULTS
# =========================================================
for row in results:

    output_ws.append_row(row)


# =========================================================
# TIMESTAMP
# =========================================================
ist_time = datetime.now(
    ZoneInfo("Asia/Kolkata")
).strftime("%Y-%m-%d %H:%M:%S")

output_ws.append_row([
    "Updated (IST)",
    ist_time
])


# =========================================================
# FORMAT TIMESTAMP
# =========================================================
last_row = len(
    output_ws.get_all_values()
)

output_ws.format(

    f"A{last_row}:B{last_row}",

    {
        "backgroundColor": {
            "red": 1,
            "green": 0.9,
            "blue": 1
        },

        "textFormat": {
            "bold": True
        }
    }
)

print("✅ COMPLETED SUCCESSFULLY")
