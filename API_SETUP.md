# API Setup Guide for AI Calling Bot Demo

This guide explains how to obtain and configure the required API keys and credentials for the AI calling bot. Add these to your `.env` file in the project root.

## Required Environment Variables

### Telnyx (Telephony)
- **TELNYX_API_KEY**: Your Telnyx API key for call control.
- **TELNYX_PUBLIC_KEY**: Your Telnyx public key for webhooks.

**How to get it:**
1. Sign up at [Telnyx](https://telnyx.com/).
2. Go to your [Dashboard](https://portal.telnyx.com/) > API Keys.
3. Create a new API key and public key.
4. Docs: [Telnyx API Docs](https://developers.telnyx.com/docs/api/v2).

### Deepgram (Speech-to-Text)
- **DEEPGRAM_API_KEY**: API key for real-time speech transcription.

**How to get it:**
1. Sign up at [Deepgram](https://deepgram.com/).
2. Navigate to [Console](https://console.deepgram.com/) > API Keys.
3. Create a new key.
4. Docs: [Deepgram API Docs](https://developers.deepgram.com/docs).

### Groq (LLM Inference)
- **GROQ_API_KEY**: API key for fast LLM responses (optional; falls back to Ollama if not set).

**How to get it:**
1. Sign up at [Groq](https://groq.com/).
2. Go to [Console](https://console.groq.com/) > API Keys.
3. Generate a new key.
4. Docs: [Groq API Docs](https://console.groq.com/docs).

### ElevenLabs (Text-to-Speech)
- **ELEVENLABS_API_KEY**: API key for voice synthesis.
- **ELEVENLABS_VOICE_ID**: ID of the voice to use (e.g., a specific voice model).

**How to get it:**
1. Sign up at [ElevenLabs](https://elevenlabs.io/).
2. Access [Profile](https://elevenlabs.io/app/profile) > API Key.
3. Copy the API key.
4. For voice ID: Go to [Voices](https://elevenlabs.io/app/voices), select a voice, and note its ID from the URL or API.
5. Docs: [ElevenLabs API Docs](https://docs.elevenlabs.io/api-reference).

### Google Sheets (Data Storage)
- **GOOGLE_SHEET_ID**: The ID of your Google Sheet (from the URL: `https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit`).
- **GOOGLE_SERVICE_ACCOUNT_JSON**: JSON content of your Google Service Account key (as a string).

**How to get it:**
1. Create a Google Cloud Project at [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the Google Sheets API.
3. Create a Service Account: IAM & Admin > Service Accounts > Create.
4. Generate a JSON key for the service account.
5. Share your Google Sheet with the service account email.
6. Docs: [Google Sheets API Quickstart](https://developers.google.com/sheets/api/quickstart/python).

### Optional
- **NGROK_URL**: Public URL from ngrok for serving audio files (if using ngrok for local dev).
  - Get from [ngrok](https://ngrok.com/): Run `ngrok http 8000` and copy the URL.
- **OLLAMA_URL**: URL for local Ollama server (default: `http://localhost:11434`).
- **OLLAMA_MODEL**: Model name for Ollama (default: `llama3.2:3b`).

## Example .env File

```
TELNYX_API_KEY=your_telnyx_api_key
TELNYX_PUBLIC_KEY=your_telnyx_public_key
DEEPGRAM_API_KEY=your_deepgram_key
GROQ_API_KEY=your_groq_key
ELEVENLABS_API_KEY=your_elevenlabs_key
ELEVENLABS_VOICE_ID=your_voice_id
GOOGLE_SHEET_ID=your_sheet_id
GOOGLE_SERVICE_ACCOUNT_JSON={"type": "service_account", ...}  # Paste full JSON
NGROK_URL=https://your-ngrok-url.ngrok.io
```

## Notes
- Keep `.env` secure and never commit it to version control.
- Test each API individually after setup.
- If using Ollama locally, ensure it's running: `ollama serve`.</content>
<parameter name="filePath">/home/aman-amarjit/Desktop/call demo/ai-calling-bot-demo/API_SETUP.md