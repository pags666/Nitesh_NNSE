# =========================
# IMPORTS
# =========================
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from transformers import pipeline
from groq import Groq
import time
import os
import base64

# =========================
# CONFIG
# =========================
SHEET_ID = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"
WORKSHEETS = ["nse", "bse", "et", "monc"]

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# =========================
# CREATE GOOGLE CREDS FILE
# =========================
def create_creds():
    encoded = os.getenv("service_account")

    if encoded:
        with open("credentials.json", "wb") as f:
            f.write(base64.b64decode(encoded))

# =========================
# GOOGLE SHEETS AUTH
# =========================
def connect_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "credentials.json", scope
    )

    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

# =========================
# FETCH NEWS
# =========================
def get_all_news(sheet):
    all_news = []

    for ws_name in WORKSHEETS:
        try:
            ws = sheet.worksheet(ws_name)
            data = ws.col_values(1)
            data = data[1:] if data else []
            all_news.extend(data)
        except Exception as e:
            print(f"Error reading {ws_name}: {e}")

    cleaned = list(set([n.strip() for n in all_news if n.strip() != ""]))
    return cleaned

# =========================
# LOAD FINBERT
# =========================
print("Loading FinBERT...")
finbert = pipeline("sentiment-analysis", model="ProsusAI/finbert")

# =========================
# FINBERT SCORING
# =========================
def finbert_score(news):
    score = 0
    details = []

    batch_size = 10

    for i in range(0, len(news), batch_size):
        batch = news[i:i+batch_size]
        results = finbert(batch)

        for n, r in zip(batch, results):
            label = r['label']
            conf = r['score']

            if label == 'positive':
                score += conf
            elif label == 'negative':
                score -= conf

            details.append({
                "news": n,
                "sentiment": label,
                "confidence": round(conf, 3)
            })

        time.sleep(0.2)

    return score, details

# =========================
# GROQ SETUP
# =========================
client = Groq(api_key=GROQ_API_KEY)

def groq_analysis(news):
    try:
        news_input = news[:20]

        prompt = f"""
        You are a NIFTY direction classifier.

        Output ONLY:
        BULLISH or BEARISH or NEUTRAL

        News:
        {news_input}
        """

        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=5
        )

        return res.choices[0].message.content.strip().upper()

    except Exception as e:
        print("Groq error:", e)
        return "NEUTRAL"

# =========================
# FINAL BIAS
# =========================
def generate_bias(fin_score, groq_text):

    if "BULLISH" in groq_text:
        g_score = 2
    elif "BEARISH" in groq_text:
        g_score = -2
    else:
        g_score = 0

    final_score = (fin_score * 0.7) + (g_score * 0.3)

    if final_score > 5:
        return "🚀 STRONG BULLISH"
    elif final_score > 2:
        return "🟢 BULLISH"
    elif final_score < -5:
        return "🔻 STRONG BEARISH"
    elif final_score < -2:
        return "🔴 BEARISH"
    else:
        return "⚖️ SIDEWAYS"

# =========================
# MAIN
# =========================
if __name__ == "__main__":

    print("🔐 Creating credentials...")
    create_creds()

    print("🔌 Connecting to Google Sheet...")
    sheet = connect_sheet()

    print("📰 Fetching News...")
    news = get_all_news(sheet)

    if not news:
        print("❌ No news found")
        exit()

    print(f"✅ Total News: {len(news)}")

    print("🧠 Running FinBERT...")
    fin_score, details = finbert_score(news)

    print(f"📊 FinBERT Score: {round(fin_score,2)}")

    # Filter top news
    top_news = sorted(details, key=lambda x: x['confidence'], reverse=True)[:20]
    news_for_groq = [d['news'] for d in top_news]

    print("🤖 Running Groq...")
    groq_text = groq_analysis(news_for_groq)

    print("Groq:", groq_text)

    bias = generate_bias(fin_score, groq_text)

    print("🎯 FINAL NIFTY BIAS:", bias)
