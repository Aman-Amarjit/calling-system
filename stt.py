import asyncio
import os

from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

deepgram_client = DeepgramClient(api_key=str(os.getenv("DEEPGRAM_API_KEY", "")))

# Store active Deepgram connections keyed by call_sid
deepgram_connections: dict = {}

SILENCE_PROMPT = (
    "Ji, aapki awaaz nahi aayi — koi baat nahi, "
    "aap dobara bol sakte hain, "
    "main aapki poori sahayata ke liye yahan hoon."
)


async def connect_deepgram(call_sid: str, on_transcript_callback, on_interim_callback=None, is_web: bool = False):
    """
    Open a Deepgram live transcription connection for this call.
    on_transcript_callback(call_sid, transcript) is called when speech_final fires.
    on_interim_callback(call_sid, transcript) is called for interim results.
    Returns the Deepgram connection object.
    """
    connection = deepgram_client.listen.live.v("1")

    if is_web:
        options = LiveOptions(
            model="nova-2",
            language="hi",
            encoding="linear16",
            sample_rate=48000,
            channels=1,
            endpointing=500, # type: ignore
            utterance_end_ms=1000, # type: ignore
            interim_results=True,
        )
    else:
        options = LiveOptions(
            model="nova-2",
            language="hi",
            encoding="mulaw",
            sample_rate=8000,
            endpointing=500, # type: ignore
            utterance_end_ms=1000, # type: ignore
            interim_results=True,
        )

    main_loop = asyncio.get_running_loop()
    # Track the latest transcript to handle UtteranceEnd as a fallback for speech_final
    last_transcript = ""

    def on_message(self, result, **kwargs):
        nonlocal last_transcript
        try:
            transcript = result.channel.alternatives[0].transcript
            if not transcript.strip():
                return
            
            last_transcript = transcript

            if result.speech_final:
                print(f"[STT FINAL] '{transcript}'")
                last_transcript = "" # Reset
                asyncio.run_coroutine_threadsafe(
                    on_transcript_callback(call_sid, transcript), main_loop
                )
            elif on_interim_callback:
                print(f"[STT INTERIM] '{transcript}'")
                asyncio.run_coroutine_threadsafe(
                    on_interim_callback(call_sid, transcript), main_loop
                )
        except Exception as e:
            print(f"[STT] Error processing transcript: {e}")

    def on_utterance_end(self, *args, **kwargs):
        """Fires when Deepgram is fully confident the utterance is complete."""
        nonlocal last_transcript
        print(f"[STT] Utterance end confirmed for {call_sid}")
        if last_transcript.strip():
            print(f"[STT FALLBACK] Triggering final from UtteranceEnd: '{last_transcript}'")
            transcript_to_send = last_transcript
            last_transcript = "" # Prevent double trigger
            asyncio.run_coroutine_threadsafe(
                on_transcript_callback(call_sid, transcript_to_send), main_loop
            )

    def on_error(self, error, **kwargs):
        print(f"[STT] Deepgram error for {call_sid}: {error}")

    connection.on(LiveTranscriptionEvents.Transcript, on_message)
    connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
    connection.on(LiveTranscriptionEvents.Error, on_error)

    connection.start(options, keep_alive=True)
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
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, conn.finish)
        print(f"[STT] Deepgram disconnected for call: {call_sid}")
