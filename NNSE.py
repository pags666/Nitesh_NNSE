import requests
import time
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pytz

# -------------------------
# GOOGLE SHEETS LOGIN
# -------------------------

SERVICE_ACCOUNT_FILE = "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=SCOPES
)

client = gspread.authorize(creds)

sheet_url = "https://docs.google.com/spreadsheets/d/1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ/edit"
spreadsheet = client.open_by_url(sheet_url)
try:
    sheet = spreadsheet.worksheet("nse")
except gspread.exceptions.WorksheetNotFound:
    sheet = spreadsheet.add_worksheet(title="nse", rows="200", cols="10")


# -------------------------
# NSE REQUEST
# -------------------------

session = requests.Session()

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest"
}

# Get cookies
session.get("https://www.nseindia.com", headers=headers)
time.sleep(3)

url = "https://www.nseindia.com/api/corporate-announcements?index=equities"

response = session.get(url, headers=headers)

data = response.json()

rows = []

for item in data:

    symbol = item.get("symbol", "")
    company = item.get("sm_name", "")
    subject = item.get("desc", "")
    details = item.get("attchmntText", "")

    rows.append([
        symbol,
        company,
        subject,
        details
    ])

df = pd.DataFrame(rows, columns=[
    "SYMBOL",
    "COMPANY NAME",
    "SUBJECT",
    "DETAILS"
])


# -------------------------
# UPLOAD TO GOOGLE SHEET
# -------------------------

ist = pytz.timezone("Asia/Kolkata")
now = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

data = [df.columns.values.tolist()] + df.values.tolist()
data.append([])
data.append(["Last Updated:", now])

sheet.clear()
sheet.update("A1", data)

print("Uploaded to Google Sheet successfully")
print("Last Updated:", now)
