import asyncio
import json
import os
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


_sheets_client = None


def get_sheets_client():
    """Build an authenticated Google Sheets client using service account JSON from env or file."""
    global _sheets_client
    if _sheets_client is not None:
        return _sheets_client
    env_val = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not env_val:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    
    # If it's a filename, read it. If it's raw JSON, parse it.
    if env_val.endswith(".json") and os.path.exists(env_val):
        with open(env_val, "r") as f:
            creds_json = json.load(f)
    else:
        creds_json = json.loads(env_val)
        
    creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    _sheets_client = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _sheets_client


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
