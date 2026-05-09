# AI Calling Bot Demo

An AI-powered inbound calling bot that books appointments through natural voice conversation and writes confirmed bookings to Google Sheets in real time.

**Demo flow:** Call the Telnyx number → Priya (AI) greets you → collects name, phone, date, time → confirms booking → row appears live in Google Sheets.

---

## Prerequisites

- Python 3.10+
- A [Telnyx](https://telnyx.com) account with a purchased phone number
- A [Deepgram](https://deepgram.com) account (free tier works)
- A [Groq](https://console.groq.com) account (free tier works)
- An [ElevenLabs](https://elevenlabs.io) account (free tier: 10,000 chars/month)
- A Google Cloud project with Sheets API enabled and a service account
- [ngrok](https://ngrok.com) with a free static domain

---

## Setup

### 1. Install dependencies

```bash
cd ai-calling-bot-demo
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in all values:

| Variable | Where to get it |
|----------|----------------|
| `TELNYX_API_KEY` | Telnyx Portal → API Keys |
| `TELNYX_PUBLIC_KEY` | Telnyx Portal → API Keys |
| `DEEPGRAM_API_KEY` | Deepgram Console → API Keys |
| `GROQ_API_KEY` | console.groq.com → API Keys |
| `ELEVENLABS_API_KEY` | ElevenLabs → Profile → API Key |
| `ELEVENLABS_VOICE_ID` | ElevenLabs → Voices → click a voice → copy ID |
| `GOOGLE_SHEET_ID` | From the Sheet URL: `docs.google.com/spreadsheets/d/{SHEET_ID}/` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Paste the full service account JSON as a single-line string |
| `NGROK_URL` | Your ngrok static domain (see step 4) |

### 3. Set up Google Sheets

1. Create a new Google Sheet
2. Add headers in row 1: `Timestamp`, `Name`, `Phone`, `Date`, `Time`
3. Go to [Google Cloud Console](https://console.cloud.google.com)
4. Create a project → Enable **Google Sheets API**
5. Create a **Service Account** → download the JSON key
6. Copy the entire JSON content and paste it as the value of `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env` (must be on one line)
7. Share your Google Sheet with the service account email (Editor access)

### 4. Set up ngrok static domain

```bash
# Authenticate ngrok (one-time)
ngrok config add-authtoken <your-ngrok-token>

# Start ngrok with your free static domain (never changes between restarts)
ngrok http --domain=your-static-domain.ngrok-free.app 8000
```

Get your static domain: [ngrok Dashboard](https://dashboard.ngrok.com) → Cloud Edge → Domains

Set `NGROK_URL=https://your-static-domain.ngrok-free.app` in `.env`

### 5. Configure Telnyx webhook

1. Go to Telnyx Portal → Your phone number → Messaging & Voice settings
2. Set the webhook URL to: `https://your-static-domain.ngrok-free.app/webhook/telnyx`
3. Set webhook method to `POST`

### 6. Run the server

```bash
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

Verify it's running:
```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

## Running the Demo

1. Open your Google Sheet on the laptop facing the client
2. Start ngrok: `ngrok http --domain=your-static-domain.ngrok-free.app 8000`
3. Start the server: `uvicorn main:app --reload --port 8000`
4. Call the Telnyx number from your phone
5. Priya greets you — provide name, phone, date, time when asked
6. After confirmation, watch the row appear in Google Sheets live

**Total demo time: ~60–90 seconds**

---

## Console Logs

During the call you'll see:
```
[WEBHOOK] event_type=call.initiated call_sid=...
[CALL] Initiated: ...
[CALL] Answered: ...
[TTS] Greeting audio: https://...
[CALL] Playback ended: ...
[STT] Greeting done, connecting Deepgram for call: ...
[CALLER]: Mera naam Rahul hai
[PRIYA]: Shukriya Rahul ji! Aapka phone number kya hai?
...
[BOOKING] Confirmed for call: ... | Fields: {...}
[SHEETS] Row written: ['2024-01-15 14:32:01', 'Rahul', '9876543210', '15 June', '3 PM']
[CALL] Hangup: ...
```

---

## Project Structure

```
ai-calling-bot-demo/
├── main.py        # FastAPI app, webhook routing, conversation orchestration
├── session.py     # In-memory session store per call
├── llm.py         # Groq LLM wrapper + Priya system prompt
├── stt.py         # Deepgram WebSocket STT manager
├── tts.py         # ElevenLabs TTS → .mp3 file
├── sheets.py      # Google Sheets API integration
├── audio/         # Temp .mp3 files (auto-cleaned after each call)
├── .env           # Your API keys (never commit this)
├── .env.example   # Template
└── requirements.txt
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Server starts but call doesn't connect | Check Telnyx webhook URL matches your ngrok domain |
| Bot answers but no voice | Check `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in `.env` |
| Bot speaks but doesn't hear you | Telnyx media streaming may need to be enabled on your number |
| Google Sheets row not appearing | Check service account has Editor access to the sheet; verify `GOOGLE_SHEET_ID` |
| `RuntimeError: Missing required env var` | Fill in all 8 required vars in `.env` |
| ngrok URL changed | Use a static domain — see step 4 |
 a03859e (Initial commit — Priya AI calling bot demo)
