import json
import uuid

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse

from .clients.asr import ASRClient
from .clients.llm import LLMClient
from .clients.tts import TTSClient
from .config import config
from .generation.insights import compute_insights
from .ingestion.chunking import chunk_blocks
from .ingestion.loaders import detect_doc_type, load_document
from .pipeline import answer as pipeline_answer
from .retrieval.embed import encode
from .retrieval.index import upsert_chunks
from .schemas import ChatRequest, ConversationCreate, ConversationOut, HealthResponse
from .store import (
    create_conversation,
    create_document,
    get_conversation,
    get_messages,
    list_conversations,
    list_documents,
    set_insights,
    update_document,
)

router = APIRouter()


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


@router.post("/transcribe")
async def post_transcribe():
    raise HTTPException(status_code=501, detail="implemented in Phase 3")


@router.post("/messages/{message_id}/speech")
async def post_message_speech(message_id: str):
    raise HTTPException(status_code=501, detail="implemented in Phase 3")


@router.get("/audio/{audio_id}")
async def get_audio(audio_id: str):
    raise HTTPException(status_code=501, detail="implemented in Phase 3")


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

        pages = max((c.get("page") or 0 for c in chunks), default=0) or None
        await update_document(did, "ready", pages=pages, chunks=len(payloads))
        return {"id": did, "filename": file.filename, "type": doc_type, "status": "ready", "pages": pages, "chunks": len(payloads)}
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
