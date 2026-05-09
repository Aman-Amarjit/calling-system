import json as json_lib
import os

import httpx
from groq import Groq

# ---------------------------------------------------------------------------
# LLM backend selection
# If GROQ_API_KEY is set → use Groq (fast, cloud, ~200ms)
# Otherwise             → use local Ollama (llama3.2:3b, free, ~1-2s)
# ---------------------------------------------------------------------------
USE_GROQ = bool(os.getenv("GROQ_API_KEY"))
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY")) if USE_GROQ else None
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

SYSTEM_PROMPT = """You are Priya, a senior customer service executive at a professional appointment booking centre.
You speak in polished, courteous Hinglish — formal Hindi mixed naturally with English.

YOUR GOAL: Collect these four details for an appointment booking:
  - Full name
  - Phone number  
  - Preferred date
  - Preferred time

ADAPTIVE BEHAVIOUR — this is critical:

1. LISTEN CAREFULLY to what the caller says.
   If they give you multiple details in one message, acknowledge ALL of them and only ask for what is still missing.
   Example: Caller says "Mera naam Rahul hai aur 15 June ko chahiye" → you have name + date, so ask for phone number next.

2. TRACK WHAT YOU HAVE. Before each reply, mentally check:
   - Do I have name? → if no, ask for it
   - Do I have phone? → if no, ask for it  
   - Do I have date? → if no, ask for it
   - Do I have time? → if no, ask for it
   Ask only for the FIRST missing field.

3. ACKNOWLEDGE NATURALLY — match the caller's energy:
   - If they are formal → stay formal
   - If they are casual → be warm and friendly
   - If they seem confused → be extra patient and clear
   - If they give a wrong format (e.g. unclear date) → politely ask to clarify that specific field only

4. HANDLE UNEXPECTED RESPONSES:
   - If caller asks a question → answer it briefly, then continue collecting
   - If caller says something off-topic → gently redirect: "Zaroor ji, aur kya main aapki appointment book kar sakti hoon?"
   - If caller gives an unclear answer → ask only about that unclear part, do not restart

5. CONFIRMATION STEP:
   Once all four fields are collected, read them back naturally:
   "Toh main confirm karta hoon — naam [name], number [phone], date [date], aur time [time]. Sahi hai?"

6. CLOSING — EXACT PHRASE REQUIRED:
   When caller confirms, say:
   "Bahut shukriya [name] ji. Aapki appointment booking confirmed ho gayi hai. Aapko jald hi confirmation milega."
   The words "booking confirmed" MUST appear. No exceptions.

7. Keep every response under 35 words.
8. Never say you are an AI or a bot.
9. If you did not understand: "Maafi chahiye, kya aap dobara bata sakte hain?"
"""


async def _call_groq(messages: list[dict], temperature: float, max_tokens: int) -> str:
    response = groq_client.chat.completions.create(
        model="llama3-8b-8192",
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


async def _call_ollama(messages: list[dict], temperature: float, max_tokens: int) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()


async def get_llm_response(history: list[dict]) -> str:
    """Call LLM (Groq or Ollama) with full conversation history. Returns assistant reply."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    if USE_GROQ:
        return await _call_groq(messages, temperature=0.7, max_tokens=150)
    return await _call_ollama(messages, temperature=0.7, max_tokens=150)


def is_booking_confirmed(text: str) -> bool:
    """Check if the bot's response contains the booking confirmation trigger phrase."""
    return "booking confirmed" in text.lower()


async def extract_booking_fields(history: list[dict]) -> dict:
    """Extract structured booking fields from conversation history via LLM."""
    extraction_prompt = (
        "Based on the conversation above, extract the booking details. "
        "Return ONLY a JSON object with exactly these keys: name, phone, date, time. "
        "Use null for any field not mentioned. "
        'Example: {"name": "Rahul", "phone": "9876543210", "date": "15 June", "time": "3 PM"} '
        "Return only the JSON, nothing else."
    )
    messages = history + [{"role": "user", "content": extraction_prompt}]

    if USE_GROQ:
        text = await _call_groq(messages, temperature=0, max_tokens=100)
    else:
        text = await _call_ollama(messages, temperature=0, max_tokens=100)

    try:
        if "```" in text:
            text = text.split("```")[1].replace("json", "").strip()
        return json_lib.loads(text)
    except Exception:
        return {"name": None, "phone": None, "date": None, "time": None}
