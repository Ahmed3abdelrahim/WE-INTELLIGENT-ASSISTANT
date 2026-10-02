import io
import os
import wave

from fastapi import FastAPI, Response
from pydantic import BaseModel
from piper import PiperVoice

MODELS_DIR = os.environ.get("TTS_MODELS_DIR", "/models")
VOICE_AR_PATH = os.environ.get("TTS_VOICE_AR", os.path.join(MODELS_DIR, "ar_voice.onnx"))
VOICE_EN_PATH = os.environ.get("TTS_VOICE_EN", os.path.join(MODELS_DIR, "en_US-lessac-medium.onnx"))

app = FastAPI()
_voices: dict[str, PiperVoice] = {}


def get_voice(lang: str) -> PiperVoice:
    path = VOICE_AR_PATH if lang == "ar" else VOICE_EN_PATH
    if path not in _voices:
        _voices[path] = PiperVoice.load(path)
    return _voices[path]


class SynthRequest(BaseModel):
    text: str
    lang: str = "en"


@app.get("/health")
def health():
    ok_ar = os.path.isfile(VOICE_AR_PATH)
    ok_en = os.path.isfile(VOICE_EN_PATH)
    status = "ok" if (ok_ar and ok_en) else "degraded"
    return {"status": status, "ar_voice": ok_ar, "en_voice": ok_en}


@app.post("/synthesize")
def synthesize(req: SynthRequest):
    voice = get_voice(req.lang)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize(req.text, wav_file)
    return Response(content=buf.getvalue(), media_type="audio/wav")
