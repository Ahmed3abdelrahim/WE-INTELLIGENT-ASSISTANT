import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from .config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    title TEXT,
    insights_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    input_mode TEXT NOT NULL,
    lang TEXT NOT NULL,
    citations_json TEXT,
    audio_path TEXT,
    timings_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    pages INTEGER,
    chunks INTEGER,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


async def init_db():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.executescript(SCHEMA)
        await db.commit()


async def create_conversation(session_id: str, title: str | None) -> dict:
    cid = new_id()
    created_at = now_iso()
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (id, session_id, title, insights_json, created_at) VALUES (?,?,?,?,?)",
            (cid, session_id, title, None, created_at),
        )
        await db.commit()
    return {"id": cid, "title": title, "created_at": created_at}


async def list_conversations(session_id: str) -> list[dict]:
    """Newest activity first. `title` falls back to the first user message so the history
    list is readable without anyone naming chats; `last_message_at` / `message_count` let the
    UI group by day and hide empty chats."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT c.id, c.created_at,
                      COALESCE(c.title, (SELECT m.text FROM messages m
                                         WHERE m.conversation_id = c.id AND m.role = 'user'
                                         ORDER BY m.created_at LIMIT 1)) AS title,
                      (SELECT MAX(m.created_at) FROM messages m WHERE m.conversation_id = c.id) AS last_message_at,
                      (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
               FROM conversations c WHERE c.session_id = ?
               ORDER BY COALESCE(last_message_at, c.created_at) DESC""",
            (session_id,),
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d["title"] and len(d["title"]) > 80:
                d["title"] = d["title"][:80].rstrip() + "…"
            out.append(d)
        return out


async def rename_conversation(conversation_id: str, session_id: str, title: str) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE conversations SET title=? WHERE id=? AND session_id=?", (title, conversation_id, session_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_conversation(conversation_id: str, session_id: str) -> list[str] | None:
    """Deletes the conversation and its messages. Returns the audio paths to remove, or None
    if the conversation doesn't belong to this session."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM conversations WHERE id=? AND session_id=?", (conversation_id, session_id)
        )
        if not await cur.fetchone():
            return None
        cur = await db.execute(
            "SELECT audio_path FROM messages WHERE conversation_id=? AND audio_path IS NOT NULL", (conversation_id,)
        )
        audio = [r[0] for r in await cur.fetchall()]
        await db.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
        await db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        await db.commit()
        return audio


async def get_conversation(conversation_id: str, session_id: str) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM conversations WHERE id=? AND session_id=?", (conversation_id, session_id)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def set_insights(conversation_id: str, insights: dict):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET insights_json=? WHERE id=?",
            (json.dumps(insights, ensure_ascii=False), conversation_id),
        )
        await db.commit()


async def add_message(
    conversation_id: str,
    role: str,
    text: str,
    input_mode: str = "text",
    lang: str = "en",
    citations: list | None = None,
    audio_path: str | None = None,
    timings: dict | None = None,
) -> dict:
    mid = new_id()
    created_at = now_iso()
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO messages
               (id, conversation_id, role, text, input_mode, lang, citations_json, audio_path, timings_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                mid,
                conversation_id,
                role,
                text,
                input_mode,
                lang,
                json.dumps(citations or [], ensure_ascii=False),
                audio_path,
                json.dumps(timings or {}, ensure_ascii=False),
                created_at,
            ),
        )
        await db.commit()
    return {"id": mid, "created_at": created_at}


async def get_messages(conversation_id: str) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC", (conversation_id,)
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["citations"] = json.loads(d.pop("citations_json") or "[]")
            d["timings"] = json.loads(d.pop("timings_json") or "{}")
            out.append(d)
        return out


async def update_message_audio(message_id: str, audio_path: str):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("UPDATE messages SET audio_path=? WHERE id=?", (audio_path, message_id))
        await db.commit()


async def get_message(message_id: str) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM messages WHERE id=?", (message_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["citations"] = json.loads(d.pop("citations_json") or "[]")
        d["timings"] = json.loads(d.pop("timings_json") or "{}")
        return d


async def create_document(session_id: str, filename: str, doc_type: str) -> str:
    did = new_id()
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO documents (id, session_id, filename, type, status, pages, chunks, error, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (did, session_id, filename, doc_type, "processing", None, None, None, now_iso()),
        )
        await db.commit()
    return did


async def update_document(did: str, status: str, pages: int | None = None, chunks: int | None = None, error: str | None = None):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE documents SET status=?, pages=?, chunks=?, error=? WHERE id=?",
            (status, pages, chunks, error, did),
        )
        await db.commit()


async def list_documents(session_id: str) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM documents WHERE session_id=? ORDER BY created_at DESC", (session_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
