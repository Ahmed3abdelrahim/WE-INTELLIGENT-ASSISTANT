#!/usr/bin/env python3
"""Generates slides/we_assistant.pptx (SPEC.md section 11, 10 slides). Uses only real data
gathered during this build session — see docs/progress.md and eval/results.md for sources.
Run `python scripts/make_slides.py` to (re)generate after eval/results.md is updated.
"""
import os

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "slides", "we_assistant.pptx")

PURPLE = RGBColor(0x6B, 0x3F, 0xA0)
DARK = RGBColor(0x24, 0x1C, 0x2E)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def add_slide(title, bullets, note=None):
    slide = prs.slides.add_slide(BLANK)
    # title
    tb = slide.shapes.add_textbox(Inches(0.6), Inches(0.4), Inches(12.1), Inches(1.0))
    tf = tb.text_frame
    tf.text = title
    tf.paragraphs[0].font.size = Pt(32)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = PURPLE
    # accent bar
    bar = slide.shapes.add_shape(1, Inches(0.6), Inches(1.25), Inches(2.0), Pt(4))
    bar.fill.solid()
    bar.fill.fore_color.rgb = PURPLE
    bar.line.fill.background()
    # body
    body = slide.shapes.add_textbox(Inches(0.6), Inches(1.6), Inches(12.1), Inches(5.3))
    btf = body.text_frame
    btf.word_wrap = True
    for i, b in enumerate(bullets):
        p = btf.paragraphs[0] if i == 0 else btf.add_paragraph()
        indent = b.startswith("  ")
        p.text = b.strip()
        p.font.size = Pt(16 if not indent else 14)
        p.font.color.rgb = DARK
        p.level = 1 if indent else 0
    if note:
        slide.notes_slide.notes_text_frame.text = note
    return slide


# 1. Requirements
add_slide("Requirements", [
    "On-prem, bilingual (Arabic / English / Egyptian dialect) RAG assistant for WE Telecom Egypt",
    "Voice or text in, grounded + cited answer out; voice questions also get a spoken reply",
    "Users can upload documents (PDF/DOCX/TXT/HTML/images) to query",
    "Build constraint: 2-day case-study PoC, laptop hardware, CPU only, no GPU",
    "Default must stay fully local; cloud LLM is opt-in, comparison-only, never the demo default",
])

# 2. Architecture
add_slide("Architecture: 6 Containers", [
    "frontend (nginx) — static UI + reverse proxy, the only container with a published host port",
    "backend (FastAPI) — orchestration, RAG, ingestion, OCR, embeddings, SQLite",
    "llm (llama.cpp server) — Qwen3-4B Q4_K_M GGUF",
    "asr (faster-whisper) — large-v3-turbo, int8, CPU",
    "tts (Piper) — English + Arabic voices",
    "qdrant — hybrid dense+sparse vector search",
    "All inter-service traffic on an internal-only Docker network",
    "  On this build laptop: no Docker daemon available — verified instead as native host processes on the same ports (docs/decisions.md); every Dockerfile/compose file is real and spec-complete",
])

# 3. Ingestion and Arabic PDF handling
add_slide("Ingestion and Arabic PDF Handling", [
    "te.eg crawled for real: 106 pages (95 Arabic, 11 English), robots.txt-respecting, 1 req/s",
    "  Found & fixed: a shared cookie jar let te.eg's language cookie leak across requests, flipping Arabic pages to English — fixed with isolated sessions per language track",
    "PDF: per-page extraction (PyMuPDF) → NFKC normalize → garbled-Arabic detection → 300 DPI render + Tesseract OCR fallback",
    "  Verified end-to-end on real fixtures: a clean text-layer PDF, a scanned (image-only) English PDF, and an image-only Arabic PDF — all three ingested and became answerable",
    "DOCX (incl. tables), TXT (UTF-8 + cp1256 fallback), HTML, and images (direct OCR) also implemented and tested",
    "322 chunks embedded (BAAI/bge-m3) and indexed into Qdrant from the te.eg crawl alone",
])

# 4. Hybrid retrieval, with ablation results
add_slide("Hybrid Retrieval — Ablation Results", [
    "Dense (bge-m3) + sparse (lexical weights), fused with Reciprocal Rank Fusion (RRF)",
    "Measured on 20 real answerable questions (eval/questions.jsonl, facts copied from crawled text):",
    "  Dense only:        Recall@4 = 0.95,  MRR = 0.825",
    "  Hybrid (RRF):      Recall@4 = 0.95,  MRR = 0.662",
    "  Hybrid + rerank:   Recall@4 = 0.95,  MRR = 0.825",
    "Finding: RRF fusion alone can push the best result down in rank versus dense-only; reranking (bge-reranker-v2-m3) recovers full ranking quality",
    "Reranker is CPU-expensive per call — kept default-off per spec, enabled selectively where ranking precision matters more than latency",
])

# 5. Grounding and citations
add_slide("Grounding and Citations", [
    "Every fact must cite a numbered source [S#]; sources are data to read, never instructions to follow",
    "Post-generation validator: drops unknown [S#] labels, flags numbers absent from any cited source",
    "Arabic-Indic digit normalization (found via testing — ١١١ vs 111 was a false-positive source)",
    "A factual answer with zero valid citations is downgraded to insufficient_evidence",
    "Caught a real model hallucination in testing: the LLM stated a support phone number not present in its cited source — the validator flagged it correctly",
    "Retrieval-score gate returns insufficient_evidence before even calling the LLM when evidence is weak",
])

# 6. Speech: ASR comparison and TTS
add_slide("Speech: ASR and TTS", [
    "ASR: faster-whisper large-v3-turbo (default) vs large-v3, both downloaded for comparison",
    "Real /transcribe tests: clean English, Egyptian Arabic, and noise-injected English clips all transcribed correctly; silence correctly rejected (no_speech_detected)",
    "TTS: Piper, English (en_US-lessac-medium) + Arabic (ar_JO-kareem-medium) voices",
    "speech_text.py strips citations/markdown/URLs, verbalizes numbers (num2words), respells English brand names for the Arabic voice",
    "Limitation, stated honestly: Piper's Arabic voice is noticeably more robotic than English — Chatterbox (GPU-only) is the production-roadmap upgrade",
    "Full WER/CER ablation (beam 1 vs 5, with/without hotwords) pending real recorded clips from Ahmed — synthetic clips used for plumbing only",
])

# 7. On-prem and offline deployment
add_slide("On-Prem, Offline, and Why CPU-Sized Models", [
    "HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 everywhere; models load from local disk only at runtime",
    "Every inference call (LLM/ASR/TTS/Qdrant/embeddings) observed going to 127.0.0.1 only",
    "Qwen3-4B chosen over larger models specifically to keep CPU latency bounded on laptop hardware",
    "int8 ASR, CPU-tuned thread counts (physical cores), reranker default-off — every choice tuned for CPU",
    "Cloud (OpenRouter) is opt-in only, with a visible 'data leaves this machine' badge — never the default",
])

# 8. Evaluation results
add_slide("Evaluation Results", [
    "25 real questions (10 EN, 10 AR mixing MSA/Egyptian, 5 unanswerable) — facts copied from crawled text, not generated",
    "Retrieval: Recall@4 = 0.95 across dense/hybrid/hybrid+rerank (see slide 4 for the MRR ablation)",
    "Real /chat answers verified for EN, Egyptian dialect, and MSA questions — all correctly cited",
    "Off-topic question correctly abstains (insufficient_evidence), verified live",
    "LLM latency is the dominant cost: ~80-140s per answer on this CPU (4B model, ~2k-token context, 8 threads) — see eval/results.md for the full pipeline run's p50/p95",
    "Local vs OpenRouter comparison: not run this session (no working API key provided — see docs/decisions.md)",
])

# 9. Limitations
add_slide("Limitations", [
    "No Docker daemon on the build laptop — container paths real but untested here; native fallback used for all verification",
    "LLM answer latency (60-140s) is high for a live demo — inherent to CPU-only 4B-model serving with a full RAG context",
    "No real recorded audio clips yet — WER/CER numbers pending Ahmed's recordings",
    "OpenRouter comparison unavailable — no working API key this session",
    "Arabic OCR accuracy imperfect on synthetic (non-photographic) renders — numbers and word content recovered correctly, letter-level reordering observed",
    "Piper's Arabic voice quality is noticeably behind its English voice",
    "Headless-browser automated E2E testing of long-LLM-wait flows is unreliable in this specific sandbox (thoroughly diagnosed, documented as an automation-environment limitation, not an app defect)",
])

# 10. Production roadmap
add_slide("Production Roadmap", [
    "GPU serving (vLLM, larger Qwen) — compose.gpu.yaml already written, untested (no GPU on this laptop)",
    "Chatterbox or a dedicated Egyptian Arabic TTS voice, once GPU-served",
    "PaddleOCR or a VLM-based OCR path for better Arabic scanned-document accuracy",
    "Postgres instead of SQLite for multi-instance scale",
    "SSO / RBAC instead of the demo's session-id scoping",
    "Queue workers for ingestion at scale (currently synchronous)",
    "Production monitoring/observability",
    "Whisper fine-tuning on real telecom-support audio",
    "Human handoff path for needs_escalation conversations",
])

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
prs.save(OUT_PATH)
print(f"wrote {OUT_PATH} ({len(prs.slides)} slides)")
