#import os
#import json
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
import gspread
from oauth2client.service_account import ServiceAccountCredentials

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_google_credentials():
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "credentials.json", scope
    )
    return creds

def authorize_google_sheets(credentials):
    return gspread.authorize(credentials)

# your existing update_google_sheet_by_name and append_footer functions remain unchanged


def update_google_sheet_by_name(sheet_id, worksheet_name, headers, rows, footer_row=None):
    try:
        credentials = get_google_credentials()
        gc = authorize_google_sheets(credentials)
        sh = gc.open_by_key(sheet_id)

        try:
            worksheet = sh.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.add_worksheet(title=worksheet_name, rows="100", cols="20")

        data = [headers] + rows
        if footer_row:
            data.append([])
            data.append(footer_row)

        worksheet.clear()
        worksheet.update("A1", data)
        print(f"✅ Data updated in worksheet: {worksheet_name}")

    except Exception as e:
        print(f"❌ Google Sheet update error: {e}")

def append_footer(sheet_id, worksheet_name, footer_row):
    try:
        credentials = get_google_credentials()
        gc = authorize_google_sheets(credentials)
        worksheet = gc.open_by_key(sheet_id).worksheet(worksheet_name)

        existing = worksheet.get_all_values()
        next_row = len(existing) + 1
        worksheet.update(f"A{next_row}", [footer_row])
        print("🕒 Timestamp footer appended.")
    except Exception as e:
        print(f"❌ Footer append error: {e}")
