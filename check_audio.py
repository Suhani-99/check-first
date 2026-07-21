import os
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])

with open("audio/S01.wav", "rb") as f:
    result = client.audio.transcriptions.create(
        file=("S01.wav", f.read()),
        model="whisper-large-v3",
    )

print("Transcript:")
print(" ", result.text)