from dotenv import load_dotenv
load_dotenv()  # MUST be first — before any custom imports that read os.getenv() at module level

import asyncio
import glob
import os
import datetime
import json
import uuid
from contextlib import asynccontextmanager

import telnyx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

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
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
]

OPTIONAL_ENV_VARS = [
    "TELNYX_API_KEY",
    "TELNYX_PUBLIC_KEY",
    "NGROK_URL",
    "GOOGLE_SHEET_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    for var in REQUIRED_ENV_VARS:
        if not os.getenv(var):
            raise RuntimeError(f"Missing required env var for Web-to-Bot: {var}")
            
    for var in OPTIONAL_ENV_VARS:
        if not os.getenv(var):
            print(f"[WARNING] Missing {var}. Telnyx calls will not work, but Web-to-Bot will function.")
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
    # Find the most recently confirmed booking by timestamp (not just the first one found)
    latest = None
    latest_ts = ""
    for s in sessions.values():
        if s.booking_status == "confirmed" and s.collected.get("name"):
            ts = s.collected.get("timestamp", "")
            if ts > latest_ts:
                latest_ts = ts
                c = s.collected
                latest = {
                    "timestamp": ts,
                    "name":      c.get("name", ""),
                    "phone":     c.get("phone", ""),
                    "date":      c.get("date", ""),
                    "time":      c.get("time", ""),
                }
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


@app.websocket("/web-call/{session_id}")
async def web_call_stream(websocket: WebSocket, session_id: str):
    """Receive audio and control messages from web clients."""
    await websocket.accept()
    print(f"[WEB] WebSocket opened: {session_id}")
    session = create_session(session_id)
    session.client_type = "web"
    session.websocket = websocket
    
    greeting = (
        "Namaste Sir, aapka swagat hai hamare appointment centre mein. "
        "Main Priya bol rahi hoon — aapki kaise sahayata kar sakti hoon? "
        "Kripaya apna poora naam batayein."
    )
    try:
        filename = await synthesise(greeting)
        session.audio_files.append(filename)
        audio_url = f"/audio/{filename}"
        await websocket.send_json({"type": "audio", "url": audio_url})
    except Exception as e:
        print(f"[ERROR] Web greeting TTS failed: {e}")

    await connect_deepgram(session_id, process_turn, is_web=True)

    import struct
    def get_rms(audio_bytes):
        if not audio_bytes: return 0
        count = len(audio_bytes) // 2
        shorts = struct.unpack(f"<{count}h", audio_bytes)
        sum_sq = sum(s*s for s in shorts)
        return (sum_sq / count)**0.5 / 32768.0

    try:
        while True:
            message = await websocket.receive()
            
            if "bytes" in message:
                audio_data = message["bytes"]
                rms = get_rms(audio_data)
                
                # Forward actual audio only if it's loud enough to be speech
                # Otherwise send silence to Deepgram to prevent noise from triggering turns
                if rms > 0.04:
                    await send_audio(session_id, audio_data)
                else:
                    await send_audio(session_id, b"\x00" * len(audio_data))
                
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    msg_type = data.get("type")
                    if msg_type == "interrupt":
                        session.interrupted = True
                        session.greeting_played = True
                        print(f"[WEB] Interrupted: {session_id}")
                    elif msg_type == "greeting_ended":
                        session.greeting_played = True
                        print(f"[WEB] Greeting ended: {session_id}")
                except Exception as e:
                    print(f"[ERROR] Web JSON parse failed: {e}")
    except WebSocketDisconnect:
        print(f"[WEB] WebSocket closed: {session_id}")
        await disconnect_deepgram(session_id)
        if session.silence_timer:
            session.silence_timer.cancel()
        for filename in session.audio_files:
            path = os.path.join("audio", filename)
            try:
                os.remove(path)
            except OSError:
                pass
        delete_session(session_id)


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
        # Greeting just finished — start media streaming, connect Deepgram, start listening
        session.greeting_played = True
        print(f"[STT] Greeting done, starting media stream + Deepgram: {call_sid}")
        # Tell Telnyx to start streaming caller audio to our WebSocket
        ngrok_url    = os.getenv("NGROK_URL", "")
        ngrok_domain = ngrok_url.replace("https://", "").replace("http://", "")
        telnyx.api_key = os.getenv("TELNYX_API_KEY")
        call = await _telnyx(telnyx.Call.retrieve, call_sid)
        await _telnyx(
            call.streaming_start,
            stream_url=f"wss://{ngrok_domain}/media/{call_sid}",
            stream_track="inbound_track",
        )
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

async def _append_booking_logged(**kwargs):
    """Wrapper so Sheets write failures are logged, not silently swallowed."""
    try:
        await append_booking(**kwargs)
    except Exception as e:
        print(f"[ERROR] Google Sheets write failed: {e}")


async def process_turn(call_sid: str, transcript: str):
    """Called when Deepgram fires speech_final. One full LLM + TTS turn."""
    print(f"[PROCESS] Attempting turn for {call_sid}: {transcript!r}")
    session = get_session(call_sid)
    if not session:
        return

    async with session.web_turn_lock:
        if session.processing:
            print(f"[SKIP] Already processing turn for {call_sid}, dropping: {transcript!r}")
            return
        session.processing = True
        session.interrupted = False

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
            fields["timestamp"] = datetime.datetime.now().strftime("%H:%M:%S")
            session.collected.update(fields)
            print(f"[BOOKING] Confirmed: {fields}")
            asyncio.create_task(_append_booking_logged(
                name=fields.get("name")  or "Unknown",
                phone=fields.get("phone") or "Unknown",
                date=fields.get("date")  or "Unknown",
                time=fields.get("time")  or "Unknown",
            ))

        filename = await synthesise(response_text)
        session.audio_files.append(filename)

        if session.client_type == "web":
            # For web, use a relative path so it works on localhost or ngrok automatically
            audio_url = f"/audio/{filename}"
            if session.interrupted:
                print(f"[WEB] Turn interrupted, dropping audio: {audio_url}")
            else:
                try:
                    await session.websocket.send_json({"type": "audio", "url": audio_url})
                    print(f"[WEB] Audio sent to client: {audio_url}")
                except Exception as e:
                    print(f"[ERROR] Web audio send failed: {e}")
        else:
            # For telephony, Telnyx REQUIRES an absolute public URL
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
            if session.client_type == "web":
                if not session.interrupted:
                    await session.websocket.send_json({"type": "audio", "url": audio_url})
            else:
                telnyx.api_key = os.getenv("TELNYX_API_KEY")
                call = await _telnyx(telnyx.Call.retrieve, call_sid)
                await _telnyx(call.playback_start, audio_url=audio_url)
        except Exception as fe:
            print(f"[ERROR] Fallback TTS failed: {fe}")
    finally:
        session.processing = False


async def _append_booking_logged(name: str, phone: str, date: str, time: str):
    """Wrapper for sheets.append_booking with logging."""
    try:
        from sheets import append_booking
        await append_booking(name, phone, date, time)
    except Exception as e:
        print(f"[ERROR] Sheets logging failed: {e}")


async def _silence_timeout(call_sid: str):
    """After 5s of silence, play the prompt — but only if bot is not currently speaking."""
    await asyncio.sleep(5)
    session = get_session(call_sid)
    if not session:
        return
    # Don't interrupt if bot is mid-response
    if session.processing:
        return
    # Skip for web clients (handled by Deepgram endpointing/VAD)
    if session.client_type == "web":
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

# Mount frontend static files last
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
