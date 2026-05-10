import asyncio
import os

from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

deepgram_client = DeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))

# Store active Deepgram connections keyed by call_sid
deepgram_connections: dict = {}

SILENCE_PROMPT = (
    "Ji, aapki awaaz nahi aayi — koi baat nahi, "
    "aap dobara bol sakte hain, "
    "main aapki poori sahayata ke liye yahan hoon."
)


async def connect_deepgram(call_sid: str, on_transcript_callback):
    """
    Open a Deepgram live transcription connection for this call.
    on_transcript_callback(call_sid, transcript) is called when speech_final fires.
    Returns the Deepgram connection object.
    """
    connection = deepgram_client.listen.live.v("1")

    options = LiveOptions(
        model="nova-2",
        language="hi",
        detect_language=False,
        encoding="mulaw",
        sample_rate=8000,
        endpointing=300,
        interim_results=False,
    )

    def on_message(self, result, **kwargs):
        try:
            transcript = result.channel.alternatives[0].transcript
            if result.speech_final and transcript.strip():
                # Deepgram fires this callback from a background thread.
                # asyncio.create_task() requires the event loop thread — use run_coroutine_threadsafe.
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(
                    on_transcript_callback(call_sid, transcript), loop
                )
        except Exception as e:
            print(f"[STT] Error processing transcript: {e}")

    connection.on(LiveTranscriptionEvents.Transcript, on_message)

    await connection.start(options)
    deepgram_connections[call_sid] = connection
    print(f"[STT] Deepgram connected for call: {call_sid}")
    return connection


async def send_audio(call_sid: str, audio_bytes: bytes):
    """Forward raw audio bytes to the Deepgram connection for this call."""
    conn = deepgram_connections.get(call_sid)
    if conn:
        try:
            conn.send(audio_bytes)
        except Exception as e:
            print(f"[STT] send_audio error for {call_sid}: {e}")


async def disconnect_deepgram(call_sid: str):
    """Close and remove the Deepgram connection for this call."""
    conn = deepgram_connections.pop(call_sid, None)
    if conn:
        await conn.finish()
        print(f"[STT] Deepgram disconnected for call: {call_sid}")
