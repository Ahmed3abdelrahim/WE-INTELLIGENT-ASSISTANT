"""RAG pipeline orchestration (SPEC.md section 6). Yields SSE-style (event, data) tuples."""
import time

from .config import config
from .generation import guard, smalltalk
from .generation.answer import regenerate_answer, route_query, stream_answer
from .generation.citations import validate as validate_citations
from .ingestion.arabic import detect_lang
from .ingestion.chunking import count_tokens
from .retrieval.search import hybrid_search
from .store import add_message, get_messages

CLARIFY_SUFFIXES = ("?", "؟")


def resolve_lang(ui_lang: str, text: str) -> str:
    if ui_lang in ("ar", "en"):
        return ui_lang
    return detect_lang(text)


def hits_to_sources(hits: list[dict]) -> list[dict]:
    sources = []
    for i, h in enumerate(hits, start=1):
        url_or_file = h.get("url") or h.get("filename") or ""
        sources.append(
            {
                "label": f"S{i}",
                "title": h.get("title") or "",
                "url": h.get("url"),
                "filename": h.get("filename"),
                "page": h.get("page"),
                "section": h.get("section"),
                "text": h.get("text", ""),
                "url_or_file": url_or_file,
                "source_type": h.get("source_type"),
            }
        )
    return sources


def _truncate_sources_to_budget(sources: list[dict], max_tokens: int) -> list[dict]:
    """SPEC.md section 6 step 5: cap context at ~2k tokens. Found via eval (a 4-source
    context hit 4308 tokens, exceeding the LLM's 4096 ctx-size, and the whole request was
    rejected) that this was never actually enforced — only retrieval's final_top_k capped
    source *count*, not combined *length*. Truncates each source's text proportionally so
    all sources are kept (better for citation coverage than dropping whole sources)."""
    total = sum(count_tokens(s["text"]) for s in sources)
    if total <= max_tokens or not sources:
        return sources
    per_source_budget = max(50, max_tokens // len(sources))
    truncated = []
    for s in sources:
        text = s["text"]
        if count_tokens(text) > per_source_budget:
            words = text.split()
            while words and count_tokens(" ".join(words)) > per_source_budget:
                words = words[: max(1, int(len(words) * 0.9))]
            text = " ".join(words)
        truncated.append({**s, "text": text})
    return truncated


async def answer(
    conversation_id: str,
    session_id: str,
    text: str,
    input_mode: str,
    ui_lang: str,
    doc_ids: list[str],
    provider: str | None = None,
):
    timings: dict[str, float] = {}
    t_start = time.time()

    lang = resolve_lang(ui_lang, text)

    yield "stage", {"name": "searching"}

    history_rows = await get_messages(conversation_id)
    history_rows = history_rows[-(config.MAX_HISTORY_TURNS * 2):]
    history = [{"role": r["role"], "text": r["text"]} for r in history_rows]

    await add_message(conversation_id, "user", text, input_mode, lang)

    async def fixed_reply(status: str, answer_text: str):
        """Small talk / refusals / no-evidence: a stored reply with no LLM answer, no citation."""
        timings["total_ms"] = round((time.time() - t_start) * 1000, 1)
        msg = await add_message(
            conversation_id, "assistant", answer_text, input_mode, lang, [], None, timings
        )
        return "final", {
            "message_id": msg["id"],
            "answer": answer_text,
            "status": status,
            "lang": lang,
            "citations": [],
            "timings": timings,
        }

    # 1. Greetings / "who are you" / thanks / bye: fixed reply, no retrieval, no LLM.
    smalltalk_kind = smalltalk.classify(text)
    if smalltalk_kind:
        yield await fixed_reply("smalltalk", smalltalk.reply(smalltalk_kind, lang))
        return

    # 2. Obvious prompt-injection phrasing: refused without any LLM call.
    if guard.looks_like_injection(text):
        yield await fixed_reply("refused", guard.refusal("injection", lang))
        return

    # 3. LLM router: scope check + standalone, typo-free search query. A router failure
    #    degrades to "plain question with the raw text", never to a refusal.
    t0 = time.time()
    try:
        route = await route_query(history, text, bool(doc_ids), provider)
    except Exception:  # noqa: BLE001
        route = {"type": "question", "query": text}
    timings["route_ms"] = round((time.time() - t0) * 1000, 1)
    if route["type"] == "smalltalk":
        yield await fixed_reply("smalltalk", smalltalk.reply("identity", lang))
        return
    if route["type"] == "injection":
        yield await fixed_reply("refused", guard.refusal("injection", lang))
        return
    if route["type"] == "off_topic" and not doc_ids:
        # With documents attached the user may ask about anything in them; the evidence
        # gate below still applies.
        yield await fixed_reply("out_of_scope", guard.refusal("off_topic", lang))
        return
    query = route["query"]

    # 4. Retrieval + evidence gate.
    t0 = time.time()
    try:
        hits = hybrid_search(query, session_id, doc_ids)
    except Exception as e:  # noqa: BLE001
        yield "error", {"code": "retrieval_failed", "message": str(e)}
        return
    timings["retrieval_ms"] = round((time.time() - t0) * 1000, 1)

    # The reranker's score is an absolute 0-1 relevance; the RRF fusion score is rank-based
    # (anything ranked first scores ~1/61 ≈ 0.016), so the old RRF threshold almost never
    # rejected anything. Gate on the reranker whenever it ran.
    if hits and "rerank_score" in hits[0]:
        enough_evidence = hits[0]["rerank_score"] >= config.MIN_RERANK_SCORE
    else:
        enough_evidence = bool(hits) and hits[0]["score"] >= config.MIN_SCORE_THRESHOLD
    if not enough_evidence:
        yield await fixed_reply(
            "insufficient_evidence",
            "عذرًا، لا تحتوي المصادر المتاحة على معلومات كافية للإجابة على هذا السؤال."
            if lang == "ar"
            else "Sorry, the available sources don't have enough information to answer that.",
        )
        return

    sources = _truncate_sources_to_budget(hits_to_sources(hits), config.MAX_CONTEXT_TOKENS)

    yield "stage", {"name": "generating"}

    t0 = time.time()
    chunks: list[str] = []
    try:
        async for token in stream_answer(lang, query, sources, provider):
            chunks.append(token)
            yield "token", {"text": token}
    except Exception as e:  # noqa: BLE001
        yield "error", {"code": "llm_failed", "message": str(e)}
        return
    timings["llm_ms"] = round((time.time() - t0) * 1000, 1)

    raw_answer = "".join(chunks)
    if guard.wrong_language(raw_answer, lang):
        # The final event replaces the streamed text in the UI, so the retry is what's shown.
        t0 = time.time()
        try:
            retry = await regenerate_answer(lang, query, sources, provider)
            if not guard.wrong_language(retry, lang):
                raw_answer = retry
        except Exception:  # noqa: BLE001 — keep the streamed answer
            pass
        timings["regenerate_ms"] = round((time.time() - t0) * 1000, 1)
    if guard.leaks_system_prompt(raw_answer):
        yield await fixed_reply("refused", guard.refusal("injection", lang))
        return
    validated = validate_citations(raw_answer, sources)

    status = "answered"
    if raw_answer.strip().endswith(CLARIFY_SUFFIXES) and not validated["has_valid_citations"]:
        status = "clarify"
    elif not validated["has_valid_citations"]:
        status = "insufficient_evidence"

    timings["total_ms"] = round((time.time() - t_start) * 1000, 1)

    msg = await add_message(
        conversation_id,
        "assistant",
        validated["text"],
        input_mode,
        lang,
        validated["citations"],
        None,
        timings,
    )

    yield "final", {
        "message_id": msg["id"],
        "answer": validated["text"],
        "status": status,
        "lang": lang,
        "citations": validated["citations"],
        "timings": timings,
        "warning": "numeric_mismatch" if validated["numeric_warning"] else None,
    }
