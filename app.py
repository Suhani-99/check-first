"""
app.py — the backend. One /analyze endpoint handles all three input types
through a single interface (text, voice, video), exactly as the KPI requires.

Run it with:   uvicorn app:app --reload
Then open:     http://127.0.0.1:8000
"""
import os
import time
import shutil
import tempfile
import secrets

from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

import analyzer
import transcribe
import db
import whatsapp

app = FastAPI(title="Scam & Manipulation Shield")
db.init_db()


def _page(name: str) -> str:
    # Read fresh each request so pages can be edited without restarting.
    with open(f"static/{name}", encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def home():
    return _page("index.html")


@app.get("/how", response_class=HTMLResponse)
def how_it_works():
    return _page("how.html")


@app.get("/measure", response_class=HTMLResponse)
def how_we_measure():
    return _page("measure.html")


@app.get("/static/style.css")
def stylesheet():
    from fastapi.responses import Response
    with open("static/style.css", encoding="utf-8") as f:
        return Response(f.read(), media_type="text/css")


@app.post("/analyze")
async def analyze_endpoint(
    input_type: str = Form(...),
    text: str = Form(None),
    file: UploadFile = File(None),
):
    t0 = time.time()

    # 1) Get the text content depending on the input type.
    if input_type == "text":
        content = (text or "").strip()
        if not content:
            return JSONResponse({"error": "No text provided."}, status_code=400)
    else:
        if file is None:
            return JSONResponse({"error": "No file uploaded."}, status_code=400)
        suffix = os.path.splitext(file.filename or "")[1] or (
            ".mp4" if input_type == "video" else ".wav"
        )
        tmp = tempfile.mktemp(suffix=suffix)
        with open(tmp, "wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            content = transcribe.transcribe_media(tmp, input_type)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if not content:
            return JSONResponse(
                {"error": "Could not transcribe any speech from that file."},
                status_code=400,
            )

    # 2) Analyse. Text uses single-message analysis (this is what the committed
    #    evaluation measures). Transcribed audio/video is auto-routed: a long
    #    back-and-forth call recording gets conversation-aware analysis.
    result = analyzer.analyze(content) if input_type == "text" else analyzer.analyze_auto(content)
    latency = round(time.time() - t0, 2)

    # 3) Log it and open a session so follow-up turns attach to this case.
    session_id = db.log_analysis(input_type, content, result, latency)

    # 4) Respond. `content` is returned so the UI can show what was transcribed.
    return {"content": content, "result": result,
            "latency_seconds": latency, "session_id": session_id}


@app.post("/followup")
async def followup_endpoint(
    original: str = Form(...),
    prior_label: str = Form(""),
    prior_explanation: str = Form(""),
    history: str = Form(""),
    message: str = Form(...),
    session_id: str = Form(""),
):
    """One turn of live guidance while the situation is still unfolding."""
    t0 = time.time()
    turns = []
    for line in (history or "").split("|||"):
        if line.startswith("u:"):
            turns.append({"role": "user", "text": line[2:]})
        elif line.startswith("a:"):
            turns.append({"role": "assistant", "text": line[2:]})

    out = analyzer.followup(
        original,
        {"risk_label": prior_label, "explanation": prior_explanation},
        turns,
        message.strip(),
    )
    latency = round(time.time() - t0, 2)
    if session_id:
        db.log_followup(session_id, len(turns) + 1, message.strip(), out, latency)
    return {"result": out, "latency_seconds": latency}


# ---------------------------------------------------------------------------
# The audit trail contains the actual messages people asked us to check —
# private family messages, by definition. It must not be readable by anyone
# who guesses the URL. Access requires ADMIN_TOKEN, set as an environment
# variable on the server and never committed.
#
# If ADMIN_TOKEN is not set, these endpoints are disabled entirely rather than
# left open: failing closed is the only safe default for private data.
# ---------------------------------------------------------------------------
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def _require_admin(token: str):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="Not found.")
    if not secrets.compare_digest(token or "", ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorised.")


@app.get("/history")
def history(token: str = ""):
    """Recent analyses with their follow-up threads — the audit trail."""
    _require_admin(token)
    return db.recent()


@app.get("/stats")
def stats(token: str = ""):
    """Aggregate counts, including which database backend is live."""
    _require_admin(token)
    return db.stats()


# ---------------------------------------------------------------------------
# WhatsApp — a second front door onto the same pipeline.
# GET  verifies ownership of the URL during setup.
# POST receives message events. It returns 200 immediately and processes in the
# background, because Meta retries any webhook that takes too long to respond —
# and transcription plus analysis takes several seconds.
# ---------------------------------------------------------------------------
@app.get("/whatsapp")
def whatsapp_verify(request: Request):
    q = request.query_params
    result = whatsapp.verify(q.get("hub.mode", ""), q.get("hub.verify_token", ""),
                             q.get("hub.challenge", ""))
    if result is None:
        raise HTTPException(status_code=403, detail="Verification failed.")
    return PlainTextResponse(str(result))


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    payload = await request.json()
    background.add_task(whatsapp.handle_event, payload)
    return {"status": "received"}


@app.get("/healthz")
def healthz():
    """Public liveness check — deliberately exposes no user data."""
    return {"ok": True,
            "backend": "postgres" if db.IS_POSTGRES else "sqlite",
            "whatsapp": whatsapp.configured()}