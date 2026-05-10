import asyncio
import os
from uuid import uuid4

import aiofiles
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings

# Ensure audio directory exists at module load time
os.makedirs("audio", exist_ok=True)

# Lazy-initialized: avoids reading env vars at import time (before load_dotenv runs)
_client: ElevenLabs | None = None


def _get_client() -> ElevenLabs:
    """Return (and lazily create) the ElevenLabs client."""
    global _client
    if _client is None:
        _client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    return _client


def _add_emotion_markup(text: str) -> str:
    """
    Pre-process text with SSML-like cues that ElevenLabs honours.
    Adds natural pauses and pacing changes at key conversational moments.
    """
    # Brief pause after the greeting — feels more human, less machine-gun
    text = text.replace("Namaste", "Namaste <break time='0.4s'/>")
    # Slow down and let the confirmation land — most important moment in the call
    text = text.replace(
        "booking confirmed",
        "<prosody rate='90%'>booking confirmed</prosody>",
    )
    # Short breath before asking a question — avoids running sentences together
    for filler in ("Kripaya", "Ab kripaya", "Aur kripaya"):
        text = text.replace(filler, f"<break time='0.2s'/>{filler}")
    return text


async def synthesise(text: str) -> str:
    """
    Convert text to speech using ElevenLabs.
    Both the API call and file write are non-blocking.
    Returns the filename (not full URL).
    """
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    loop = asyncio.get_running_loop()
    marked_text = _add_emotion_markup(text)

    print(f"[TTS] Synthesising: {text[:50]}...")
    audio_bytes = await loop.run_in_executor(
        None,
        lambda: b"".join(_get_client().generate(
            text=marked_text,
            voice=voice_id,
            model="eleven_multilingual_v2",  # supports Hinglish
            voice_settings=VoiceSettings(
                stability=0.35,         # Lower = more expressive, varied delivery
                similarity_boost=0.80,  # Stays true to the chosen voice
                style=0.55,             # Emotional style intensity (0–1)
                use_speaker_boost=True, # Cleaner, more presence on the line
            ),
        )),
    )

    filename = f"{uuid4()}.mp3"
    path = f"audio/{filename}"

    # Non-blocking file write
    async with aiofiles.open(path, "wb") as f:
        await f.write(audio_bytes)

    print(f"[TTS] Saved to {filename}")
    return filename
