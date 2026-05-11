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
    \"\"\"
    Pre-process text with SSML-like cues that ElevenLabs honours.
    Adds natural pauses and pacing changes at key conversational moments.
    \"\"\"
    # Brief pause after greetings
    text = text.replace("Namaste", "Namaste <break time='0.5s'/>")
    text = text.replace("Hello", "Hello <break time='0.3s'/>")
    
    # Natural fillers - add slight hesitation
    # We use regex to match whole words and avoid double-replacing
    import re
    fillers = {
        "achha": "achha <break time='0.3s'/>",
        "theek hai": "theek hai <break time='0.4s'/>",
        "toh": "toh <break time='0.2s'/>",
        "waise": "waise <break time='0.3s'/>",
        "ji": "ji <break time='0.2s'/>",
        "um": "<break time='0.2s'/> um <break time='0.3s'/>",
    }
    for word, replacement in fillers.items():
        pattern = re.compile(rf'\b{re.escape(word)}\b', re.IGNORECASE)
        text = pattern.sub(replacement, text)

    # Slow down for the most important confirmation part
    text = text.replace(
        "confirm ho gayi hai",
        "<prosody rate='85%'>confirm ho gayi hai</prosody>",
    )
    text = text.replace(
        "booking confirmed",
        "<prosody rate='85%'>booking confirmed</prosody>",
    )

    # Add pauses at sentence boundaries if not already there
    text = text.replace("!", "! <break time='0.4s'/>")
    text = text.replace("?", "? <break time='0.5s'/>")
    
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
