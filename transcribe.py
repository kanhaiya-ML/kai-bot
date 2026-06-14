import tempfile
from groq import Groq
import os
from dotenv import load_dotenv
load_dotenv()


client = Groq(api_key=os.getenv("GROQ_API_KEY"))

async def transcribe_audio(audio_file: bytes, filename: str):
    suffix = "." + filename.split(".")[-1]

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_file)
        tmp_path = tmp.name

    try:
        with open(tmp_path,"rb") as f:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=("audio.webm",f)
            )
        return {"text": transcription.text}
    finally:
        os.remove(tmp_path)

