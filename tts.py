import os
from uuid import uuid4

import aiofiles
from elevenlabs.client import ElevenLabs

# Ensure audio directory exists at module load time
os.makedirs("audio", exist_ok=True)

client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))


async def synthesise(text: str) -> str:
    """
    Convert text to speech using ElevenLabs.
    Saves audio to audio/{uuid}.mp3 using non-blocking aiofiles.
    Returns the filename (not full URL).
    """
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")

    # generate() returns a generator of audio chunks
    audio_generator = client.generate(
        text=text,
        voice=voice_id,
        model="eleven_multilingual_v2",  # supports Hinglish
    )

    # Collect all chunks into bytes
    audio_bytes = b"".join(audio_generator)

    filename = f"{uuid4()}.mp3"
    path = f"audio/{filename}"

    # Non-blocking file write
    async with aiofiles.open(path, "wb") as f:
        await f.write(audio_bytes)

    return filename
