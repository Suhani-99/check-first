import os
import json
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "llama-3.3-70b-versatile"  # free, fast

# ---------------------------------------------------------------------------
# THE PROMPT is the whole spike. Everything the product must do lives here:
#  - it assesses SCAM INTENT, not whether media is real/fake
#  - it is structurally forbidden from ever saying "genuine"
#  - it always explains in plain language
#  - it always ends with a human verification step
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a scam and manipulation shield that helps families,
especially older adults, decide what to do about a suspicious message.

You assess SCAM INTENT and manipulation pressure. You do NOT and CANNOT judge
whether a voice, video, or message is real or fake. Never declare anything
genuine, authentic, safe, or verified. That is not your job and it is not
possible to do reliably.

Check the message for these manipulation signals:
  - urgency pressure (act now, emergency, no time to think)
  - secrecy demand (don't tell anyone, keep this between us)
  - unusual payment route (gift cards, crypto, a new/unknown account or UPI ID)
  - impersonated authority (bank, police, government, official)
  - blocking verification (can't be reached normally, don't call back)

Important: urgency ALONE is weak - a real emergency is also urgent. What
matters is the CLUSTER of signals, especially anything that tries to stop the
person from verifying. A real emergency wants to be verified; a scam does not.

Return ONLY valid JSON, no other text, in exactly this shape:
{
  "risk_level": "high" | "medium" | "low",
  "risk_label": "short plain phrase, e.g. 'strong scam pressure' or 'few scam signals'",
  "signals_detected": [
    {"signal": "urgency pressure", "evidence": "quote or paraphrase from the message"}
  ],
  "explanation": "2-3 sentences a non-technical person understands. No jargon.",
  "verification_step": "one concrete action, e.g. call them back on a number you already have"
}

Rules for the JSON:
  - signals_detected lists ONLY signals that are actually present (can be empty)
  - even when risk_level is 'low', you STILL give a verification_step
  - never put words like 'genuine', 'authentic', or 'safe to send' anywhere
"""


def analyze(message: str) -> dict:
    """Send one message to the LLM and get back structured, guard-railed output."""
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,  # we want stable, repeatable judgments
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Message to assess:\n\n{message}"},
        ],
    )
    result = json.loads(resp.choices[0].message.content)
    return guardrail(result)


def guardrail(result: dict) -> dict:
    """
    The safety layer. Even if the model misbehaves, this enforces the two
    non-negotiables in CODE, not just in the prompt:
      1. a verification step is ALWAYS present
      2. a 'never certifies genuine' reminder is ALWAYS attached
    Notice there is no 'genuine' field anywhere - it is structurally impossible
    for this system to output one.
    """
    if not result.get("verification_step"):
        result["verification_step"] = (
            "Call the person back on a number you already have saved, "
            "not a number from this message."
        )
    result["reminder"] = (
        "This tool never confirms a message is genuine. Always verify with the "
        "real person through a channel you already trust."
    )
    return result


# ---------------------------------------------------------------------------
# Test cases - deliberately chosen to stress the design.
# ---------------------------------------------------------------------------
TEST_CASES = [
    ("SCAM - classic emergency + secrecy + odd payment",
     "Mom it's me, I had an accident and the police need bail money right now. "
     "Don't tell Dad, he'll panic. Send 40000 to this UPI ID immediately, I can't talk long."),

    ("LEGIT - normal check-in",
     "Hi Mom, reached Delhi safe. Will call you tonight after dinner."),

    ("HARD CASE - a REAL emergency, urgent but not a scam",
     "Mom I'm okay but my card got declined at the pharmacy and I need 2000. "
     "Send it to my usual number whenever you can, or just call me."),

    ("SCAM - impersonated authority + OTP theft",
     "This is HDFC Bank security. Your account is compromised. To secure it, "
     "read out the OTP we just sent to your phone. Do this now or your account is frozen."),
]


def main():
    for label, message in TEST_CASES:
        print("=" * 70)
        print(label)
        print("-" * 70)
        print(f"MESSAGE: {message}\n")
        result = analyze(message)
        print(f"  risk_level        : {result['risk_level']}")
        print(f"  risk_label        : {result['risk_label']}")
        print(f"  signals_detected  :")
        for s in result["signals_detected"]:
            print(f"      - {s['signal']}: {s['evidence']}")
        print(f"  explanation       : {result['explanation']}")
        print(f"  verification_step : {result['verification_step']}")
        print(f"  reminder          : {result['reminder']}")
        print()


if __name__ == "__main__":
    main()