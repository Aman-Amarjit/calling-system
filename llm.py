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
# This is the key fix: we extract fields deterministically so the LLM
# never has to "remember" what it already collected.
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r'\b[6-9]\d{9}\b')

_MONTHS = [
    "january","february","march","april","may","june",
    "july","august","september","october","november","december",
    "jan","feb","mar","apr","jun","jul","aug","sep","oct","nov","dec",
]

_DATE_WORDS = [
    "aaj","kal","parso","agle","next","monday","tuesday","wednesday",
    "thursday","friday","saturday","sunday","somvar","mangalvar",
    "budhvar","guruvar","shukravar","shanivar","ravivar",
    "1","2","3","4","5","6","7","8","9","10","11","12","13","14","15",
    "16","17","18","19","20","21","22","23","24","25","26","27","28","29","30","31",
    "pehli","doosri","teesri","chauthi","paanchvi",
]

_TIME_WORDS = [
    "baje","am","pm","morning","evening","afternoon","night",
    "subah","shaam","dopahar","raat","midnight","noon",
    "1","2","3","4","5","6","7","8","9","10","11","12",
    "ek","do","teen","chaar","paanch","chhe","saat","aath","nau","das","gyarah","barah",
]


def extract_fields_from_text(text: str, existing: dict) -> dict:
    """
    Extract booking fields from a single user message using regex + heuristics.
    Only updates fields that are still None in existing.
    Returns updated dict.
    """
    collected = dict(existing)
    lower = text.lower()
    words = lower.split()

    # --- Phone number ---
    if collected["phone"] is None:
        m = _PHONE_RE.search(text)
        if m:
            collected["phone"] = m.group()

    # --- Name ---
    # Patterns: "mera naam X hai", "main X hoon", "I am X", "naam X"
    if collected["name"] is None:
        name_patterns = [
            # "mera naam Rahul hai" — capture stops before hai/hoon
            r"(?:mera|meri|my|main|mai)\s+naam\s+([A-Za-z]+(?:\s+[A-Za-z]+?)?)\s*(?:\bhai\b|\bhe\b|\bhoon\b|\bhun\b|\bh\b|$)",
            # "naam Rahul" or "naam hai Rahul"
            r"\bnaam\s+(?:hai\s+)?([A-Za-z]{2,})\b",
            # "I am Rahul" / "I'm Rahul" / "myself Rahul"
            r"(?:i am|i'm|myself)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)",
            # "Rahul bol raha hoon" / "Rahul speaking"
            r"([A-Za-z]{3,}(?:\s+[A-Za-z]+)?)\s+(?:bol|speaking|here|bolta|bolti)",
        ]
        _STOP_WORDS = {
            "hello","hi","haan","nahi","okay","ok","yes","no","sir","madam",
            "hai","he","hoon","hun","h","aur","or","aap","main","mera","meri",
            "naam","number","phone","date","time","appointment","booking",
        }
        for pat in name_patterns:
            m = re.search(pat, lower)
            if m:
                name = m.group(1).strip().title()
                # Reject if any word in the captured name is a stop word
                name_words = name.lower().split()
                if not any(w in _STOP_WORDS for w in name_words) and len(name_words[0]) >= 2:
                    collected["name"] = name
                    break

    # --- Date ---
    if collected["date"] is None:
        # Check for month name — use word boundary to avoid "market" matching "mar"
        for month in _MONTHS:
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
    """Build a focused system prompt that tells the LLM exactly what to do next."""

    known = []
    if collected["name"]:    known.append(f"name: {collected['name']}")
    if collected["phone"]:   known.append(f"phone: {collected['phone']}")
    if collected["date"]:    known.append(f"date: {collected['date']}")
    if collected["time"]:    known.append(f"time: {collected['time']}")

    known_str = (", ".join(known)) if known else "nothing yet"

    field_instructions = {
        "name":  "Ask for the caller's full name only. Be warm and welcoming.",
        "phone": "Ask for the caller's phone number only. Acknowledge what they said first.",
        "date":  "Ask for their preferred appointment date only. Acknowledge what they said first.",
        "time":  "Ask for their preferred appointment time only. Acknowledge what they said first.",
        None:    (
            f"You have all four details: {known_str}. "
            f"Read them back to confirm: naam {collected['name']}, "
            f"number {collected['phone']}, date {collected['date']}, "
            f"time {collected['time']}. Ask 'Sahi hai?'"
        ),
    }

    base = f"""You are Priya, a professional appointment booking assistant.
Speak in polite Hinglish (Hindi + English mix). Be warm, courteous, and brief.

WHAT YOU ALREADY KNOW: {known_str}

YOUR NEXT TASK: {field_instructions[next_field]}

RULES:
- Reply in under 30 words
- Never ask for information you already have
- Never mention you are an AI
- If caller asks a question, answer it in one sentence then do your task
- If caller's answer is unclear, ask only about that unclear part
- If caller says something off-topic, gently redirect to the booking"""

    if next_field is None:
        base += f"""

AFTER CALLER CONFIRMS: Reply with EXACTLY:
"Bahut shukriya {collected['name']} ji. Aapki appointment booking confirmed ho gayi hai. Aapko jald hi confirmation milega."
The phrase "booking confirmed" MUST appear."""

    return base


# ---------------------------------------------------------------------------
# LLM CALL WRAPPERS
# ---------------------------------------------------------------------------

async def _call_groq(messages: list[dict], temperature: float, max_tokens: int) -> str:
    response = await groq_client.chat.completions.create(
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


async def get_llm_response(history: list[dict], collected: dict) -> str:
    """
    Call LLM with a dynamically built system prompt based on current state.
    collected: the session's collected fields dict (updated by extract_fields_from_text)
    """
    next_field = get_next_question(collected)
    system = build_system_prompt(collected, next_field)
    messages = [{"role": "system", "content": system}] + history
    if USE_GROQ:
        return await _call_groq(messages, temperature=0.7, max_tokens=100)
    return await _call_ollama(messages, temperature=0.7, max_tokens=100)


def is_booking_confirmed(text: str) -> bool:
    """Check if the bot's response contains the booking confirmation trigger phrase."""
    return "booking confirmed" in text.lower()


async def extract_booking_fields(history: list[dict]) -> dict:
    """
    Extract structured booking fields — tries Python regex first,
    falls back to LLM extraction only if regex missed something.
    """
    # Rebuild from full conversation using Python extractor
    collected = {"name": None, "phone": None, "date": None, "time": None}
    for msg in history:
        if msg["role"] == "user":
            collected = extract_fields_from_text(msg["content"], collected)

    # If anything still missing, try LLM extraction as fallback
    if any(v is None for v in collected.values()):
        extraction_prompt = (
            "From the conversation above, extract booking details. "
            "Return ONLY valid JSON with keys: name, phone, date, time. "
            "Use null for missing fields. No explanation, just JSON."
        )
        messages = history + [{"role": "user", "content": extraction_prompt}]
        try:
            if USE_GROQ:
                text = await _call_groq(messages, temperature=0, max_tokens=80)
            else:
                text = await _call_ollama(messages, temperature=0, max_tokens=80)
            # Strip markdown code fences robustly
            text = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()
            # Extract first JSON object from the response
            json_match = re.search(r'\{.*?\}', text, re.DOTALL)
            if json_match:
                llm_fields = json_lib.loads(json_match.group())
                for k in collected:
                    if collected[k] is None and llm_fields.get(k):
                        collected[k] = llm_fields[k]
        except Exception:
            pass

    return collected
