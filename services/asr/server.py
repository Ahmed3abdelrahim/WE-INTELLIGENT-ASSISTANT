import os
import tempfile
import time

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel

MODEL_NAME = os.environ.get("ASR_MODEL", "large-v3-turbo")
MODEL_PATH = os.environ.get("ASR_MODEL_PATH", f"/models/{MODEL_NAME}")
DEVICE = os.environ.get("ASR_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("ASR_COMPUTE_TYPE", "int8")
CPU_THREADS = int(os.environ.get("ASR_THREADS", os.environ.get("LLM_THREADS", "4")))

app = FastAPI()
_model = None
_model_source = MODEL_PATH if os.path.isdir(MODEL_PATH) else MODEL_NAME


def get_model():
    global _model
    if _model is None:
        _model = WhisperModel(
            _model_source,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            cpu_threads=CPU_THREADS if DEVICE == "cpu" else 0,
        )
    return _model


@app.get("/health")
def health():
    try:
        get_model()
        return {"status": "ok", "model": MODEL_NAME, "device": DEVICE, "compute_type": COMPUTE_TYPE}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    hotwords: str | None = Form(default=None),
    beam_size: int = Form(default=1),
):
    model = get_model()
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    t0 = time.time()
    try:
        try:
            segments_iter, info = model.transcribe(
                tmp_path,
                task="transcribe",
                language=language or None,
                vad_filter=True,
                condition_on_previous_text=False,
                beam_size=beam_size,
                hotwords=hotwords or None,
            )
        except ValueError:
            # faster-whisper raises ValueError during language auto-detection when VAD
            # finds literally no speech in the clip (e.g. silence, pure tone/noise) — the
            # backend's own silence-rejection logic (SPEC.md section 7) handles this via
            # no_speech_prob, so report it as a confident no-speech result, not a 500.
            return {
                "text": "",
                "language": language or None,
                "duration_s": round(time.time() - t0, 3),
                "no_speech_prob": 1.0,
                "segments": [],
                "ms": round((time.time() - t0) * 1000, 1),
            }
        segments = []
        no_speech_probs = []
        text_parts = []
        for seg in segments_iter:
            segments.append(
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "no_speech_prob": seg.no_speech_prob,
                }
            )
            no_speech_probs.append(seg.no_speech_prob)
            text_parts.append(seg.text)
        duration_s = time.time() - t0
        mean_no_speech = sum(no_speech_probs) / len(no_speech_probs) if no_speech_probs else 1.0
        text = "".join(text_parts).strip()
        return {
            "text": text,
            "language": info.language,
            "duration_s": round(info.duration, 3),
            "no_speech_prob": round(mean_no_speech, 4),
            "segments": segments,
            "ms": round(duration_s * 1000, 1),
        }
    finally:
        os.unlink(tmp_path)
