import json as json_lib
import os
import re

import httpx
from groq import AsyncGroq

# ---------------------------------------------------------------------------
# LLM backend selection
# If GROQ_API_KEY is set → use Groq (fast, cloud, ~200ms)
# Otherwise             → use local Ollama (llama3.2:3b, free, ~3-5s on CPU)
# ---------------------------------------------------------------------------
USE_GROQ = bool(os.getenv("GROQ_API_KEY"))
groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY")) if USE_GROQ else None
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

# ---------------------------------------------------------------------------
# FIELD EXTRACTION — done in Python, not by the LLM
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r'\b[6-9]\d{9}\b')

_MONTHS = [
    "january","february","march","april","may","june",
    "july","august","september","october","november","december",
    "jan","feb","mar","apr","jun","jul","aug","sep","oct","nov","dec",
]


def extract_fields_from_text(text: str, existing: dict) -> dict:
    """
    Extract booking fields from a single user message using regex + heuristics.
    Only updates fields that are still None in existing.
    Returns updated dict.
    """
    collected = dict(existing)
    print(f"[EXTRACTOR] Input: '{text}' | Before: {existing}")
    lower = text.lower()
    words = lower.split()

    # --- Phone number ---
    if collected["phone"] is None:
        m = _PHONE_RE.search(text)
        if m:
            collected["phone"] = m.group()

    # --- Name ---
    if collected["name"] is None:
        name_patterns = [
            # "mera naam Rahul hai"
            r"(?:mera|meri|my|main|mai)\s+naam\s+([A-Za-z]+(?:\s+[A-Za-z]+?)?)\s*(?:\bhai\b|\bhe\b|\bhoon\b|\bhun\b|\bh\b|$)",
            # "naam Rahul" or "naam hai Rahul"
            r"\bnaam\s+(?:hai\s+)?([A-Za-z]{2,})\b",
            # "I am Rahul" / "I'm Rahul" / "myself Rahul"
            r"(?:i am|i'm|myself)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)",
            # "Rahul bol raha hoon" / "Rahul speaking"
            r"([A-Za-z]{3,}(?:\s+[A-Za-z]+)?)\s+(?:bol|speaking|here|bolta|bolti)",
            # "Rahul hoon" / "Rahul hun"
            r"([A-Za-z]{2,}(?:\s+[A-Za-z]+)?)\s+(?:hoon|hun)\b",
            # "Rahul hai naam mera"
            r"([A-Za-z]{2,}(?:\s+[A-Za-z]+)?)\s+hai\s+naam",
            # bare name — only as last resort (single word reply to 'apna naam batayein')
            r"^([A-Za-z]{2,}(?:\s+[A-Za-z]+)?)$",
        ]
        _STOP_WORDS = {
            "hello","hi","haan","nahi","okay","ok","yes","no","sir","madam",
            "hai","he","hoon","hun","h","aur","or","aap","main","mera","meri",
            "naam","number","phone","date","time","appointment","booking",
            "kal","aaj","parso","subah","shaam","raat","dopahar",
        }
        for pat in name_patterns:
            m = re.search(pat, lower)
            if m:
                name = m.group(1).strip().title()
                name_words = name.lower().split()
                if not any(w in _STOP_WORDS for w in name_words) and len(name_words[0]) >= 2:
                    collected["name"] = name
                    break

    # --- Date ---
    if collected["date"] is None:
        # Check for month name — use word boundary to avoid "market" matching "mar"
        for month in _MONTHS:
            # "may" is also a common English auxiliary verb ("I may come tomorrow").
            # Only treat it as a month when it appears next to a digit.
            if month == "may":
                if not re.search(r'(\d{1,2}\s*may|may\s*\d{1,2})', lower):
                    continue
            if re.search(r'\b' + month + r'\b', lower):
                m = re.search(r'(\d{1,2})\s*' + month, lower)
                if m:
                    collected["date"] = f"{m.group(1)} {month.capitalize()}"
                else:
                    m = re.search(month + r'\s*(\d{1,2})', lower)
                    if m:
                        collected["date"] = f"{m.group(1)} {month.capitalize()}"
                    else:
                        collected["date"] = month.capitalize()
                break
        # Check for relative dates
        if collected["date"] is None:
            for word in ["aaj", "kal", "parso", "agle hafte", "next week"]:
                if word in lower:
                    collected["date"] = word
                    break
        # Check for day names
        if collected["date"] is None:
            for day in ["monday","tuesday","wednesday","thursday","friday","saturday","sunday",
                        "somvar","mangalvar","budhvar","guruvar","shukravar","shanivar","ravivar"]:
                if day in lower:
                    collected["date"] = day.capitalize()
                    break

    # --- Time ---
    if collected["time"] is None:
        # Pattern: "X baje", "X am/pm", "shaam X", "subah X"
        time_patterns = [
            r'(\d{1,2}(?::\d{2})?)\s*(?:baje|am|pm|AM|PM)',
            r'(?:shaam|subah|dopahar|raat|evening|morning|afternoon|night)\s+(\d{1,2}(?::\d{2})?)\s*(?:baje|am|pm)?',
            r'(\d{1,2}(?::\d{2})?)\s*(?:baje|bajey)',
        ]
        for pat in time_patterns:
            m = re.search(pat, lower)
            if m:
                collected["time"] = m.group(0).strip()
                break
        # Hindi number + baje
        if collected["time"] is None:
            hindi_nums = {"ek":"1","do":"2","teen":"3","chaar":"4","paanch":"5",
                          "chhe":"6","saat":"7","aath":"8","nau":"9","das":"10",
                          "gyarah":"11","barah":"12"}
            for hindi, num in hindi_nums.items():
                if hindi in lower and ("baje" in lower or "am" in lower or "pm" in lower):
                    # Find context around it
                    m = re.search(rf'(?:shaam|subah|dopahar|raat|evening|morning)?\s*{hindi}\s*(?:baje|am|pm)?', lower)
                    if m:
                        collected["time"] = m.group(0).strip().replace(hindi, f"{num}")
                    else:
                        collected["time"] = f"{num} baje"
                    break

    print(f"[EXTRACTOR] After:  {collected}")
    return collected


def get_next_question(collected: dict) -> str | None:
    """Return the next question to ask based on what's missing. None if all collected."""
    if collected["name"] is None:
        return "name"
    if collected["phone"] is None:
        return "phone"
    if collected["date"] is None:
        return "date"
    if collected["time"] is None:
        return "time"
    return None  # all collected


# ---------------------------------------------------------------------------
# SYSTEM PROMPT — kept minimal so small models can follow it
# State tracking is done in Python above, not by the LLM
# ---------------------------------------------------------------------------

def build_system_prompt(collected: dict, next_field: str | None) -> str:
    \"\"\"Build a warm, natural conversational prompt for a Hinglish booking assistant.\"\"\"
    known = {k: v for k, v in collected.items() if v is not None}
    known_str = ", ".join(f"{k}={v}" for k, v in known.items()) or "nothing yet"

    if next_field is not None:
        base = f\"\"\"You are Priya, a friendly and natural-sounding booking assistant.
Your goal is to be helpful and warm, like a real person on the phone.

CONFIRMED FACTS (don't repeat these):
{known_str}

YOUR TASK: Ask for the '{next_field}' in a natural way.

CONVERSATIONAL GUIDELINES:
- Use Hinglish (Hindi + English) naturally.
- Use filler words like "achha", "theek hai", "toh", "waise" or "um" to sound human.
- Keep it concise but NOT robotic. Acknowledge what the user said before asking the next thing.
- Never ask for more than one thing at a time.
- If the caller says something emotional or off-topic, acknowledge it briefly with "achha" or "bilkul" before redirecting.

FEW-SHOT EXAMPLES:
User: "Mera naam Rahul hai"
Priya: "Achha, Rahul ji! Bahut khushi hui. Toh, aapka phone number kya hai?"

User: "9876543210"
Priya: "Theek hai. Aur... kaunsi date pe aap appointment book karna chahenge?"

User: "Kal subah"
Priya: "Bilkul! Kal ka din toh perfect hai. Subah mein kaunsa time aapko suit karega?"

Remember: Sound warm, helpful, and human. Avoid being a strict robot.\"\"\"

        # Hard guardrail — belt-and-suspenders against re-asking
        do_not_ask = [k for k, v in collected.items() if v is not None]
        if do_not_ask:
            base += f"\n\nDO NOT ask for: {', '.join(do_not_ask)}."

    else:
        name  = collected.get("name", "")
        phone = collected.get("phone", "")
        date  = collected.get("date", "")
        time  = collected.get("time", "")
        base = f\"\"\"You are Priya. The booking is complete.

CONFIRMED: name={name}, phone={phone}, date={date}, time={time}

Say something warm and natural to confirm the booking:
"Achha {name} ji, toh aapki appointment {date} ko {time} baje confirm ho gayi hai. Humein bahut khushi hai ki aapne humein chuna. Aapko jald hi confirmation message mil jayega. Dhanyavaad!"

The phrase 'booking confirmed' or 'confirm ho gayi hai' MUST appear in your reply.
Keep it natural and friendly.\"\"\"

    return base


# ---------------------------------------------------------------------------
# LLM CALL WRAPPERS
# ---------------------------------------------------------------------------

async def _call_groq(messages: list[dict], temperature: float, max_tokens: int) -> str:
    assert groq_client is not None, "GROQ_API_KEY is not set"
    response = await groq_client.chat.completions.create(
        model="llama3-8b-8192",
        messages=messages, # type: ignore
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
                    "num_ctx": 512,        # small context = much faster first token
                    "num_thread": 8,       # match your CPU core count
                    "repeat_penalty": 1.0, # disabled = slightly faster sampling
                },
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()


async def get_llm_response(history: list[dict], collected: dict) -> str:
    """
    Call LLM with a dynamically built system prompt based on current state.
    collected: the session's collected fields dict (updated by extract_fields_from_text)
    """
    next_field = get_next_question(collected)
    system = build_system_prompt(collected, next_field)
    trimmed = history[-4:]
    messages = [{"role": "system", "content": system}] + trimmed
    if USE_GROQ:
        return await _call_groq(messages, temperature=0.4, max_tokens=60)  # slightly higher temp for naturalness
    return await _call_ollama(messages, temperature=0.3, max_tokens=60)    # was 0.1




def is_booking_confirmed(text: str) -> bool:
    """Check if the bot's response contains the booking confirmation trigger phrase."""
    lower = text.lower()
    return "booking confirmed" in lower or "confirm ho gayi hai" in lower


async def _call_ollama_json(messages: list[dict]) -> dict:
    """Forces Ollama to return valid JSON via format=json — no hallucinated prose."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "format": "json",  # Ollama native JSON mode
                "options": {
                    "temperature": 0,
                    "num_predict": 80,
                    "num_ctx": 512,
                },
            },
        )
        resp.raise_for_status()
        return json_lib.loads(resp.json()["message"]["content"])


async def extract_booking_fields(history: list[dict]) -> dict:
    """
    Extract structured booking fields — Python regex first (never hallucinates),
    then LLM JSON extraction only for anything still missing.
    """
    collected = {"name": None, "phone": None, "date": None, "time": None}
    for msg in history:
        if msg["role"] == "user":
            collected = extract_fields_from_text(msg["content"], collected)

    if any(v is None for v in collected.values()):
        extraction_prompt = (
            "Extract booking fields from the conversation. "
            "Return JSON with exactly these keys: name, phone, date, time. "
            "Use null for anything not mentioned. Return ONLY the JSON object."
        )
        messages = history + [{"role": "user", "content": extraction_prompt}]
        try:
            if USE_GROQ:
                text = await _call_groq(messages, temperature=0, max_tokens=80)
                text = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()
                json_match = re.search(r'\{.*?\}', text, re.DOTALL)
                if json_match:
                    llm_fields = json_lib.loads(json_match.group())
                    for k in collected:
                        if collected[k] is None and llm_fields.get(k):
                            collected[k] = llm_fields[k]
            else:
                # Ollama: use format=json for guaranteed valid JSON output
                llm_fields = await _call_ollama_json(messages)
                for k in collected:
                    if collected[k] is None and llm_fields.get(k):
                        collected[k] = llm_fields[k]
        except Exception as e:
            print(f"[LLM] JSON extraction failed: {e}")

    return collected
