"""
analyzer.py — the analysis engine (the "brain" of the product).

This is deliberately its OWN module so that BOTH:
  - the live API (app.py), and
  - the scoring script (score.py, built next)
call the *exact same* analyze() function. That means the accuracy number you
measure describes the behaviour you actually ship — no "tested one thing,
deployed another" gap. This is the integrity of your whole KPI story.
"""
import os
import json
from groq import Groq

_client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "llama-3.3-70b-versatile"  # free on Groq, fast

# ---------------------------------------------------------------------------
# The prompt is the product. Every rule the tool must follow lives here.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a scam and manipulation shield that helps families,
especially older adults, decide what to do about a suspicious message.

You assess SCAM INTENT and manipulation pressure. You do NOT and CANNOT judge
whether a voice, video, or message is real or fake. Never declare anything
genuine, authentic, safe, or verified — that is impossible to do reliably and
dangerous to claim.

Check the message for these manipulation signals:
  - urgency pressure (act now, emergency, deadline, no time to think)
  - secrecy demand (don't tell anyone, keep this between us)
  - unusual payment route (gift cards, crypto, a new/unknown account or UPI ID,
    changed bank details)
  - impersonated authority (bank, police, government, tax, delivery, tech-support)
  - credential / OTP request (share or forward an OTP, password, or code)
  - blocking verification (can't be reached normally, don't call back, don't disconnect)
  - threats or fear (account frozen, legal action, shame you to your contacts)
  - suspicious links (lookalike or unfamiliar web addresses)

Important: urgency ALONE is weak — a real emergency is also urgent. What matters
is the CLUSTER of signals, especially anything that tries to STOP the person from
independently verifying. A real emergency wants to be verified; a scam does not.

Decide scam_intent: true if the message shows a pattern of manipulation
consistent with a scam; false if it does not. When false, this means "no clear
scam signals" — it does NOT mean the message is genuine or safe.

Return ONLY valid JSON, no other text, in exactly this shape:
{
  "scam_intent": true or false,
  "risk_level": "high" or "medium" or "low",
  "risk_label": "short plain phrase, e.g. 'strong scam pressure' or 'no clear scam signals'",
  "signals_detected": [
    {"signal": "urgency pressure", "evidence": "short quote or paraphrase from the message"}
  ],
  "explanation": "2-3 sentences a non-technical person understands. No jargon.",
  "verification_step": "one concrete action, e.g. call them back on a number you already have"
}

Rules for the JSON:
  - signals_detected lists ONLY signals actually present (may be empty)
  - even when scam_intent is false, still give a verification_step
  - never put words like 'genuine', 'authentic', or 'safe to send' anywhere
"""


def analyze(message: str) -> dict:
    """Assess one message. Returns a guard-railed structured result."""
    resp = _client.chat.completions.create(
        model=MODEL,
        temperature=0,  # stable, repeatable judgments
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Message to assess:\n\n{message}"},
        ],
    )
    try:
        result = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        # Fail SAFE, never fail reassuring: if we can't parse, treat as suspicious.
        result = {
            "scam_intent": True,
            "risk_level": "medium",
            "risk_label": "could not fully analyse — treat with caution",
            "signals_detected": [],
            "explanation": "The system could not fully analyse this message, so it "
                           "is flagging it for caution rather than assuming it is fine.",
            "verification_step": "",
        }
    return guardrail(result)


def guardrail(result: dict) -> dict:
    """
    The safety layer — enforced in CODE, not just requested in the prompt.
    Guarantees (structurally, not by hope):
      1. a verification step is ALWAYS present
      2. a 'never confirms genuine' reminder is ALWAYS attached
      3. sensible defaults if any field is missing, always erring toward caution
    Note there is no 'genuine'/'safe' field anywhere — it is structurally
    impossible for this system to certify a message as real.
    """
    result.setdefault("scam_intent", True)     # fail safe
    result.setdefault("risk_level", "medium")
    result.setdefault("risk_label", "assessed")
    result.setdefault("signals_detected", [])
    result.setdefault("explanation", "")
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