from dotenv import load_dotenv
load_dotenv()  # MUST be first — before any custom imports that read os.getenv() at module level

import asyncio
import glob
import os
from contextlib import asynccontextmanager

import telnyx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from llm import extract_booking_fields, extract_fields_from_text, get_llm_response, is_booking_confirmed
from sheets import append_booking
from session import create_session, delete_session, get_session
from stt import SILENCE_PROMPT, connect_deepgram, disconnect_deepgram, send_audio
from tts import synthesise

# ---------------------------------------------------------------------------
# Telnyx helper — all SDK calls are synchronous; wrap in executor so they
# don't block the async event loop
# ---------------------------------------------------------------------------
async def _telnyx(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

REQUIRED_ENV_VARS = [
    "TELNYX_API_KEY",
    "TELNYX_PUBLIC_KEY",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    # GROQ_API_KEY is optional — omit to use Ollama instead
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    for var in REQUIRED_ENV_VARS:
        if not os.getenv(var):
            raise RuntimeError(f"Missing required env var: {var}")
    yield


app = FastAPI(title="AI Calling Bot Demo", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def stats():
    """Live stats for the frontend monitor."""
    from session import sessions
    latest = None
    for s in sessions.values():
        if s.booking_status == "confirmed" and s.collected.get("name"):
            c = s.collected
            latest = {
                "timestamp": c.get("timestamp", ""),
                "name":      c.get("name", ""),
                "phone":     c.get("phone", ""),
                "date":      c.get("date", ""),
                "time":      c.get("time", ""),
            }
            break
    return {"active_calls": len(sessions), "latest_booking": latest}


@app.post("/webhook/telnyx")
async def webhook_telnyx(request: Request):
    payload = await request.json()
    event_type = payload["data"]["event_type"]
    call_sid   = payload["data"]["payload"]["call_control_id"]
    print(f"[WEBHOOK] {event_type} | {call_sid}")

    if   event_type == "call.initiated":     return await handle_call_initiated(call_sid, payload)
    elif event_type == "call.answered":      return await handle_call_answered(call_sid, payload)
    elif event_type == "call.playback.ended":return await handle_playback_ended(call_sid, payload)
    elif event_type == "call.hangup":        return await handle_hangup(call_sid, payload)
    else:
        print(f"[WEBHOOK] Ignored: {event_type}")
        return JSONResponse({"status": "ignored"})


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    # Sanitise filename — no path traversal
    filename = os.path.basename(filename)
    path = os.path.join("audio", filename)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="audio/mpeg")


@app.websocket("/media/{call_sid}")
async def media_stream(websocket: WebSocket, call_sid: str):
    """Receive raw audio from Telnyx and forward to Deepgram."""
    await websocket.accept()
    print(f"[MEDIA] WebSocket opened: {call_sid}")
    try:
        while True:
            data = await websocket.receive_bytes()
            await send_audio(call_sid, data)
    except WebSocketDisconnect:
        print(f"[MEDIA] WebSocket closed: {call_sid}")
        await disconnect_deepgram(call_sid)


# ---------------------------------------------------------------------------
# Call handlers
# ---------------------------------------------------------------------------

async def handle_call_initiated(call_sid: str, payload: dict):
    """Create session and answer the call — non-blocking Telnyx call."""
    create_session(call_sid)
    print(f"[CALL] Initiated: {call_sid}")
    telnyx.api_key = os.getenv("TELNYX_API_KEY")
    call = await _telnyx(telnyx.Call.retrieve, call_sid)
    await _telnyx(call.answer)
    return JSONResponse({"status": "answered"})


async def handle_call_answered(call_sid: str, payload: dict):
    """Synthesise greeting, return TeXML <Play> + <Stream>. Deepgram NOT connected yet."""
    print(f"[CALL] Answered: {call_sid}")
    greeting = (
        "Namaste Sir, aapka swagat hai hamare appointment centre mein. "
        "Main Priya bol rahi hoon — aapki kaise sahayata kar sakti hoon? "
        "Kripaya apna poora naam batayein."
    )
    try:
        filename   = await synthesise(greeting)
        ngrok_url  = os.getenv("NGROK_URL", "")
        audio_url  = f"{ngrok_url}/audio/{filename}"
        # wss:// for WebSocket — strip the https:// scheme
        ngrok_domain = ngrok_url.replace("https://", "").replace("http://", "")
        print(f"[TTS] Greeting: {audio_url}")
        texml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Play>{audio_url}</Play>
  <Stream url="wss://{ngrok_domain}/media/{call_sid}" />
</Response>'''
        return PlainTextResponse(texml, media_type="text/xml")
    except Exception as e:
        print(f"[ERROR] Greeting TTS failed: {e}")
        return PlainTextResponse(
            '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="text/xml",
        )


async def handle_playback_ended(call_sid: str, payload: dict):
    """
    Two jobs:
    1. First playback (greeting) → connect Deepgram, start silence timer.
    2. Subsequent playbacks (bot responses) → restart silence timer NOW,
       because the bot just finished speaking and the caller is about to respond.
    """
    print(f"[CALL] Playback ended: {call_sid}")
    session = get_session(call_sid)
    if not session:
        return JSONResponse({"status": "no_session"})

    if not session.greeting_played:
        # Greeting just finished — connect Deepgram and start listening
        session.greeting_played = True
        print(f"[STT] Connecting Deepgram: {call_sid}")
        await connect_deepgram(call_sid, process_turn)

    # (Re)start silence timer — bot has finished speaking, caller should respond now
    if session.silence_timer:
        session.silence_timer.cancel()
    session.silence_timer = asyncio.create_task(_silence_timeout(call_sid))

    return JSONResponse({"status": "ok"})


async def handle_hangup(call_sid: str, payload: dict):
    """Clean up session, Deepgram, and only this call's audio files."""
    print(f"[CALL] Hangup: {call_sid}")
    session = get_session(call_sid)
    if session:
        if session.silence_timer:
            session.silence_timer.cancel()
        # Bug 4 fix: delete only files belonging to this session, not all mp3s
        for filename in session.audio_files:
            path = os.path.join("audio", filename)
            try:
                os.remove(path)
            except OSError:
                pass
    await disconnect_deepgram(call_sid)
    delete_session(call_sid)
    return JSONResponse({"status": "cleaned"})


# ---------------------------------------------------------------------------
# Conversation loop
# ---------------------------------------------------------------------------

async def process_turn(call_sid: str, transcript: str):
    """Called when Deepgram fires speech_final. One full LLM + TTS turn."""
    session = get_session(call_sid)
    if not session:
        return

    # Race condition guard — drop duplicate transcripts while a turn is in progress
    if session.processing:
        print(f"[SKIP] Already processing turn for {call_sid}, dropping: {transcript!r}")
        return
    session.processing = True

    print(f"[CALLER]: {transcript}")

    if session.silence_timer:
        session.silence_timer.cancel()
        session.silence_timer = None

    session.history.append({"role": "user", "content": transcript})
    session.collected = extract_fields_from_text(transcript, session.collected)

    try:
        response_text = await get_llm_response(session.history, session.collected)
        session.history.append({"role": "assistant", "content": response_text})
        print(f"[PRIYA]: {response_text}")

        if is_booking_confirmed(response_text) and session.booking_status != "confirmed":
            session.booking_status = "confirmed"
            fields = await extract_booking_fields(session.history)
            import datetime
            fields["timestamp"] = datetime.datetime.now().strftime("%H:%M:%S")
            session.collected.update(fields)
            print(f"[BOOKING] Confirmed: {fields}")
            asyncio.create_task(append_booking(
                name=fields.get("name")  or "Unknown",
                phone=fields.get("phone") or "Unknown",
                date=fields.get("date")  or "Unknown",
                time=fields.get("time")  or "Unknown",
            ))

        filename = await synthesise(response_text)
        # Track this file so hangup can delete only its own files
        session.audio_files.append(filename)

        ngrok_url = os.getenv("NGROK_URL", "")
        audio_url = f"{ngrok_url}/audio/{filename}"
        telnyx.api_key = os.getenv("TELNYX_API_KEY")
        call = await _telnyx(telnyx.Call.retrieve, call_sid)
        await _telnyx(call.playback_start, audio_url=audio_url)
        # Silence timer is restarted in handle_playback_ended once bot finishes speaking

    except Exception as e:
        print(f"[ERROR] process_turn: {e}")
        try:
            fallback  = "Kripaya ek pal pratiksha karein, main abhi aapki poori sahayata karta hoon."
            filename  = await synthesise(fallback)
            session.audio_files.append(filename)
            audio_url = f"{os.getenv('NGROK_URL', '')}/audio/{filename}"
            telnyx.api_key = os.getenv("TELNYX_API_KEY")
            call = await _telnyx(telnyx.Call.retrieve, call_sid)
            await _telnyx(call.playback_start, audio_url=audio_url)
        except Exception as fe:
            print(f"[ERROR] Fallback TTS failed: {fe}")
    finally:
        session.processing = False


async def _silence_timeout(call_sid: str):
    """After 5s of silence, play the prompt."""
    await asyncio.sleep(5)
    session = get_session(call_sid)
    if not session:
        return
    print(f"[SILENCE] 5s timeout: {call_sid}")
    try:
        filename  = await synthesise(SILENCE_PROMPT)
        session.audio_files.append(filename)
        audio_url = f"{os.getenv('NGROK_URL', '')}/audio/{filename}"
        telnyx.api_key = os.getenv("TELNYX_API_KEY")
        call = await _telnyx(telnyx.Call.retrieve, call_sid)
        await _telnyx(call.playback_start, audio_url=audio_url)
    except Exception as e:
        print(f"[ERROR] Silence prompt: {e}")
