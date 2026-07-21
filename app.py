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


@app.get("/", response_class=HTMLResponse)
def home():
    # Read fresh each request so you can edit the page without restarting.
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


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

    # 2) Analyse (same brain the scoring script uses).
    result = analyzer.analyze(content)
    latency = round(time.time() - t0, 2)

    # 3) Log it.
    db.log_analysis(input_type, content, result, latency)

    # 4) Respond. `content` is returned so the UI can show what was transcribed.
    return {"content": content, "result": result, "latency_seconds": latency}


@app.get("/history")
def history():
    """Recent analyses — handy for the demo."""
    return db.recent()