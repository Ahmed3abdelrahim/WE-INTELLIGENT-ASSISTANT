"""Regression test for a real bug found via Phase 5 eval: a 4-source context could reach
4308 tokens against llama-server's 4096 ctx-size, and the whole request was rejected with a
400 (`exceed_context_size_error`) — SPEC.md section 6 step 5's "~2k token" context cap was
never actually enforced, only the source *count* (final_top_k) was. See docs/progress.md
Phase 5 and docs/decisions.md for the full diagnosis."""
from app.ingestion.chunking import count_tokens
from app.pipeline import _truncate_sources_to_budget


def _source(label, text):
    return {"label": label, "title": "t", "url": None, "filename": None, "page": None, "section": None, "text": text}


def test_sources_under_budget_are_untouched():
    sources = [_source("S1", "short text here")]
    out = _truncate_sources_to_budget(sources, max_tokens=2000)
    assert out == sources


def test_sources_over_budget_are_truncated_to_fit():
    long_text = "word " * 2000  # way over any reasonable token budget
    sources = [_source("S1", long_text), _source("S2", long_text)]
    out = _truncate_sources_to_budget(sources, max_tokens=2000)
    total = sum(count_tokens(s["text"]) for s in out)
    assert total <= 2000
    assert len(out) == 2  # both sources kept (truncated), none dropped entirely
    assert out[0]["text"]  # not emptied out
    assert out[1]["text"]


def test_truncation_preserves_other_fields():
    long_text = "word " * 2000
    sources = [_source("S1", long_text)]
    out = _truncate_sources_to_budget(sources, max_tokens=100)
    assert out[0]["label"] == "S1"
    assert out[0]["title"] == "t"
