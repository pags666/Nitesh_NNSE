import requests
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo  # ✅ for IST

# your existing module
from google_sheets import update_google_sheet_by_name, append_footer

BASE_URL = "https://www.moneycontrol.com/news/business/stocks/"
HEADERS = {"User-Agent": "Mozilla/5.0"}

pages = 3

rows = []

for page in range(1, pages + 1):

    if page == 1:
        url = BASE_URL
    else:
        url = f"{BASE_URL}page-{page}/"

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Error fetching page {page}: {e}")
        continue

    soup = BeautifulSoup(response.text, "html.parser")

    news_items = soup.find_all("li", class_="clearfix")

    for item in news_items:

        h2 = item.find("h2")
        a = item.find("a")

        if h2 and a:
            title = h2.text.strip()
          

            rows.append([title])

# ---------------- GOOGLE SHEETS ---------------- #

SHEET_ID = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"
WORKSHEET = "monc"

headers = ["TITLE", "LINK"]

# update sheet
update_google_sheet_by_name(SHEET_ID, WORKSHEET, headers, rows)

# ---------------- IST TIMESTAMP ---------------- #

ist_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")

append_footer(
    SHEET_ID,
    WORKSHEET,
    ["Updated (IST):", ist_time]
)
