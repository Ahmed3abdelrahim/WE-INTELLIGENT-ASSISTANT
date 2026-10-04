import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from .clients.asr import ASRClient
from .clients.llm import LLMClient
from .clients.tts import TTSClient
from .config import CONFIG_DIR, config
from .generation import guard
from .generation.insights import compute_insights
from .ingestion.chunking import chunk_blocks
from .ingestion.loaders import detect_doc_type, load_document
from .pipeline import answer as pipeline_answer
from .retrieval.embed import encode
from .retrieval.index import upsert_chunks
from .schemas import ChatRequest, ConversationCreate, ConversationOut, ConversationRename, HealthResponse
from .speech_text import clean_for_tts, voice_for_lang
from .store import (
    create_conversation,
    create_document,
    delete_conversation,
    get_conversation,
    get_message,
    get_messages,
    list_conversations,
    list_documents,
    rename_conversation,
    set_insights,
    update_document,
    update_message_audio,
)

router = APIRouter()
logger = logging.getLogger("we-assistant")


def get_session_id(x_session_id: str | None = Header(default=None)) -> str:
    return x_session_id or str(uuid.uuid4())


@router.get("/health", response_model=HealthResponse)
async def health():
    import httpx

    llm = LLMClient()
    llm_health = await llm.health()
    asr_health = await ASRClient().health()
    tts_health = await TTSClient().health()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{config.QDRANT_URL}/healthz")
            qdrant_status = {"status": "ok" if resp.status_code == 200 else "error"}
    except Exception as e:  # noqa: BLE001
        qdrant_status = {"status": "error", "detail": str(e)}

    components = {"llm": llm_health, "asr": asr_health, "tts": tts_health, "qdrant": qdrant_status}
    overall = "ok" if all(c.get("status") == "ok" for c in components.values()) else "degraded"
    return HealthResponse(status=overall, provider=config.LLM_PROVIDER, components=components)


@router.post("/conversations", response_model=ConversationOut)
async def post_conversation(body: ConversationCreate, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or str(uuid.uuid4())
    conv = await create_conversation(sid, body.title)
    return conv


@router.get("/conversations", response_model=list[ConversationOut])
async def get_conversations(session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or str(uuid.uuid4())
    return await list_conversations(sid)


@router.patch("/conversations/{conversation_id}")
async def patch_conversation(
    conversation_id: str, body: ConversationRename, session_id: str = Header(default=None, alias="X-Session-Id")
):
    if not session_id or not await rename_conversation(conversation_id, session_id, body.title.strip()):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"id": conversation_id, "title": body.title.strip()}


@router.delete("/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    audio = await delete_conversation(conversation_id, session_id) if session_id else None
    if audio is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    for path in audio:
        try:
            p = Path(path)
            if p.resolve().is_relative_to(config.AUDIO_DIR.resolve()):
                p.unlink(missing_ok=True)
        except OSError:
            pass
    return {"deleted": conversation_id}


@router.get("/conversations/{conversation_id}/messages")
async def get_conv_messages(conversation_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or str(uuid.uuid4())
    conv = await get_conversation(conversation_id, sid)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")

    return await get_messages(conversation_id)


@router.post("/chat")
async def post_chat(body: ChatRequest, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or str(uuid.uuid4())
    conv = await get_conversation(body.conversation_id, sid)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")

    async def event_stream():
        try:
            async for event, data in pipeline_answer(
                body.conversation_id, sid, body.text, body.input_mode, body.lang, body.doc_ids
            ):
                yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": json.dumps({"code": "internal_error", "message": str(e)})}

    return EventSourceResponse(event_stream())


_blocklist_cache: list[str] | None = None
_hotwords_cache: str | None = None


def _load_blocklist() -> list[str]:
    global _blocklist_cache
    if _blocklist_cache is None:
        path = CONFIG_DIR / "asr_blocklist.txt"
        lines = path.read_text(encoding="utf-8").splitlines()
        _blocklist_cache = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    return _blocklist_cache


def _load_hotwords() -> str:
    global _hotwords_cache
    if _hotwords_cache is None:
        path = CONFIG_DIR / "asr_hotwords.txt"
        lines = path.read_text(encoding="utf-8").splitlines()
        words = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
        _hotwords_cache = ", ".join(words)
    return _hotwords_cache


@router.post("/transcribe")
async def post_transcribe(file: UploadFile = File(...), lang: str = Form(default=None)):
    data = await file.read()
    asr = ASRClient()
    result = await asr.transcribe(
        data, filename=file.filename or "audio.wav", language=lang, hotwords=_load_hotwords(), beam_size=1
    )

    text = result.get("text", "").strip()
    no_speech_prob = result.get("no_speech_prob", 1.0)
    rejected, reason = False, None

    if not text or no_speech_prob > 0.6:
        rejected, reason = True, "no_speech_detected"
    else:
        blocklist = _load_blocklist()
        if any(phrase in text for phrase in blocklist):
            rejected, reason = True, "hallucination_detected"

    return {
        "text": "" if rejected else text,
        "lang": result.get("language"),
        "duration_s": result.get("duration_s"),
        "rejected": rejected,
        "reason": reason,
        "ms": result.get("ms"),
    }


@router.post("/messages/{message_id}/speech")
async def post_message_speech(message_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or str(uuid.uuid4())
    message = await get_message(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message not found")
    conv = await get_conversation(message["conversation_id"], sid)
    if not conv:
        raise HTTPException(status_code=403, detail="not authorized for this message")

    cleaned = clean_for_tts(message["text"], message["lang"])
    tts = TTSClient()
    wav_bytes = await tts.synthesize(cleaned, voice_for_lang(message["lang"]))

    audio_path = config.AUDIO_DIR / f"{message_id}.wav"
    audio_path.write_bytes(wav_bytes)
    await update_message_audio(message_id, str(audio_path))

    return {"audio_url": f"/api/v1/audio/{message_id}"}


@router.get("/audio/{audio_id}")
async def get_audio(audio_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or str(uuid.uuid4())
    message = await get_message(audio_id)
    if not message or not message.get("audio_path"):
        raise HTTPException(status_code=404, detail="audio not found")
    conv = await get_conversation(message["conversation_id"], sid)
    if not conv:
        raise HTTPException(status_code=403, detail="not authorized for this audio")

    path = Path(message["audio_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="audio file missing on disk")
    return FileResponse(path, media_type="audio/wav")


@router.post("/documents")
async def post_documents(
    file: UploadFile = File(...), session_id: str = Header(default=None, alias="X-Session-Id")
):
    sid = session_id or str(uuid.uuid4())
    data = await file.read()

    if len(data) > config.MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"file exceeds {config.MAX_FILE_MB} MB limit")

    doc_type = detect_doc_type(file.filename or "upload", data)
    if doc_type is None:
        raise HTTPException(status_code=400, detail="unsupported or mismatched file type")

    did = await create_document(sid, file.filename or "upload", doc_type)

    try:
        if doc_type == "pdf":
            import pymupdf

            page_count = len(pymupdf.open(stream=data, filetype="pdf"))
            if page_count > config.MAX_PDF_PAGES:
                await update_document(
                    did, "error", pages=page_count, error=f"PDF exceeds {config.MAX_PDF_PAGES} page limit"
                )
                raise HTTPException(status_code=400, detail=f"PDF exceeds {config.MAX_PDF_PAGES} page limit")

        title, blocks = load_document(doc_type, data)
        title = title or file.filename or "upload"
        # Uploaded text is untrusted: remove sentences aimed at the assistant's instructions.
        removed_injections = 0
        for b in blocks:
            b["text"], n = guard.strip_injections(b["text"])
            removed_injections += n
        blocks = [b for b in blocks if b["text"].strip()]
        chunks = chunk_blocks(blocks)

        if not chunks:
            await update_document(did, "error", error="no extractable text")
            raise HTTPException(status_code=422, detail="no extractable text found in document")

        texts = [c["text"] for c in chunks]
        embeddings = encode(texts)

        payloads = []
        for i, c in enumerate(chunks):
            payloads.append(
                {
                    "chunk_id": f"{did}_{i}",
                    "source_type": "upload",
                    "url": None,
                    "title": title,
                    "lang": "auto",
                    "doc_id": did,
                    "filename": file.filename,
                    "page": c.get("page"),
                    "section": c.get("section"),
                    "session_id": sid,
                    "ocr": c.get("ocr", False),
                    "text": c["text"],
                }
            )
        upsert_chunks(payloads, embeddings["dense"], embeddings["sparse"])
        # SPEC.md section 5: keep the original under data/uploads/, UUID-named (never the
        # user's filename, so no path tricks). Written only after a successful ingest.
        (config.UPLOADS_DIR / f"{did}.{doc_type}").write_bytes(data)

        pages = max((c.get("page") or 0 for c in chunks), default=0) or None
        await update_document(did, "ready", pages=pages, chunks=len(payloads))
        if removed_injections:
            logger.warning("document %s: removed %d instruction-like sentence(s)", did, removed_injections)
        return {
            "id": did, "filename": file.filename, "type": doc_type, "status": "ready", "pages": pages,
            "chunks": len(payloads), "removed_instructions": removed_injections,
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        await update_document(did, "error", error=str(e))
        raise HTTPException(status_code=500, detail=f"ingestion failed: {e}") from e


@router.get("/documents")
async def get_documents(session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or str(uuid.uuid4())
    return await list_documents(sid)


@router.post("/conversations/{conversation_id}/insights")
async def post_insights(conversation_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or str(uuid.uuid4())
    conv = await get_conversation(conversation_id, sid)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    messages = await get_messages(conversation_id)
    transcript = "\n".join(f"{m['role']}: {m['text']}" for m in messages)
    insights = await compute_insights(transcript)
    await set_insights(conversation_id, insights)
    return insights
