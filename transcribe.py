"""
transcribe.py — turns audio/video into text so the analyzer can read it.

Uses Groq's hosted Whisper (whisper-large-v3). We chose the hosted API over
local faster-whisper for reliability/speed on Windows; the pipeline is
model-agnostic, so swapping to on-device Whisper later (a privacy win in
production) is a one-function change.
"""
import os
import subprocess
import tempfile
from groq import Groq

_client = Groq(api_key=os.environ["GROQ_API_KEY"])


def transcribe_audio(path: str) -> str:
    """Transcribe an audio file to text."""
    with open(path, "rb") as f:
        r = _client.audio.transcriptions.create(
            file=(os.path.basename(path), f.read()),
            model="whisper-large-v3",
        )
    return (r.text or "").strip()


def extract_audio_from_video(video_path: str) -> str:
    """Pull the audio track out of a video into a temp .wav, return its path."""
    out = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out],
        check=True, capture_output=True,
    )
    return out


def transcribe_media(path: str, input_type: str) -> str:
    """Dispatch by input type. Video -> extract audio -> transcribe."""
    if input_type == "video":
        audio = extract_audio_from_video(path)
        try:
            return transcribe_audio(audio)
        finally:
            if os.path.exists(audio):
                os.remove(audio)
    return transcribe_audio(path)