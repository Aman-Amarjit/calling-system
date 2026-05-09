import asyncio
import json
import os
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_sheets_client():
    """Build an authenticated Google Sheets client using service account JSON from env."""
    creds_json = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
    creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


async def append_booking(name: str, phone: str, date: str, time: str) -> None:
    """
    Append a booking row to the configured Google Sheet.
    Uses run_in_executor to avoid blocking the event loop.
    Row format: Timestamp | Name | Phone | Date | Time
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [[timestamp, name, phone, date, time]]
    sheet_id = os.getenv("GOOGLE_SHEET_ID")

    def _write():
        client = get_sheets_client()
        client.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range="Sheet1!A:E",
            valueInputOption="RAW",
            body={"values": row},
        ).execute()

    # Non-blocking — run the synchronous Sheets API call in a thread pool
    await asyncio.get_running_loop().run_in_executor(None, _write)
    print(f"[SHEETS] Row written: {row[0]}")
