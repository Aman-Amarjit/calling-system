import asyncio
import glob
import os
from contextlib import asynccontextmanager

import telnyx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from llm import extract_booking_fields, get_llm_response, is_booking_confirmed
from sheets import append_booking
from session import create_session, delete_session, get_session
from stt import SILENCE_PROMPT, connect_deepgram, disconnect_deepgram, send_audio
from tts import synthesise

load_dotenv()

REQUIRED_ENV_VARS = [
    "TELNYX_API_KEY",
    "TELNYX_PUBLIC_KEY",
    "DEEPGRAM_API_KEY",
    "GROQ_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate required env vars
    for var in REQUIRED_ENV_VARS:
        if not os.getenv(var):
            raise RuntimeError(f"Missing required env var: {var}")
    yield
    # Shutdown: nothing to clean up at app level


app = FastAPI(title="AI Calling Bot Demo", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def stats():
    """Live stats for the frontend monitor."""
    from session import sessions
    latest = None
    # Find the most recently confirmed booking across all sessions
    for s in sessions.values():
        if s.booking_status == "confirmed" and s.collected.get("name"):
            c = s.collected
            latest = {
                "timestamp": c.get("timestamp", ""),
                "name": c.get("name", ""),
                "phone": c.get("phone", ""),
                "date": c.get("date", ""),
                "time": c.get("time", ""),
            }
            break
    return {
        "active_calls": len(sessions),
        "latest_booking": latest,
    }


@app.post("/webhook/telnyx")
async def webhook_telnyx(request: Request):
    payload = await request.json()

    event_type = payload["data"]["event_type"]
    call_sid = payload["data"]["payload"]["call_control_id"]

    print(f"[WEBHOOK] event_type={event_type} call_sid={call_sid}")

    if event_type == "call.initiated":
        return await handle_call_initiated(call_sid, payload)
    elif event_type == "call.answered":
        return await handle_call_answered(call_sid, payload)
    elif event_type == "call.playback.ended":
        return await handle_playback_ended(call_sid, payload)
    elif event_type == "call.hangup":
        return await handle_hangup(call_sid, payload)
    else:
        print(f"[WEBHOOK] Ignoring unhandled event: {event_type}")
        return JSONResponse({"status": "ignored"})


async def handle_call_initiated(call_sid: str, payload: dict):
    """Create session and answer the call via Telnyx API."""
    create_session(call_sid)
    print(f"[CALL] Initiated: {call_sid}")

    telnyx.api_key = os.getenv("TELNYX_API_KEY")
    call = telnyx.Call.retrieve(call_sid)
    call.answer()

    return JSONResponse({"status": "answered"})


async def handle_call_answered(call_sid: str, payload: dict):
    """Synthesise greeting and return TeXML <Play>. Deepgram NOT connected yet."""
    print(f"[CALL] Answered: {call_sid}")

    greeting = (
        "Namaste Sir, aapka swagat hai hamare appointment centre mein. "
        "Main Priya bol rahi hoon — aapki kaise sahayata kar sakti hoon? "
        "Kripaya apna poora naam batayein."
    )

    try:
        filename = await synthesise(greeting)
        ngrok_url = os.getenv("NGROK_URL", "")
        audio_url = f"{ngrok_url}/audio/{filename}"
        print(f"[TTS] Greeting audio: {audio_url}")

        texml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Play>{audio_url}</Play>
</Response>'''
        return PlainTextResponse(texml, media_type="text/xml")

    except Exception as e:
        print(f"[ERROR] TTS failed in greeting: {e}")
        # Fallback: empty response so call doesn't drop
        return PlainTextResponse(
            '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="text/xml",
        )


async def handle_playback_ended(call_sid: str, payload: dict):
    """Connect Deepgram only after the greeting finishes playing."""
    print(f"[CALL] Playback ended: {call_sid}")
    session = get_session(call_sid)
    if not session:
        return JSONResponse({"status": "no_session"})

    if not session.greeting_played:
        # This is the greeting ending — NOW connect Deepgram and start listening
        session.greeting_played = True
        print(f"[STT] Greeting done, connecting Deepgram for call: {call_sid}")
        await connect_deepgram(call_sid, process_turn)
        # Start silence timer
        session.silence_timer = asyncio.create_task(_silence_timeout(call_sid))
    # else: subsequent bot responses ending — Deepgram already connected, do nothing

    return JSONResponse({"status": "ok"})


async def handle_hangup(call_sid: str, payload: dict):
    """Clean up session, Deepgram connection, and temp audio files."""
    print(f"[CALL] Hangup: {call_sid}")

    session = get_session(call_sid)
    if session and session.silence_timer:
        session.silence_timer.cancel()

    await disconnect_deepgram(call_sid)
    delete_session(call_sid)

    for f in glob.glob("audio/*.mp3"):
        try:
            os.remove(f)
        except OSError:
            pass

    return JSONResponse({"status": "cleaned"})


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    path = f"audio/{filename}"
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="audio/mpeg")


@app.websocket("/media/{call_sid}")
async def media_stream(websocket: WebSocket, call_sid: str):
    """Receive raw audio from Telnyx and forward to Deepgram."""
    await websocket.accept()
    print(f"[MEDIA] WebSocket opened for call: {call_sid}")
    try:
        while True:
            data = await websocket.receive_bytes()
            await send_audio(call_sid, data)
    except WebSocketDisconnect:
        print(f"[MEDIA] WebSocket closed for call: {call_sid}")
        await disconnect_deepgram(call_sid)


async def process_turn(call_sid: str, transcript: str):
    """Called when Deepgram fires speech_final. Runs one full LLM + TTS turn."""
    session = get_session(call_sid)
    if not session:
        return

    print(f"[CALLER]: {transcript}")

    # Cancel silence timer — caller just spoke
    if session.silence_timer:
        session.silence_timer.cancel()
        session.silence_timer = None

    # Add caller's message to history
    session.history.append({"role": "user", "content": transcript})

    try:
        # Get LLM response
        response_text = await get_llm_response(session.history)
        session.history.append({"role": "assistant", "content": response_text})
        print(f"[PRIYA]: {response_text}")

        # Check for booking confirmation trigger
        if is_booking_confirmed(response_text) and session.booking_status != "confirmed":
            session.booking_status = "confirmed"
            # Extract structured fields from conversation history via LLM
            fields = await extract_booking_fields(session.history)
            fields["timestamp"] = __import__("datetime").datetime.now().strftime("%H:%M:%S")
            session.collected.update(fields)
            print(f"[BOOKING] Confirmed for call: {call_sid} | Fields: {fields}")
            # Write to Google Sheets — non-blocking, fires concurrently
            asyncio.create_task(
                append_booking(
                    name=fields.get("name") or "Unknown",
                    phone=fields.get("phone") or "Unknown",
                    date=fields.get("date") or "Unknown",
                    time=fields.get("time") or "Unknown",
                )
            )

        # Synthesise and play response
        filename = await synthesise(response_text)
        ngrok_url = os.getenv("NGROK_URL", "")
        audio_url = f"{ngrok_url}/audio/{filename}"

        # Play audio back via Telnyx
        telnyx.api_key = os.getenv("TELNYX_API_KEY")
        call = telnyx.Call.retrieve(call_sid)
        call.playback_start(audio_url=audio_url)

        # Restart silence timer
        session.silence_timer = asyncio.create_task(
            _silence_timeout(call_sid)
        )

    except Exception as e:
        print(f"[ERROR] process_turn failed: {e}")
        # Fallback: play a polite hold message
        try:
            fallback = "Kripaya ek pal pratiksha karein, main abhi aapki poori sahayata karta hoon."
            filename = await synthesise(fallback)
            ngrok_url = os.getenv("NGROK_URL", "")
            audio_url = f"{ngrok_url}/audio/{filename}"
            telnyx.api_key = os.getenv("TELNYX_API_KEY")
            call = telnyx.Call.retrieve(call_sid)
            call.playback_start(audio_url=audio_url)
        except Exception as fallback_err:
            print(f"[ERROR] Fallback TTS also failed: {fallback_err}")


async def _silence_timeout(call_sid: str):
    """Wait 5 seconds; if no speech, play the silence prompt."""
    await asyncio.sleep(5)
    session = get_session(call_sid)
    if not session:
        return
    print(f"[SILENCE] No speech for 5s on call: {call_sid}")
    try:
        filename = await synthesise(SILENCE_PROMPT)
        ngrok_url = os.getenv("NGROK_URL", "")
        audio_url = f"{ngrok_url}/audio/{filename}"
        telnyx.api_key = os.getenv("TELNYX_API_KEY")
        call = telnyx.Call.retrieve(call_sid)
        call.playback_start(audio_url=audio_url)
    except Exception as e:
        print(f"[ERROR] Silence prompt failed: {e}")
