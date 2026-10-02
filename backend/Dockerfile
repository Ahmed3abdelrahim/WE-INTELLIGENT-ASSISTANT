FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-ara ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# requirements.txt pins torch==...+cpu and declares --extra-index-url for the CPU wheel index
# (see docs/decisions.md — the default PyPI torch wheel bundles CUDA and is 1-2GB+ for no reason
# on a CPU-only deployment).
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
