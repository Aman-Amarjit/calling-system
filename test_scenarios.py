import asyncio
import os
from dotenv import load_dotenv
from llm import get_llm_response, extract_fields_from_text

load_dotenv()

# Test scenarios from user
test_cases = [
    {
        "name": "TEST 1: Caller gives name and date",
        "history": [{"role": "user", "content": "Hello, mera naam Rahul hai aur 15 June ko appointment chahiye"}],
        "expected": "Should ask for phone number (acknowledge name and date first)"
    },
    {
        "name": "TEST 2: Caller asks question mid-booking",
        "history": [
            {"role": "user", "content": "Hello, mera naam Rahul hai"},
            {"role": "assistant", "content": "Namaste Rahul ji, aapka naam noted. Ab phone number bataiye."},
            {"role": "user", "content": "Yeh service kitne baje tak available hai?"}
        ],
        "expected": "Should answer briefly then ask for phone number"
    },
    {
        "name": "TEST 3: Caller gives unclear date",
        "history": [
            {"role": "user", "content": "mera naam Rahul"},
            {"role": "assistant", "content": "Rahul ji, naam noted. Phone number?"},
            {"role": "user", "content": "9876543210"},
            {"role": "assistant", "content": "Phone noted. Date bataiye."},
            {"role": "user", "content": "agle hafte"}
        ],
        "expected": "Should ask to clarify which specific day next week"
    },
    {
        "name": "TEST 4: Full happy path",
        "history": [
            {"role": "user", "content": "Hi, Rahul bol raha hoon"},
            {"role": "assistant", "content": "Namaste Rahul ji! Mera naam Priya hai. Aapko appointment booking ki zaroorat hai? Kya aap apni date aur samay bata sakte hain?"},
            {"role": "user", "content": "9876543210"},
            {"role": "assistant", "content": "Aapka phone number sahi hai, Rahul ji. Ab abhi date aur samay bataiye. Kya 15 June ko aur kuch samay chahiye?"},
            {"role": "user", "content": "20 June"},
            {"role": "assistant", "content": "Sahi ho gaya! Aapke liye 20 June ki appointment booking ki ja rahi hai. Ab abhi samay bataiye. Kya aapko morning ya afternoon mein jagah milna chahiye?"},
            {"role": "user", "content": "Shaam 5 baje"},
            {"role": "assistant", "content": "Aapka samay sahi ho gaya, Rahul ji. Toh main confirm karta hoon — naam Rahul, number 9876543210, date 20 June, aur time shaam 5 baje. Sahi hai?"},
            {"role": "user", "content": "Haan bilkul sahi hai"}
        ],
        "expected": "Should confirm booking with 'booking confirmed'"
    }
]

async def run_test(test_case):
    print(f"\n--- {test_case['name']} ---")
    print(f"Expected: {test_case['expected']}")
    collected = {"name": None, "phone": None, "date": None, "time": None}

    # Simulate collecting fields from history
    for msg in test_case["history"]:
        if msg["role"] == "user":
            collected = extract_fields_from_text(msg["content"], collected)

    print(f"Collected so far: {collected}")

    # Get the last user message for the response
    last_user = [msg for msg in test_case["history"] if msg["role"] == "user"][-1]
    history_for_response = test_case["history"][:-1] + [{"role": "user", "content": last_user["content"]}]

    response = await get_llm_response(history_for_response, collected)
    print(f"PRIYA: {response}")
    print(f"Word count: {len(response.split())}")

async def main():
    for test in test_cases:
        await run_test(test)

if __name__ == "__main__":
    asyncio.run(main())