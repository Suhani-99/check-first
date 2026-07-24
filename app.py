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

from fastapi import FastAPI, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

import analyzer
import transcribe
import db

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


@app.get("/history")
def history():
    """Recent analyses with their follow-up threads — the audit trail."""
    return db.recent()


@app.get("/stats")
def stats():
    """Aggregate counts, including which database backend is live."""
    return db.stats()