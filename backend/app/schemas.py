from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    provider: str
    components: dict[str, Any]


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationOut(BaseModel):
    id: str
    title: str | None
    created_at: str
    last_message_at: str | None = None
    message_count: int = 0


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str
    text: str
    input_mode: str
    lang: str
    citations: list[dict[str, Any]]
    audio_path: str | None
    timings: dict[str, Any]
    created_at: str


# A question plus up to ~2k tokens of sources must fit one LLM slot (4096 tokens).
MAX_QUESTION_CHARS = 2000


class ChatRequest(BaseModel):
    conversation_id: str
    text: str = Field(max_length=MAX_QUESTION_CHARS)
    input_mode: Literal["voice", "text"] = "text"
    lang: Literal["auto", "ar", "en"] = "auto"
    doc_ids: list[str] = []

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        # An empty message used to reach the router and come back as a "refused" reply.
        if not v.strip():
            raise ValueError("message is empty")
        return v


class Citation(BaseModel):
    label: str
    title: str
    url: str | None = None
    filename: str | None = None
    page: int | None = None
    section: str | None = None
    excerpt: str


class TranscribeResponse(BaseModel):
    text: str
    lang: str | None
    duration_s: float
    rejected: bool
    reason: str | None = None
    ms: float


class SpeechResponse(BaseModel):
    audio_url: str


class DocumentOut(BaseModel):
    id: str
    filename: str
    type: str
    status: str
    pages: int | None
    chunks: int | None
    error: str | None
    created_at: str


class InsightsOut(BaseModel):
    intent: str
    products: list[str]
    language: str
    dialect: str | None
    sentiment: str
    resolved: bool
    needs_escalation: bool
    summary: str
