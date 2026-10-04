import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
if not CONFIG_DIR.exists():
    # native (non-container) run: repo config/ dir
    CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
if not DATA_DIR.exists():
    DATA_DIR = Path(__file__).resolve().parents[2] / "data"

_settings_path = CONFIG_DIR / "settings.yaml"
with open(_settings_path, encoding="utf-8") as f:
    SETTINGS = yaml.safe_load(f)


class Config:
    # --- LLM ---
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "local")
    LLM_LOCAL_URL = os.environ.get("LLM_LOCAL_URL", "http://localhost:8080/v1")
    LLM_LOCAL_MODEL = os.environ.get("LLM_LOCAL_MODEL", "qwen3-4b-q4_k_m")
    LLM_THREADS = int(os.environ.get("LLM_THREADS", "8"))

    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen3-4b")
    OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    @property
    def openrouter_available(self) -> bool:
        return bool(self.OPENROUTER_API_KEY)

    # --- ASR / TTS ---
    ASR_URL = os.environ.get("ASR_URL", "http://localhost:8001")
    TTS_URL = os.environ.get("TTS_URL", "http://localhost:8002")

    # --- Qdrant ---
    QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
    QDRANT_COLLECTION = "we_chunks"

    # --- Retrieval / pipeline ---
    RERANKER_ENABLED = os.environ.get("RERANKER_ENABLED", "false").lower() == "true"
    # "cpu" (default) or "cuda" / "cuda:0"; fp16 is used automatically on GPU.
    EMBED_DEVICE = os.environ.get("EMBED_DEVICE", "cpu")
    DENSE_TOP_K = SETTINGS["retrieval"]["dense_top_k"]
    SPARSE_TOP_K = SETTINGS["retrieval"]["sparse_top_k"]
    FINAL_TOP_K = SETTINGS["retrieval"]["final_top_k"]
    MIN_SCORE_THRESHOLD = SETTINGS["retrieval"]["min_score_threshold"]
    RERANK_CANDIDATE_POOL = SETTINGS["retrieval"]["rerank_candidate_pool"]

    MAX_CONTEXT_TOKENS = SETTINGS["context"]["max_context_tokens"]
    MAX_ANSWER_TOKENS = SETTINGS["context"]["max_answer_tokens"]
    MAX_HISTORY_TURNS = SETTINGS["history"]["max_turns"]

    MAX_FILE_MB = SETTINGS["uploads"]["max_file_mb"]
    MAX_PDF_PAGES = SETTINGS["uploads"]["max_pdf_pages"]

    # --- Paths ---
    MODELS_ENCODER_DIR = Path(os.environ.get("MODELS_ENCODER_DIR", "/models/encoder"))
    if not MODELS_ENCODER_DIR.exists():
        MODELS_ENCODER_DIR = Path(__file__).resolve().parents[2] / "models" / "encoder"

    DB_PATH = DATA_DIR / "app.db"
    UPLOADS_DIR = DATA_DIR / "uploads"
    AUDIO_DIR = DATA_DIR / "audio"
    LOGS_DIR = DATA_DIR / "logs"
    WEBSITE_DIR = DATA_DIR / "website"


config = Config()

for d in (config.UPLOADS_DIR, config.AUDIO_DIR, config.LOGS_DIR, config.WEBSITE_DIR):
    d.mkdir(parents=True, exist_ok=True)
