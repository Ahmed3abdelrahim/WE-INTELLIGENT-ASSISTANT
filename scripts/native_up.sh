#!/bin/bash
# Native (no-Docker) equivalent of `make up`, used on this laptop because no Docker daemon
# is available (see docs/decisions.md). Starts the same code/binaries as host processes on
# the same ports the containerized services would use, so the backend/frontend are unaware
# of the difference.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORE="/home/ahmed/we-assistant-store"
PIDFILE="$REPO_ROOT/data/logs/native_pids.txt"
LOGDIR="$REPO_ROOT/data/logs"
mkdir -p "$LOGDIR"
: > "$PIDFILE"

source /home/ahmed/miniforge3/etc/profile.d/conda.sh
conda activate we

export HF_HOME="$STORE/hf-cache"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export LLM_THREADS="${LLM_THREADS:-8}"

start() {
  local name="$1"; shift
  echo "[native_up] starting $name: $*"
  nohup "$@" >"$LOGDIR/$name.log" 2>&1 &
  echo "$name:$!" >> "$PIDFILE"
}

# 1. Qdrant
QDRANT_STORAGE="$REPO_ROOT/data/qdrant_storage"
mkdir -p "$QDRANT_STORAGE"
QDRANT__STORAGE__STORAGE_PATH="$QDRANT_STORAGE" QDRANT__SERVICE__HTTP_PORT=6333 \
  start qdrant "$STORE/bin/qdrant"

# 2. LLM (llama-server, CPU)
LLM_GGUF="$REPO_ROOT/models/llm/qwen3-4b-q4_k_m.gguf"
if [ -f "$LLM_GGUF" ]; then
  LD_LIBRARY_PATH="$STORE/bin/llama-b11323:$CONDA_PREFIX/lib" \
    start llm "$STORE/bin/llama-b11323/llama-server" \
    --model "$LLM_GGUF" --jinja --ctx-size 4096 --threads "$LLM_THREADS" \
    --host 127.0.0.1 --port 8080 \
    --reasoning off
else
  echo "[native_up] WARNING: $LLM_GGUF not found, skipping llm (run 'make models' first)"
fi

# 3. ASR service
export ASR_MODEL="${ASR_MODEL:-large-v3-turbo}"
export ASR_MODEL_PATH="$REPO_ROOT/models/asr/${ASR_MODEL}"
export ASR_DEVICE="${ASR_DEVICE:-cpu}"
export ASR_COMPUTE_TYPE="${ASR_COMPUTE_TYPE:-int8}"
(cd "$REPO_ROOT/services/asr" && start asr uvicorn server:app --host 0.0.0.0 --port 8001) || true

# 4. TTS service
export TTS_MODELS_DIR="$REPO_ROOT/models/tts"
(cd "$REPO_ROOT/services/tts" && start tts uvicorn server:app --host 0.0.0.0 --port 8002) || true

# 5. Backend
export LLM_PROVIDER="${LLM_PROVIDER:-local}"
export LLM_LOCAL_URL="http://127.0.0.1:8080/v1"
export LLM_LOCAL_MODEL="${LLM_LOCAL_MODEL:-qwen3-4b-q4_k_m}"
export ASR_URL="http://127.0.0.1:8001"
export TTS_URL="http://127.0.0.1:8002"
export QDRANT_URL="http://127.0.0.1:6333"
export CONFIG_DIR="$REPO_ROOT/config"
export DATA_DIR="$REPO_ROOT/data"
export MODELS_ENCODER_DIR="$REPO_ROOT/models/encoder"
# Port 8000 is unusable natively on this machine (docs/decisions.md: a root-owned
# WSL interop listener occupies it); the native backend uses 8020 instead.
(cd "$REPO_ROOT/backend" && start backend uvicorn app.main:app --host 127.0.0.1 --port 8020)

echo "[native_up] all processes started, pids in $PIDFILE"
echo "[native_up] waiting 5s before returning..."
sleep 5
