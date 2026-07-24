"""
analyzer.py — the analysis engine (the "brain" of the product).

Shared by BOTH the live API (app.py) and the scoring script (score.py), so the
accuracy you measure describes the behaviour you actually ship.

DAY 3 REVISION — fixes three failures found by testing:
  1. Signals were being INVENTED ("OTP request is implied" when no OTP was
     mentioned). Now every signal must quote real text.
  2. "Unusual payment route" fired on ordinary transfers ("UPI me on my usual
     number"). Now narrowly defined: new/unknown/changed destinations only.
  3. Urgency + a money request alone was enough for a scam verdict, so terse
     real messages ("mom i need help pls send 5000 rn") were flagged. Now a
     scam verdict requires a genuine red flag beyond urgency.
"""
import os
import re
import json
from groq import Groq

_client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "llama-3.3-70b-versatile"

# tokens used by the most recent call (read by score.py to pace itself)
LAST_USAGE = 0

# ---------------------------------------------------------------------------
# Few-shot examples. IMPORTANT: these are deliberately NOT drawn from
# testset.csv — using test cases here would contaminate the evaluation.
# ---------------------------------------------------------------------------
FEWSHOT = """
Examples of correct judgments:

MESSAGE: "ma need 800 urgently for books, send whenever ur free"
CORRECT: scam_intent = false. Urgent and asks for money, but there is no
secrecy, no unknown payment destination, no impersonation, and nothing blocking
verification. Terse and demanding is how real people text. Still give a
verification step.

MESSAGE: "Mom I'm at the police station, I need 50,000 for bail. Don't tell
anyone, and don't call me back - send it to this account: 9284xxxx"
CORRECT: scam_intent = true. Secrecy demand ("Don't tell anyone"), blocks
verification ("don't call me back"), and a new unknown account. These are real
red flags quoted directly from the text.

MESSAGE: "Hi, following up on invoice 8823 - please note we've switched banks,
so kindly send this month's payment to the new account below. Thanks!"
CORRECT: scam_intent = true. No urgency and a perfectly polite tone, but the
payment destination has CHANGED to a new account - that alone is an unusual
payment route and is sufficient.

MESSAGE: "Your SBI account is suspended. Share the OTP sent to your phone to
reactivate."
CORRECT: scam_intent = true. Impersonated authority plus an explicit request to
share an OTP - both quoted directly.
"""

SYSTEM_PROMPT = """You are a scam and manipulation shield that helps families,
especially older adults, decide what to do about a suspicious message.

You assess SCAM INTENT and manipulation pressure. You do NOT and CANNOT judge
whether a voice, video, or message is real or fake. Never declare anything
genuine, authentic, safe, or verified.

=== THE SIGNALS (each requires DIRECT evidence in the text) ===
  - secrecy demand: told to hide it ("don't tell Dad", "keep this between us")
  - blocking verification: discouraged from checking ("don't call back",
    "do not disconnect", "I can't be reached", "can't talk")
  - unusual payment route: a NEW, UNKNOWN or CHANGED destination. This
    INCLUDES all of the following, and these count even when the message is
    calm, polite and businesslike:
      * gift cards, vouchers, or crypto
      * a specific unfamiliar account or UPI ID given in the message
      * "our bank details have changed" / "updated account details" / "new
        account" for an invoice or vendor payment
      * money sent to a NEW or DIFFERENT number/account than the one normally
        used ("save this as my new number", "I'm on a friend's number")
      * an ADVANCE FEE to release something - customs, clearance, processing,
        registration, delivery, or a prize/package being "held"
  - impersonated authority: claims to be a bank, police, government, tax
    office, delivery service, or tech support
  - credential request: asks you to SHARE or FORWARD an OTP, code, PIN or password
  - threats or fear: account frozen, legal action, arrest, public shaming
  - suspicious link: an unfamiliar or lookalike web address
  - urgency pressure: pushed to act immediately

=== SIGNAL DISCIPLINE (critical) ===
Only list a signal if you can quote the EXACT words that show it. Never infer,
imply, or extrapolate a signal that is not literally present. Do not use words
like "implied", "implication", or "potentially" in the signal name. If the
message does not mention an OTP, there is NO credential request. If it does not
name an unfamiliar account, there is NO unusual payment route.

What is NOT an unusual payment route: sending money to a person's own known
number, their usual account, or any ordinary transfer where no new or unfamiliar
destination is named. "UPI me on my usual number" is a NORMAL route.

=== THE VERDICT BAR ===
Urgency alone is WEAK evidence - a real emergency is also urgent and demanding.
Short, blunt, context-free requests are how real people text under stress.

Set scam_intent = true ONLY if at least one of these is present:
  secrecy demand, blocking verification, unusual payment route (as defined
  above), impersonated authority, credential request, threats, or a suspicious
  link.

Urgency plus a request for money, with NONE of the above, is NOT sufficient.

But do NOT swing too far the other way: if ANY of the listed red flags IS
present in the text, set scam_intent = true - even if the message is calm,
polite, well-written, or has no urgency at all. Quiet scams are common. A
changed account detail, an advance fee, or a new unfamiliar payment destination
is enough on its own.
In that case set scam_intent = false with risk_level "low" or "medium", say
plainly that you see no clear scam signals, and STILL give a verification step.

Remember the asymmetry: it is safe to under-flag because every result routes
the user to verify with a real human. It is NOT safe to accuse ordinary
messages with invented evidence - that destroys the user's trust in the tool.

=== THE VERIFICATION PLAN (this is the most important part of your output) ===
A single instruction like "call them back" is not enough - the user will hit a
wall the moment it fails. You must give a LADDER of actions that lets the person
DETERMINE the truth using only what is in their own power.

Every action must be DIAGNOSTIC: taking it should actually reveal whether this is
a scam, not merely delay. Tailor it to the scam type:
  - family/emergency impersonation -> call the person on the number ALREADY saved
    in your phone (never a number from the message); if that fails, call someone
    else who would know where they are
  - bank / card / OTP -> hang up and call the number printed on the BACK OF YOUR
    CARD or in the bank's official app; never a number from the message
  - IMPORTANT: whenever you tell the user to call an official body, name the
    ACTUAL number if a national one exists (police/emergency: 112; cybercrime
    and financial fraud: 1930). Never say only "the published number" without
    telling them how to find it.
  - police / government / "digital arrest" -> no Indian police force conducts
    arrests, interrogations or investigations over a phone or video call. Hang up
    and dial 112 (police emergency) or 1930 (cybercrime helpline) directly
  - delivery / parcel / customs -> check the order status inside the courier's or
    seller's official app; never through a link in the message
  - job offer -> call the company's official HR number from their website; no
    legitimate employer charges a fee to hire you
  - investment / trading -> check whether the adviser or platform is registered
    with SEBI; guaranteed returns are not legal to promise
  - invoice / vendor payment change -> phone the vendor on the number you have
    used before and confirm the account change verbally

EVEN WHEN THERE ARE NO SCAM SIGNALS, the primary action must still be a real
VERIFICATION action - never social chit-chat. Do not write things like "enjoy
your birthday" or "reply when you can". Write the light check that fits, e.g.
"No action needed, but if you ever act on a message like this, contact them on
the number you already have rather than replying here." Keep it calm and short:
a harmless message must not be made to feel like an emergency.

IDENTITY CHECK (use when someone claims to be a known person):
ALWAYS name the family safe word FIRST and explicitly - write it as a question
the user can say out loud, e.g. "Ask them: what is our family safe word?".
Then, as a fallback for families who have not agreed one yet, add a specific
shared memory that could not be found on social media. Never give only the
shared-memory version. Close with why it works: a cloned voice can copy how
someone sounds, but it cannot know a word that was never spoken online.

=== OUTPUT ===
Return ONLY valid JSON in exactly this shape:
{
  "scam_intent": true or false,
  "involves_action": true or false,
  "risk_level": "high" or "medium" or "low",
  "risk_label": "short plain phrase, e.g. 'strong scam pressure' or 'no clear scam signals'",
  "signals_detected": [
    {"signal": "secrecy demand", "evidence": "exact words quoted from the message"}
  ],
  "explanation": "2-3 sentences a non-technical person understands. No jargon. If there are no clear signals, say so plainly and do not speculate.",
  "verification_step": "the single primary action, one sentence",
  "verification_plan": {
    "primary": "the first thing to do, specific to this scam type",
    "identity_check": "an exact question to ask that only the real person could answer, or how to use the family safe word",
    "if_unreachable": "what to do if you cannot reach them - who else to contact, what else to check",
    "if_cannot_verify": "what to do when verification is impossible"
  }
}

Rules:
  - involves_action is TRUE whenever the message asks for money, a payment, a
    transfer, an OTP/code/password, card details, or any other action the user
    could not undo. It is about what is AT STAKE, not about how suspicious the
    message is - a completely ordinary request for money is still true. It is
    FALSE only for messages with nothing at stake, such as a greeting, a status
    update or an appointment reminder.
  - signals_detected may be empty; empty is correct when nothing is present
  - ALWAYS fill every field of verification_plan, even when scam_intent is false
  - if_cannot_verify must always say NOT to send money or share any code. Being
    unable to verify is itself the strongest red flag - a scam manufactures
    urgency precisely to outrun verification
  - never use the words 'genuine', 'authentic', or 'safe to send'
"""


def analyze(message: str) -> dict:
    """Assess one message. Returns a guard-railed structured result."""
    resp = _client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + FEWSHOT},
            {"role": "user", "content": f"Message to assess:\n\n{message}"},
        ],
    )
    global LAST_USAGE
    try:
        LAST_USAGE = int(resp.usage.total_tokens)
    except Exception:
        LAST_USAGE = 0
    try:
        result = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        result = {
            "scam_intent": True,
            "risk_level": "medium",
            "risk_label": "could not fully analyse - treat with caution",
            "signals_detected": [],
            "explanation": "The system could not fully analyse this message, so it "
                           "is flagging it for caution rather than assuming it is fine.",
            "verification_step": "",
        }
    return guardrail(result)


# Detecting affirmative certification of genuineness.
#
# A fixed phrase list is not enough: the model writes "appears to be a genuine
# birthday wish", which no exact phrase match catches. So we match a PATTERN -
# a linking/appearance verb followed within a few words by a genuineness word -
# and we explicitly exclude negated forms, because our own disclaimer
# legitimately says "never confirms a message is genuine".
_GENUINE_WORD = r"(?:genuine|authentic|legitimate|real|safe|trustworthy|verified)"
_LINK_VERB = r"(?:is|are|it'?s|was|appears?|seems?|looks?|sounds?|comes across)"
CERTIFY_PATTERN = re.compile(
    rf"\b{_LINK_VERB}\b(?:\s+(?:to\s+be|like|as if))?\s+(?:a|an|the)?\s*"
    rf"(?:\w+\s+){{0,2}}{_GENUINE_WORD}\b",
    re.IGNORECASE,
)
# If any of these appear right before the match, it is a DENIAL, not a claim.
NEGATORS = re.compile(
    r"\b(?:not|never|cannot|can'?t|do(?:es)?n'?t|didn'?t|won'?t|no|unable\s+to|"
    r"without|rather\s+than|instead\s+of|confirm(?:s|ed)?\s+whether)\b",
    re.IGNORECASE,
)
# Blunt phrases that are always unacceptable regardless of grammar.
ALWAYS_BAD = [
    "safe to send", "safe to pay", "safe to transfer", "you can send",
    "go ahead and send", "no need to verify", "this is not a scam",
]


def _field_certifies(text: str) -> bool:
    """Check ONE field. Fields are checked separately so a negation in one
    field cannot mask a claim in another (e.g. the label 'no clear scam
    signals' must not neutralise 'appears to be a genuine' in the explanation)."""
    text = text.lower()
    if any(p in text for p in ALWAYS_BAD):
        return True
    for m in CERTIFY_PATTERN.finditer(text):
        # only the current clause counts as context for a negation
        clause_start = max(
            text.rfind(".", 0, m.start()), text.rfind(",", 0, m.start()),
            text.rfind(";", 0, m.start()), text.rfind(" but ", 0, m.start()),
        ) + 1
        window = text[max(clause_start, m.start() - 45):m.start()]
        if not NEGATORS.search(window):
            return True
    return False


def certifies_genuine(result: dict) -> bool:
    """True only if the MODEL affirmatively certified the message as real/safe.

    Checks the model's own fields only - never our reminder text, which denies
    certification while containing the word 'genuine'.
    """
    return any(_field_certifies(str(result.get(k, "")))
               for k in ("risk_label", "explanation", "verification_step"))


# India-specific reporting guidance. Deliberately a CODE CONSTANT, never generated
# by the model: a hallucinated helpline number in a safety tool would be dangerous.
REPORTING_INDIA = {
    "if_already_sent": (
        "Call 1930 immediately - India's national cybercrime helpline, free and "
        "24/7. Then call your bank's fraud number and file at cybercrime.gov.in. "
        "Speed decides recovery: reports made within the first hour let banks "
        "freeze the receiving account before the money is moved on."
    ),
    "helpline": "1930 (cybercrime) | 112 (emergency) | cybercrime.gov.in (file a report)",
}


def guardrail(result: dict) -> dict:
    """
    Safety layer enforced in CODE, not merely requested in the prompt:
      1. a verification step is ALWAYS present
      2. a 'never confirms genuine' reminder is ALWAYS attached
      3. any affirmative certification of genuineness is STRIPPED
      4. missing fields default toward caution
    There is no 'genuine'/'safe' field in the schema, so certifying a message as
    real is structurally impossible.
    """
    result.setdefault("scam_intent", True)
    result.setdefault("involves_action", True)  # fail safe: show the full ladder
    result.setdefault("risk_level", "medium")
    result.setdefault("risk_label", "assessed")
    result.setdefault("signals_detected", [])
    result.setdefault("explanation", "")

    if certifies_genuine(result):
        # Do NOT merely prefix a disclaimer - the certifying sentence would still
        # be sitting there for the user to read. Remove the offending sentences
        # entirely and keep only what is safe to say.
        kept = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(result.get("explanation", "")))
                if s.strip() and not _field_certifies(s)]
        result["explanation"] = (
            "This tool cannot confirm whether a message is real, so it is not "
            "saying that here. " + " ".join(kept)
        ).strip()
        result["risk_label"] = "cannot be confirmed - verify with a human"
        result["certification_stripped"] = True

    if not result.get("verification_step"):
        result["verification_step"] = (
            "Call the person back on a number you already have saved, "
            "not a number from this message."
        )
    # The escalation ladder is enforced here, not left to the model.
    plan = result.get("verification_plan") or {}
    plan.setdefault("primary", result["verification_step"])
    plan.setdefault(
        "identity_check",
        "Ask them something only the real person could know - your family safe "
        "word, or a shared memory that is not on social media. A cloned voice "
        "can copy how someone sounds; it cannot know this."
    )
    plan.setdefault(
        "if_unreachable",
        "Contact someone else who would know where they are - another family "
        "member, a roommate, or their workplace - before doing anything else."
    )
    plan.setdefault(
        "if_cannot_verify",
        "Do not send money and do not share any code. Being unable to verify is "
        "not a reason to go ahead - it is the strongest warning sign there is. "
        "The pressure to hurry exists precisely to stop you checking."
    )
    # Always code-injected, never model-generated:
    plan["if_already_sent"] = REPORTING_INDIA["if_already_sent"]
    plan["helplines"] = REPORTING_INDIA["helpline"]
    result["verification_plan"] = plan

    result["reminder"] = (
        "This tool never confirms a message is genuine. Always verify with the "
        "real person through a channel you already trust."
    )
    return result

# ---------------------------------------------------------------------------
# CONVERSATION MODE
#
# Real fraud rarely arrives as one message. The dominant pattern (confirmed by
# people who receive these calls daily) is a SEQUENCE: the caller opens with
# leaked personal data to establish credibility, builds rapport over several
# turns, and only asks for money or an OTP at the very end.
#
# Single-message analysis is structurally blind to this: turn 1 asks for nothing,
# so it has no signals. This addendum is appended ONLY when analysing a
# conversation, so single-message behaviour - and therefore the committed
# evaluation - is completely unchanged.
# ---------------------------------------------------------------------------
CONVERSATION_ADDENDUM = """

=== YOU ARE NOW ANALYSING A CONVERSATION, NOT ONE MESSAGE ===
You are given a numbered sequence of turns. Judge the ARC, not any single turn.

Conversational fraud signals (these appear ACROSS turns, never within one):
  - UNEXPLAINED KNOWLEDGE: the sender knows private details - a maturing deposit,
    a recent property enquiry, a purchase - that they have no legitimate reason
    to hold. A real bank does not cold-call to recite what is already in its own
    records. Leaked or purchased data is how the credibility is manufactured.
  - RAPPORT BEFORE THE ASK: early turns request nothing at all. They exist to
    establish trust and to get you talking.
  - ESCALATING COMMITMENT: each turn asks slightly more than the last - first
    attention, then confirmation of a detail, then a small action, then money
    or a code.
  - THE ASK ARRIVES LATE: the payment, OTP or credential request appears only in
    the final turns, after trust is built. Its absence early is not reassurance.
  - MANUFACTURED CONTINUITY: "as I mentioned", "the case we discussed", implying
    a shared history that the recipient cannot actually verify.
  - CHANNEL PULL: pushing the conversation to WhatsApp, a personal number, or a
    call, away from anywhere it can be checked or recorded.

Important: a conversation can be a scam even if NO SINGLE TURN would be flagged
on its own. That is the whole point of this mode. If the arc shows manufactured
credibility followed by escalation, set scam_intent = true and say which TURN
each signal came from.

For signals_detected, quote the turn number with the evidence, e.g.
{"signal": "unexplained knowledge", "evidence": "turn 1: knows her FD matures on the 14th"}.
"""


def analyze_conversation(turns: list) -> dict:
    """Assess a SEQUENCE of messages as one conversation.

    turns: list of strings, oldest first. Returns the same structure as
    analyze(), so the API, guardrail and UI need no special handling.
    """
    global LAST_USAGE
    transcript = "\n".join(f"Turn {i}: {t.strip()}" for i, t in enumerate(turns, 1) if t.strip())

    resp = _client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + FEWSHOT + CONVERSATION_ADDENDUM},
            {"role": "user", "content": f"Conversation to assess:\n\n{transcript}"},
        ],
    )
    try:
        LAST_USAGE = int(resp.usage.total_tokens)
    except Exception:
        LAST_USAGE = 0
    try:
        result = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        result = {
            "scam_intent": True, "involves_action": True, "risk_level": "medium",
            "risk_label": "could not fully analyse - treat with caution",
            "signals_detected": [],
            "explanation": "The system could not fully analyse this conversation, "
                           "so it is flagging it for caution rather than assuming it is fine.",
            "verification_step": "",
        }
    result["mode"] = "conversation"
    result["turn_count"] = len([t for t in turns if t.strip()])
    return guardrail(result)

# ---------------------------------------------------------------------------
# LIVE FOLLOW-UP
#
# The realistic situation: the person is ON the call right now. They describe
# what is being said, turn by turn, and get guidance as it unfolds. This is the
# same engine, carrying the original analysis plus the running history as
# context, so advice compounds instead of resetting.
# ---------------------------------------------------------------------------
FOLLOWUP_PROMPT = """You are the same scam and manipulation shield, now helping
someone THROUGH a situation in real time. They have already had a message or call
assessed, and are now telling you what is happening next.

Reply the way a calm, well-informed relative would: short, plain, specific.
2-4 sentences. No jargon, no headings, no bullet lists.

Rules that never change:
  - Never say a message, caller or person is genuine, authentic, real or safe.
    You cannot know that.
  - Never tell them to share an OTP, code, PIN or password with anyone, for any
    reason. There is no legitimate exception.
  - If they cannot verify, that is not a reason to proceed - say so plainly.
  - If they say money has already gone, tell them to call 1930 immediately and
    their bank's fraud line, and file at cybercrime.gov.in. Speed decides recovery.
  - If new information makes things clearly worse (an OTP is now being asked for,
    they are told not to hang up, a new account appears, secrecy is demanded),
    say so directly and tell them to end the call.

NEVER tell them to search online for an official phone number or address.
Scammers buy search placement for fake "police", "bank" and "helpline" numbers,
so searching is how people reach a second scammer. Instead route them to a
number that cannot be faked: 112 (police emergency), 1930 (cyber fraud), the
number printed on the back of their own card, or walking into any police station
in person. If you do not know a local address, say that dialling 112 will connect
them and direct them - do not leave them to look it up.

Never answer with "I don't have that information" and stop there. Always give
the action that works regardless of location.

Specific facts you should state plainly when relevant:
  - There is no such thing as a "digital arrest". No Indian police force, court,
    CBI, ED or customs officer conducts arrests, interrogations or case
    verification over a phone or video call. If someone is doing that, the case
    does not exist.
  - Police and courts never take bail, fines or fees by UPI, gift card or
    transfer to a personal account. Bail is set by a court, in person.
  - A real summons arrives in writing, and you can walk into a station to check it.

Watch for conversational fraud across what they tell you: knowing private details
they should not know, building rapport before asking for anything, escalating
requests, pushing to another channel, and the real ask arriving only at the end.

Return ONLY valid JSON:
{
  "reply": "your 2-4 sentence answer to them",
  "risk_direction": "worse" or "same" or "clearer",
  "urgent": true or false
}
urgent = true only when they should stop and end the call right now, or when
money has already been sent.
"""


def _looks_like_conversation(text: str) -> bool:
    """Heuristic: is this transcript a back-and-forth rather than one message?

    Used to route long call recordings to conversation-aware analysis without
    asking the user to categorise their own input.
    """
    t = text.strip()
    if len(t.split()) > 90:
        return True
    markers = ("hello?", "yes ma'am", "yes sir", "hold on", "one moment",
               "as i said", "as i mentioned", "speaking", "who is this")
    hits = sum(1 for m in markers if m in t.lower())
    return hits >= 2


def analyze_auto(text: str) -> dict:
    """Route to single-message or conversation analysis automatically."""
    if _looks_like_conversation(text):
        return analyze_conversation([text])
    return analyze(text)


def followup(original: str, prior: dict, history: list, message: str) -> dict:
    """One turn of live guidance. history = [{'role':'user'|'assistant','text':...}]"""
    global LAST_USAGE
    convo = "\n".join(
        f"{'They asked' if h['role']=='user' else 'You said'}: {h['text']}"
        for h in history[-8:]
    )
    context = (
        f"The message originally assessed was:\n{original[:1200]}\n\n"
        f"Your assessment was: {prior.get('risk_label','')} - "
        f"{prior.get('explanation','')}\n\n"
        + (f"So far in this conversation:\n{convo}\n\n" if convo else "")
        + f"They now say:\n{message}"
    )
    resp = _client.chat.completions.create(
        model=MODEL, temperature=0.2,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": FOLLOWUP_PROMPT},
                  {"role": "user", "content": context}],
    )
    try:
        LAST_USAGE = int(resp.usage.total_tokens)
    except Exception:
        LAST_USAGE = 0
    try:
        out = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        out = {"reply": "I could not process that. If you are unsure, do not send "
                        "money or share any code - end the call and check independently.",
               "risk_direction": "same", "urgent": False}

    # same guardrail discipline applies to live advice
    if _field_certifies(str(out.get("reply", ""))):
        out["reply"] = ("I cannot tell you this is real - that is not something I can "
                        "confirm. Verify independently before doing anything, and do "
                        "not share any code.")
    out.setdefault("risk_direction", "same")
    out.setdefault("urgent", False)
    return out