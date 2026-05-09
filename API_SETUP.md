# API Setup Guide for AI Calling Bot Demo

This guide is written for someone who may not know how to configure APIs yet. Follow each step exactly, copy the text, and paste it into the `.env` file.

## Step 1: Make a copy of `.env.example`

In the project folder, create your `.env` file from the example:

- On Windows: open `File Explorer`, right-click `.env.example`, copy it, and rename the copy to `.env`
- On macOS/Linux: run this command in the project folder:

```bash
cp .env.example .env
```

## Step 2: Open `.env` in a text editor

If you are using Windows:
- Right-click `.env` and choose `Open with` → `Notepad` or `Visual Studio Code`

If you are using macOS/Linux:
- Open it in a text editor such as VS Code, TextEdit, or Nano.
- Example command: `code .env` or `nano .env`

## Step 3: Fill in each variable using the links below

Use the exact links and steps for each service.

### 3.1 Telnyx (phone calls)

- `TELNYX_API_KEY`
- `TELNYX_PUBLIC_KEY`

How to get them:
1. Sign up at https://telnyx.com/ and log in.
2. Go to the Telnyx dashboard.
3. Find `API Keys` or `Credentials`.
4. Copy the value for `TELNYX_API_KEY` and paste it into `.env`.
5. Copy the value for `TELNYX_PUBLIC_KEY` and paste it into `.env`.

Helpful page: https://developers.telnyx.com/docs/api/v2

### 3.2 Deepgram (speech transcription)

- `DEEPGRAM_API_KEY`

How to get it:
1. Sign up at https://deepgram.com/ and log in.
2. Go to the Deepgram console: https://console.deepgram.com/
3. Find `API Keys`.
4. Create a new key if needed.
5. Copy the key and paste it into `.env`.

Helpful page: https://developers.deepgram.com/docs

### 3.3 Groq (LLM service) — optional

- `GROQ_API_KEY`

If you do not want to use Groq, leave this line blank and the app will try to use local Ollama instead.

How to get it:
1. Sign up at https://groq.com/ and log in.
2. Go to `API Keys`.
3. Create a new key.
4. Copy it and paste it into `.env`.

Helpful page: https://console.groq.com/docs

### 3.4 ElevenLabs (voice synthesis)

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`

How to get them:
1. Sign up at https://elevenlabs.io/ and log in.
2. Open your profile: https://elevenlabs.io/app/profile
3. Copy your API key and paste it into `.env`.
4. Open the voice library: https://elevenlabs.io/app/voices
5. Click a voice you like and copy the voice ID from the page.
6. Paste the voice ID into `.env`.

Helpful page: https://docs.elevenlabs.io/api-reference

### 3.5 Google Sheets (save bookings)

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

How to get them:
1. Open https://console.cloud.google.com/ and sign in.
2. Create or open a Google Cloud project.
3. Enable the Google Sheets API.
4. Create a service account under `IAM & Admin > Service Accounts`.
5. Create a key for the service account and download the JSON file.
6. Open the downloaded JSON file with a text editor.
7. Copy the full JSON text exactly as it appears.
8. Paste that full JSON into the `.env` line for `GOOGLE_SERVICE_ACCOUNT_JSON`.

How to find `GOOGLE_SHEET_ID`:
- Open your Google Sheet.
- Look at the URL in the browser.
- Copy the long string between `/d/` and `/edit`.
- Paste that string into `.env`.

Helpful page: https://developers.google.com/sheets/api/quickstart/python

### 3.6 ngrok URL (optional but recommended)

- `NGROK_URL`

If you are running the app locally and want audio files to work, use ngrok:
1. Install ngrok from https://ngrok.com/
2. Run `ngrok http 8000`
3. Copy the URL shown by ngrok, such as `https://xxxxxx.ngrok.io`
4. Paste that URL into `.env`

If you are not using ngrok, you can leave this blank for now.

### 3.7 Ollama settings (only if using local Ollama)

- `OLLAMA_URL` should remain `http://localhost:11434`
- `OLLAMA_MODEL` should remain `llama3.2:3b`

If you are using `GROQ_API_KEY`, you do not need to change these.

## Step 4: Example `.env` content

Copy this example and replace the `your_*` text with your real values.

```env
TELNYX_API_KEY=your_telnyx_api_key
TELNYX_PUBLIC_KEY=your_telnyx_public_key
DEEPGRAM_API_KEY=your_deepgram_api_key
GROQ_API_KEY=your_groq_api_key
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=your_voice_id
GOOGLE_SHEET_ID=your_google_sheet_id
GOOGLE_SERVICE_ACCOUNT_JSON={"type": "service_account", "project_id": "...", "private_key": "-----BEGIN PRIVATE KEY-----...", "client_email": "..."}
NGROK_URL=https://your-ngrok-url.ngrok.io
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
```

## Step 5: Save the `.env` file

After you paste the values:
- Save the file in your editor.
- Make sure the file name is exactly `.env`.
- Do not save it as `.env.txt`.

## Quick checklist for beginners

| Variable | Do this |
|---|---|
| `TELNYX_API_KEY` | Copy from Telnyx API Keys page |
| `TELNYX_PUBLIC_KEY` | Copy from Telnyx API Keys page |
| `DEEPGRAM_API_KEY` | Copy from Deepgram Console > API Keys |
| `GROQ_API_KEY` | Copy from Groq console, or leave blank to use Ollama |
| `ELEVENLABS_API_KEY` | Copy from ElevenLabs Profile |
| `ELEVENLABS_VOICE_ID` | Copy from ElevenLabs Voices page |
| `GOOGLE_SHEET_ID` | Copy from the Google Sheet URL after `/d/` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Paste full JSON from downloaded service account key |
| `NGROK_URL` | Copy from ngrok after running `ngrok http 8000` |

## Troubleshooting

- If the `.env` file does not work, re-open it and check for missing values.
- If the JSON line breaks into multiple lines, remove the line breaks so it stays one line.
- If you are not sure what a value is, use the links above and copy the exact key from the website.
- If a field is not ready yet, leave it blank and come back later.

## Final note

This guide is meant to help you update `.env` by yourself. When in doubt, copy a value from the service website and paste it into the matching `.env` field.
</content>
<parameter name="filePath">/home/aman-amarjit/Desktop/call demo/ai-calling-bot-demo/API_SETUP.md