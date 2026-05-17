import requests
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pytz

# ---------------------------
# GOOGLE SHEET CONNECTION
# ---------------------------

SERVICE_FILE = "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(SERVICE_FILE, scopes=SCOPES)

client = gspread.authorize(creds)

spreadsheet = client.open_by_url(
    "https://docs.google.com/spreadsheets/d/1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ/edit"
)

try:
    sheet = spreadsheet.worksheet("bse")
except gspread.exceptions.WorksheetNotFound:
    sheet = spreadsheet.add_worksheet(title="bse", rows="200", cols="10")


# ---------------------------
# FETCH BSE DATA
# ---------------------------

url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"

params = {
    "pageno": 1,
    "strCat": -1,
    "strPrevDate": "",
    "strScrip": "",
    "strSearch": "P",
    "strToDate": "",
    "strType": "C",
}

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.bseindia.com/corporates/ann.html"
}

response = requests.get(url, params=params, headers=headers)

data = response.json()

rows = []

for item in data["Table"]:

    company = item["SLONGNAME"]
    code = item["SCRIP_CD"]
    title = item["HEADLINE"]
    category = item["CATEGORYNAME"]

    rows.append([
        code,
        company,
        title,
        category
    ])


# ---------------------------
# UPDATE GOOGLE SHEET
# ---------------------------

sheet.clear()

sheet.append_row([
    "SYMBOL",
    "COMPANY NAME",
    "ANNOUNCEMENT",
    "CATEGORY"
])

sheet.append_rows(rows)

# ---------------------------
# ADD LAST UPDATED TIME
# ---------------------------

ist = pytz.timezone("Asia/Kolkata")
now = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

sheet.append_row([])
sheet.append_row(["Last Updated:", now])

print("BSE announcements updated:", len(rows))
