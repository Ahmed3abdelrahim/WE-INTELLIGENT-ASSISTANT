import json
import uuid

from fastapi import APIRouter, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from .clients.asr import ASRClient
from .clients.llm import LLMClient
from .clients.tts import TTSClient
from .config import config
from .generation.insights import compute_insights
from .pipeline import answer as pipeline_answer
from .schemas import ChatRequest, ConversationCreate, ConversationOut, HealthResponse
from .store import create_conversation, get_conversation, get_messages, list_conversations, set_insights

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
async def post_documents():
    raise HTTPException(status_code=501, detail="implemented in Phase 2")


@router.get("/documents")
async def get_documents():
    raise HTTPException(status_code=501, detail="implemented in Phase 2")


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
