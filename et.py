import requests
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo  # ✅ IST support

# import your existing google sheets module
from google_sheets import update_google_sheet_by_name, append_footer

BASE = "https://economictimes.indiatimes.com"
URL = "https://economictimes.indiatimes.com/markets/stocks/news"

HEADERS = {"User-Agent": "Mozilla/5.0"}

rows = []

try:
    res = requests.get(URL, headers=HEADERS, timeout=10)
    res.raise_for_status()
except Exception as e:
    print("Error fetching ET page:", e)
    rows = []
else:
    soup = BeautifulSoup(res.text, "html.parser")

    articles = soup.select("h3 a")

    for a in articles[:20]:

        subject = a.text.strip()
        link = BASE + a.get("href")

        # ---- SYMBOL EXTRACTION ---- #
        symbol = ""

        for word in subject.split():
            if word.isupper() and len(word) <= 10:
                
                break

        rows.append([ subject])

# ---------------- GOOGLE SHEETS ---------------- #

SHEET_ID = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"
WORKSHEET = "et"

headers = [ "SUBJECT"]

# update sheet
update_google_sheet_by_name(SHEET_ID, WORKSHEET, headers, rows)

# ---------------- IST TIMESTAMP ---------------- #

ist_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")

append_footer(
    SHEET_ID,
    WORKSHEET,
    ["Updated (IST):", ist_time]
)
