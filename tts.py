import asyncio
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
    Both the API call and file write are non-blocking.
    Returns the filename (not full URL).
    """
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    loop = asyncio.get_running_loop()

    print(f"[TTS] Synthesising: {text[:50]}...")
    audio_bytes = await loop.run_in_executor(
        None,
        lambda: b"".join(client.generate(
            text=text,
            voice=voice_id,
            model="eleven_multilingual_v2",  # supports Hinglish
        )),
    )

    filename = f"{uuid4()}.mp3"
    path = f"audio/{filename}"

    # Non-blocking file write
    async with aiofiles.open(path, "wb") as f:
        await f.write(audio_bytes)

    print(f"[TTS] Saved to {filename}")
    return filename
