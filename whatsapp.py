"""
whatsapp.py — WhatsApp Cloud API front door.

This is a NEW CHANNEL, not a new system. A WhatsApp message runs through exactly
the same analyzer, guardrail and database as the web form. Nothing in the
analysis path changes, which is the point: the architecture separates *how a
message arrives* from *how it is judged*.

Flow
----
    user forwards a suspicious voice note to the business number
        -> Meta POSTs the event to  /whatsapp
        -> we download the media from Meta (a second, authenticated call)
        -> transcribe.py            (same as web)
        -> analyzer.analyze()       (same as web)
        -> format for plain text    (WhatsApp has no HTML)
        -> Meta Cloud API sends the reply back into the thread

Environment
-----------
    WHATSAPP_TOKEN          permanent System User token
    WHATSAPP_PHONE_ID       the business number's phone number ID
    WHATSAPP_VERIFY_TOKEN   a string you choose; Meta echoes it during setup
"""
import os
import time
import tempfile

import requests

import analyzer
import transcribe
import db

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")

# WhatsApp hard-limits a message body to 4096 characters.
MAX_BODY = 4000


def configured() -> bool:
    return bool(TOKEN and PHONE_ID)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def send_text(to: str, body: str) -> dict:
    """Send a plain-text WhatsApp message."""
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY - 3] + "..."
    r = requests.post(
        f"{GRAPH}/{PHONE_ID}/messages",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"},
        json={"messaging_product": "whatsapp", "to": to,
              "type": "text", "text": {"preview_url": False, "body": body}},
        timeout=20,
    )
    return r.json()


# ---------------------------------------------------------------------------
# Receiving media
#
# Meta does not deliver the file. The webhook carries a media ID; the file is
# fetched in two further authenticated steps: resolve the ID to a temporary URL,
# then download that URL with the same bearer token.
# ---------------------------------------------------------------------------
def download_media(media_id: str, suffix: str) -> str:
    meta = requests.get(
        f"{GRAPH}/{media_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=20,
    ).json()
    url = meta.get("url")
    if not url:
        raise RuntimeError(f"no media url returned: {meta}")

    blob = requests.get(
        url,
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=60,
    ).content

    path = tempfile.mktemp(suffix=suffix)
    with open(path, "wb") as f:
        f.write(blob)
    return path


# ---------------------------------------------------------------------------
# Formatting for a phone screen
#
# No HTML on WhatsApp. *bold* and _italic_ only, and the reader is frightened,
# so the shape matters: verdict, why, then what to do.
# ---------------------------------------------------------------------------
def format_result(result: dict, transcript: str = "") -> str:
    p = result.get("verification_plan", {}) or {}
    lvl = str(result.get("risk_level", "")).lower()
    mark = {"high": "🔴", "medium": "🟠"}.get(lvl, "🔵")

    out = [f"{mark} *{result.get('risk_label', 'assessed')}*",
           "_This reads the pressure in the message. It is not a ruling on "
           "whether the message is real._"]

    if transcript:
        short = transcript if len(transcript) < 320 else transcript[:317] + "..."
        out += ["", f"*What we heard*", f"_{short}_"]

    signals = result.get("signals_detected") or []
    if signals:
        out += ["", "*What we noticed*"]
        for s in signals[:6]:
            ev = s.get("evidence", "")
            out.append(f"• {s.get('signal')}" + (f' — "{ev}"' if ev else ""))

    if result.get("explanation"):
        out += ["", "*What that means*", result["explanation"]]

    out += ["", "*What to do*", f"1. {p.get('primary', result.get('verification_step', ''))}"]
    if p.get("identity_check"):
        out.append(f"2. {p['identity_check']}")
    if p.get("if_unreachable"):
        out.append(f"3. {p['if_unreachable']}")
    if p.get("if_cannot_verify"):
        out.append(f"❗ {p['if_cannot_verify']}")
    if p.get("if_already_sent"):
        out += ["", f"*If money has already gone*", p["if_already_sent"]]

    out += ["", "_Reply here to tell us what happens next._"]
    return "\n".join(out)


WELCOME = (
    "👋 *Check First*\n\n"
    "Forward me anything that feels off — a message, a voice note or a video — "
    "and I'll tell you what pressure is being used on you and how to check.\n\n"
    "_I will never tell you a message is genuine. Nothing can reliably do that, "
    "and a wrong reassurance is what costs families their savings._"
)


# ---------------------------------------------------------------------------
# The webhook
# ---------------------------------------------------------------------------
# One conversation per sender, so replies carry context like the web chat.
_sessions: dict[str, dict] = {}


def verify(mode: str, token: str, challenge: str):
    """Meta's setup handshake: echo the challenge if the token matches."""
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge) if str(challenge).isdigit() else challenge
    return None


def handle_event(payload: dict) -> None:
    """Process one webhook payload. Errors are contained per message so a
    single bad input cannot take down the endpoint."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in (change.get("value", {}) or {}).get("messages", []) or []:
                try:
                    _handle_message(msg)
                except Exception as e:                      # noqa: BLE001
                    frm = msg.get("from")
                    if frm:
                        send_text(frm, "Something went wrong reading that. "
                                       "If you're unsure, send no money and share "
                                       "no codes until you've spoken to the person "
                                       "on a number you already have.")
                    print(f"[whatsapp] {type(e).__name__}: {e}")


def _handle_message(msg: dict) -> None:
    frm = msg.get("from")
    mtype = msg.get("type")
    t0 = time.time()
    tmp = None
    transcript = ""

    try:
        if mtype == "text":
            body = (msg.get("text", {}) or {}).get("body", "").strip()
            if not body:
                return
            # Greetings and follow-ups are conversation, not a new case.
            if body.lower() in {"hi", "hello", "hey", "start", "hi!", "namaste"}:
                send_text(frm, WELCOME)
                return
            prior = _sessions.get(frm)
            if prior and _is_followup(body):
                _do_followup(frm, prior, body, t0)
                return
            content, input_type = body, "text"

        elif mtype in ("audio", "voice"):
            media = msg.get(mtype, {}) or {}
            tmp = download_media(media.get("id"), ".ogg")
            content = transcribe.transcribe_media(tmp, "voice")
            transcript, input_type = content, "voice"

        elif mtype == "video":
            tmp = download_media((msg.get("video", {}) or {}).get("id"), ".mp4")
            content = transcribe.transcribe_media(tmp, "video")
            transcript, input_type = content, "video"

        elif mtype == "document":
            doc = msg.get("document", {}) or {}
            name = (doc.get("filename") or "").lower()
            suffix = ".mp4" if name.endswith((".mp4", ".mov")) else ".ogg"
            kind = "video" if suffix == ".mp4" else "voice"
            tmp = download_media(doc.get("id"), suffix)
            content = transcribe.transcribe_media(tmp, kind)
            transcript, input_type = content, kind

        else:
            send_text(frm, "Send me a message, a voice note or a short video and "
                           "I'll take a look.")
            return

        if not content:
            send_text(frm, "I couldn't make out any speech in that. Could you send "
                           "it again, or type out what was said?")
            return

        result = analyzer.analyze(content) if input_type == "text" \
            else analyzer.analyze_auto(content)
        latency = round(time.time() - t0, 2)

        session_id = db.log_analysis(f"whatsapp:{input_type}", content, result, latency)
        _sessions[frm] = {"session_id": session_id, "original": content,
                          "result": result, "turns": []}

        send_text(frm, format_result(result, transcript))

    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def _is_followup(body: str) -> bool:
    """Short conversational replies continue the last case; anything longer or
    message-shaped is treated as a new thing to check."""
    if len(body) > 240:
        return False
    cues = ("they ", "he ", "she ", "i ", "now ", "what ", "should ", "but ",
            "it's", "its ", "not picking", "no answer", "already", "sent",
            "asking", "said", "call", "otp", "still")
    low = body.lower()
    return any(c in low for c in cues)


def _do_followup(frm: str, prior: dict, body: str, t0: float) -> None:
    out = analyzer.followup(prior["original"], prior["result"],
                            prior["turns"], body)
    prior["turns"].append({"role": "user", "text": body})
    prior["turns"].append({"role": "assistant", "text": out.get("reply", "")})
    latency = round(time.time() - t0, 2)
    db.log_followup(prior["session_id"], len(prior["turns"]) // 2, body, out, latency)

    reply = out.get("reply", "")
    if out.get("urgent"):
        reply = "❗ " + reply
    send_text(frm, reply)