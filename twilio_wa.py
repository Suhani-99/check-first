"""
twilio_wa.py — WhatsApp via Twilio.

Same pipeline, second provider. Twilio is a WhatsApp Business Solution Provider:
it holds the Meta relationship, so its sandbox needs no business verification, no
registered phone number and no payment method — the gates that block direct Cloud
API access without a company entity.

Differences from Meta's Cloud API, all handled here:
  - Twilio POSTs form-encoded fields, not JSON
  - media arrives as MediaUrl0..N, fetched with HTTP basic auth
  - replies go out through Twilio's REST API
  - the message body limit is 1600 characters, not 4096

Environment
-----------
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_WHATSAPP_FROM    e.g. whatsapp:+14155238886  (sandbox number)
"""
import os
import time
import tempfile

import requests

import analyzer
import transcribe
import db
from whatsapp import format_result, WELCOME, _is_followup

SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
AUTH = os.environ.get("TWILIO_AUTH_TOKEN", "")
_raw_from = os.environ.get("TWILIO_WHATSAPP_FROM", "+14155238886").strip()
# Twilio rejects the send unless BOTH From and To carry the channel prefix.
# Normalise here so a missing "whatsapp:" in configuration cannot break sending.
FROM = _raw_from if _raw_from.startswith("whatsapp:") else f"whatsapp:{_raw_from}"

API = "https://api.twilio.com/2010-04-01"
MAX_BODY = 1500          # Twilio hard limit is 1600; leave headroom


def configured() -> bool:
    return bool(SID and AUTH)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def send_text(to: str, body: str) -> dict:
    """Send one WhatsApp message. Long bodies are split rather than truncated —
    the escalation ladder must never be cut off halfway."""
    if not to.startswith("whatsapp:"):
        to = "whatsapp:" + to

    for chunk in _split(body, MAX_BODY):
        r = requests.post(
            f"{API}/Accounts/{SID}/Messages.json",
            auth=(SID, AUTH),
            data={"From": FROM, "To": to, "Body": chunk},
            timeout=20,
        )
        if r.status_code >= 300:
            print(f"[twilio] send failed {r.status_code}: {r.text[:200]}")
        time.sleep(0.3)          # keep ordering on the recipient's phone
    return {"ok": True}


def _split(text: str, limit: int) -> list[str]:
    """Split on blank lines so a message never breaks mid-sentence."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > limit and cur:
            parts.append(cur.strip())
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur.strip():
        parts.append(cur.strip())
    return parts


# ---------------------------------------------------------------------------
# Receiving media — Twilio hosts the file behind basic auth
# ---------------------------------------------------------------------------
def download_media(url: str, content_type: str) -> str:
    suffix = ".mp4" if "video" in (content_type or "") else ".ogg"
    blob = requests.get(url, auth=(SID, AUTH), timeout=60).content
    path = tempfile.mktemp(suffix=suffix)
    with open(path, "wb") as f:
        f.write(blob)
    return path


# ---------------------------------------------------------------------------
# The webhook
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}


def handle_message(form: dict) -> None:
    """Process one inbound Twilio message. Called in the background so the
    webhook can return immediately — Twilio times out at 15 seconds and
    transcription plus analysis can take longer."""
    frm = form.get("From", "")
    body = (form.get("Body") or "").strip()
    n_media = int(form.get("NumMedia", "0") or 0)
    t0 = time.time()
    tmp = None
    transcript = ""

    try:
        if n_media > 0:
            url = form.get("MediaUrl0")
            ctype = form.get("MediaContentType0", "")
            kind = "video" if "video" in ctype else "voice"
            tmp = download_media(url, ctype)
            content = transcribe.transcribe_media(tmp, kind)
            transcript, input_type = content, kind

        elif body:
            if body.lower() in {"hi", "hello", "hey", "start", "namaste"}:
                send_text(frm, WELCOME)
                return
            prior = _sessions.get(frm)
            if prior and _is_followup(body):
                out = analyzer.followup(prior["original"], prior["result"],
                                        prior["turns"], body)
                prior["turns"] += [{"role": "user", "text": body},
                                   {"role": "assistant", "text": out.get("reply", "")}]
                db.log_followup(prior["session_id"], len(prior["turns"]) // 2,
                                body, out, round(time.time() - t0, 2))
                reply = out.get("reply", "")
                send_text(frm, ("❗ " + reply) if out.get("urgent") else reply)
                return
            content, input_type = body, "text"

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

        session_id = db.log_analysis(f"twilio:{input_type}", content, result, latency)
        _sessions[frm] = {"session_id": session_id, "original": content,
                          "result": result, "turns": []}

        send_text(frm, format_result(result, transcript))

    except Exception as e:                                   # noqa: BLE001
        print(f"[twilio] {type(e).__name__}: {e}")
        if frm:
            send_text(frm, "Something went wrong reading that. If you're unsure, "
                           "send no money and share no codes until you've spoken to "
                           "the person on a number you already have.")
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)