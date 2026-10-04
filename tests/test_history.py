"""History store: auto titles, activity ordering, and rename/delete scoped to the owning
session (the GUI's organized history). Uses a throwaway SQLite file, not data/app.db."""
import asyncio

import pytest

from app import store
from app.config import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    asyncio.run(store.init_db())


def run(coro):
    return asyncio.run(coro)


def test_title_falls_back_to_first_question_and_newest_activity_first(db):
    old = run(store.create_conversation("A", None))
    new = run(store.create_conversation("A", None))
    run(store.add_message(new["id"], "user", "What are the WE Air packages?"))
    run(store.add_message(old["id"], "user", "ما هو رقم خدمة العملاء؟"))  # old chat used last
    convs = run(store.list_conversations("A"))
    assert [c["id"] for c in convs] == [old["id"], new["id"]]
    assert convs[0]["title"] == "ما هو رقم خدمة العملاء؟"
    assert convs[0]["message_count"] == 1 and convs[0]["last_message_at"]


def test_empty_chat_has_no_title_and_zero_messages(db):
    c = run(store.create_conversation("A", None))
    (conv,) = run(store.list_conversations("A"))
    assert conv["id"] == c["id"] and conv["title"] is None and conv["message_count"] == 0


def test_rename_and_delete_only_within_the_owning_session(db):
    c = run(store.create_conversation("A", None))
    run(store.add_message(c["id"], "user", "hello"))
    assert run(store.rename_conversation(c["id"], "B", "hijacked")) is False
    assert run(store.delete_conversation(c["id"], "B")) is None
    assert run(store.list_conversations("A"))[0]["title"] == "hello"

    assert run(store.rename_conversation(c["id"], "A", "Support numbers")) is True
    assert run(store.list_conversations("A"))[0]["title"] == "Support numbers"
    assert run(store.delete_conversation(c["id"], "A")) == []
    assert run(store.list_conversations("A")) == []
    assert run(store.get_messages(c["id"])) == []
