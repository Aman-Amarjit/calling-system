from dotenv import load_dotenv
load_dotenv()  # MUST be first — before any custom imports that read os.getenv() at module level

import asyncio
import glob
import os
import datetime
import json
import uuid
from contextlib import asynccontextmanager

import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

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


def _sanitize_response(response: str, collected: dict) -> str:
    """Remove any phone number the LLM may have hallucinated."""
    import re
    # If LLM invents a phone number not in collected, strip it
    found_phones = re.findall(r'\b[6-9]\d{9}\b', response)
    for phone in found_phones:
        if phone != collected.get("phone"):
            response = response.replace(phone, "aapka number")
            print(f"[SANITIZE] Removed hallucinated phone: {phone}")
    return response

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

    # Set once at startup — avoids mutating a global SDK attribute on every request
    telnyx.api_key = os.getenv("TELNYX_API_KEY")

    # Bottleneck 5: warm up Ollama so the model is already in RAM on first call
    from llm import USE_GROQ, OLLAMA_URL, OLLAMA_MODEL
    if not USE_GROQ:
        import httpx as _httpx
        try:
            async with _httpx.AsyncClient(timeout=60) as c:
                await c.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": OLLAMA_MODEL,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                        "options": {"num_predict": 1, "num_ctx": 512},
                        "keep_alive": "30m",  # keep model in RAM between calls
                    },
                )
            print(f"[OLLAMA] Model '{OLLAMA_MODEL}' warmed up and kept alive for 30m")
        except Exception as e:
            print(f"[WARNING] Ollama warmup failed (server may not be running): {e}")

    yield


app = FastAPI(title="AI Calling Bot Demo", lifespan=lifespan)

_allowed_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
_ngrok = os.getenv("NGROK_URL", "")
if _ngrok:
    _allowed_origins.append(_ngrok)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
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


def _verify_telnyx_signature(body: bytes, signature_b64: str, timestamp: str) -> bool:
    public_key_b64 = os.getenv("TELNYX_PUBLIC_KEY", "")
    if not public_key_b64:
        print("[WEBHOOK] TELNYX_PUBLIC_KEY not set — skipping signature verification")
        return True  # Degrade gracefully if key not configured
    try:
        pub_key_bytes = base64.b64decode(public_key_b64)
        pub_key = Ed25519PublicKey.from_public_bytes(pub_key_bytes)
        message = f"{timestamp}|".encode() + body
        sig_bytes = base64.b64decode(signature_b64)
        pub_key.verify(sig_bytes, message)
        return True
    except (InvalidSignature, Exception):
        return False


@app.post("/webhook/telnyx")
async def webhook_telnyx(request: Request):
    body = await request.body()
    sig   = request.headers.get("telnyx-signature-ed25519", "")
    ts    = request.headers.get("telnyx-timestamp", "")
    if not _verify_telnyx_signature(body, sig, ts):
        print("[WEBHOOK] Signature verification failed")
        return JSONResponse({"error": "invalid signature"}, status_code=403)
    try:
        payload    = json.loads(body)
        event_type = payload["data"]["event_type"]
        call_sid   = payload["data"]["payload"]["call_control_id"]
    except (KeyError, TypeError, ValueError) as e:
        print(f"[WEBHOOK] Malformed payload: {e}")
        return JSONResponse({"error": "malformed payload"}, status_code=400)

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
    """WebSocket endpoint for browser-based calls."""
    print(f"[DEBUG] Incoming WebSocket connection request for session: {session_id}")
    await websocket.accept()
    print(f"[WEB] WebSocket opened: {session_id}")
    session = create_session(session_id)
    session.client_type = "web"
    session.websocket = websocket
    
    greeting = (
        "Namaste! Aapka swagat hai. Main Priya bol rahi hoon appointment centre se. "
        "Ji, main aapki kaise madad kar sakti hoon? "
        "Sabse pehle, kya main aapka naam jaan sakti hoon?"
    )
    try:
        filename = await synthesise(greeting)
        session.audio_files.append(filename)
        audio_url = f"/audio/{filename}"
        await websocket.send_json({"type": "audio", "url": audio_url})
    except Exception as e:
        print(f"[ERROR] Web greeting TTS failed: {e}")

    async def handle_interim_transcript(sid: str, transcript: str):
        if session.client_type == "web" and session.websocket:
            try:
                await session.websocket.send_json({"type": "interim", "text": transcript})
            except:
                pass

    await connect_deepgram(session_id, process_turn, on_interim_callback=handle_interim_transcript, is_web=True)

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
            if message.get("type") == "websocket.disconnect":
                print(f"[WEB] WebSocket disconnected: {session_id}")
                break
            
            if "bytes" in message:
                audio_data = message["bytes"]
                if len(audio_data) > 0:
                    # Print peak volume to verify mic is picking up sound
                    session._audio_chunk_count += 1
                    
                    if session._audio_chunk_count % 50 == 1:
                        count = len(audio_data) // 2
                        if count > 0:
                            shorts = struct.unpack(f"<{count}h", audio_data)
                            peak = max(abs(s) for s in shorts)
                            print(f"[DEBUG] Chunk {session._audio_chunk_count} | Peak: {peak} | Size: {len(audio_data)}")
                
                await send_audio(session_id, audio_data)
                
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
    except Exception as e:
        print(f"[ERROR] web_call_stream: {e}")
    finally:
        print(f"[WEB] Cleaning up session: {session_id}")
        await disconnect_deepgram(session_id)
        if session.silence_timer:
            session.silence_timer.cancel()
        for filename in getattr(session, 'audio_files', []):
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
    call = await _telnyx(telnyx.Call.retrieve, call_sid)
    await _telnyx(call.answer)
    return JSONResponse({"status": "answered"})


async def handle_call_answered(call_sid: str, payload: dict):
    """Synthesise greeting, return TeXML <Play> + <Stream>. Deepgram NOT connected yet."""
    print(f"[CALL] Answered: {call_sid}")
    greeting = (
        "Namaste! Aapka swagat hai. Main Priya bol rahi hoon appointment centre se. "
        "Ji, main aapki kaise madad kar sakti hoon? "
        "Sabse pehle, kya main aapka naam jaan sakti hoon?"
    )
    try:
        filename   = await synthesise(greeting)
        # Track file so it gets cleaned up on hangup
        session = get_session(call_sid)
        if session:
            session.audio_files.append(filename)
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


async def process_turn(call_sid: str, transcript: str):
    """Called when Deepgram fires speech_final. One full LLM + TTS turn."""
    print(f"[PROCESS] Attempting turn for {call_sid}: {transcript!r}")
    session = get_session(call_sid)
    if not session:
        return

    # Cancel silence timer IMMEDIATELY on any speech — before everything else
    if session.silence_timer:
        session.silence_timer.cancel()
        session.silence_timer = None

    # Drop noise / very short transcripts (background sounds, mic pops)
    if len(transcript.strip()) < 3:
        print(f"[STT] Ignoring noise transcript: '{transcript}'")
        return

    async with session.web_turn_lock:
        if session.processing:
            print(f"[SKIP] Already processing turn for {call_sid}, dropping: {transcript!r}")
            return
        session.processing = True
        session.interrupted = False

    print(f"[CALLER]: {transcript}")

    session.history.append({"role": "user", "content": transcript})
    session.collected = extract_fields_from_text(transcript, session.collected)

    try:
        response_text = await get_llm_response(session.history, session.collected)
        response_text = _sanitize_response(response_text, session.collected)
        session.history.append({"role": "assistant", "content": response_text})
        print(f"[PRIYA]: {response_text}")

        if is_booking_confirmed(response_text) and session.booking_status != "confirmed":
            # Hard Python gate — never trust the LLM alone
            c = session.collected
            all_filled = all([c.get("name"), c.get("phone"), c.get("date"), c.get("time")])
            if not all_filled:
                print(f"[WARN] LLM said 'confirmed' but fields incomplete: {c} — overriding")
                session.booking_status = "pending"
            else:
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
    """After 8s of silence, play the prompt — but only if bot is not currently speaking."""
    await asyncio.sleep(8)  # was 5 — give caller more time to respond
    session = get_session(call_sid)
    if not session:
        return
    # Don't interrupt if bot is mid-response
    if session.processing:
        return
    # Skip for web clients (handled by Deepgram endpointing/VAD)
    if session.client_type == "web":
        return
    print(f"[SILENCE] 8s timeout: {call_sid}")
    try:
        filename  = await synthesise(SILENCE_PROMPT)
        session.audio_files.append(filename)
        audio_url = f"{os.getenv('NGROK_URL', '')}/audio/{filename}"
        call = await _telnyx(telnyx.Call.retrieve, call_sid)
        await _telnyx(call.playback_start, audio_url=audio_url)
    except Exception as e:
        print(f"[ERROR] Silence prompt: {e}")

# Mount frontend static files last
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
