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
    """Build a tightly-constrained prompt. Small models need hard rules, not suggestions."""
    known = {k: v for k, v in collected.items() if v is not None}
    known_str = ", ".join(f"{k}={v}" for k, v in known.items()) or "nothing yet"

    if next_field is not None:
        base = f"""You are Priya, a booking assistant. Respond in Hinglish only.

CONFIRMED FACTS (do not question, repeat, or re-ask these):
{known_str}

YOUR ONLY JOB RIGHT NOW: Ask for '{next_field}' in one warm sentence.

HARD RULES:
- Reply in UNDER 20 words
- Do NOT invent, assume, or repeat any field value
- Do NOT say booking is confirmed unless instructed
- Do NOT ask for anything except '{next_field}'
- Do NOT explain yourself
- If caller goes off-topic, say "zaroor" and redirect to '{next_field}'

FEW-SHOT EXAMPLES (follow this style exactly):
Caller: "Mera naam Rahul hai"
Priya: "Shukriya Rahul ji! Aapka phone number kya hai?"

Caller: "9876543210"
Priya: "Perfect! Kaunsi date pe appointment chahiye aapko?"

Caller: "Kal"
Priya: "Bilkul! Subah ya shaam — kaunsa time prefer karenge aap?"

Notice: Short. Warm. Asks exactly one thing. Never invents details."""

        # Hard guardrail — belt-and-suspenders against re-asking
        do_not_ask = [k for k, v in collected.items() if v is not None]
        if do_not_ask:
            base += f"\n\nDO NOT ask for: {', '.join(do_not_ask)}. Asking again is an error."

    else:
        name  = collected.get("name", "")
        phone = collected.get("phone", "")
        date  = collected.get("date", "")
        time  = collected.get("time", "")
        base = f"""You are Priya. All booking details are confirmed.

CONFIRMED: name={name}, phone={phone}, date={date}, time={time}

Say EXACTLY this (do not change a single word):
"Shukriya {name} ji. Aapki appointment {date} ko {time} baje confirmed ho gayi hai. Aapko jald confirmation milega. Dhanyavaad!"

The phrase 'booking confirmed' MUST appear somewhere in your reply.
Do not add anything else."""

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
        return await _call_groq(messages, temperature=0.2, max_tokens=60)  # low temp = deterministic
    return await _call_ollama(messages, temperature=0.1, max_tokens=60)    # was 0.7


async def get_llm_response_streaming(history: list[dict], collected: dict):
    """
    Async generator: yields text sentence-by-sentence as Ollama streams tokens.
    Allows TTS synthesis of the first sentence before the LLM finishes the rest.
    Only used with Ollama (Groq latency is already low enough to not need streaming).
    """
    next_field = get_next_question(collected)
    system = build_system_prompt(collected, next_field)
    trimmed = history[-4:]
    messages = [{"role": "system", "content": system}] + trimmed

    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": True,
                "options": {"num_predict": 60, "num_ctx": 512},
            },
        ) as resp:
            buffer = ""
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json_lib.loads(line)
                except Exception:
                    continue
                buffer += chunk.get("message", {}).get("content", "")
                # Yield every complete sentence immediately
                while any(p in buffer for p in ["।", ".", "?", "!"]):
                    for punct in ["।", ".", "?", "!"]:
                        if punct in buffer:
                            sentence, buffer = buffer.split(punct, 1)
                            sentence = sentence.strip()
                            if sentence:
                                yield sentence + punct
                            break
            # Flush any remaining text
            if buffer.strip():
                yield buffer.strip()


def is_booking_confirmed(text: str) -> bool:
    """Check if the bot's response contains the booking confirmation trigger phrase."""
    return "booking confirmed" in text.lower()


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
