"""Unit-level check for the retrieval filter's session isolation (SPEC.md section 6/10).
Full end-to-end isolation (upload as session A, confirm session B can't retrieve it even
when passing A's doc_ids) was verified live against the running stack — see docs/progress.md
Phase 2. This test pins down the filter logic itself so a regression fails fast in CI.
"""
from app.retrieval.search import build_filter


def test_filter_allows_official_sources_regardless_of_session():
    f = build_filter("session-A", doc_ids=None)
    conditions = f.should
    assert any(
        getattr(c, "key", None) == "source_type" and c.match.value == "official" for c in conditions
    )


def test_filter_scopes_uploads_to_the_requesting_session_only():
    f = build_filter("session-A", doc_ids=["doc-1", "doc-2"])
    nested = [c for c in f.should if hasattr(c, "must")]
    assert len(nested) == 1
    must_conditions = nested[0].must
    session_cond = next(c for c in must_conditions if c.key == "session_id")
    doc_cond = next(c for c in must_conditions if c.key == "doc_id")
    assert session_cond.match.value == "session-A"
    assert set(doc_cond.match.any) == {"doc-1", "doc-2"}


def test_filter_with_no_doc_ids_has_no_session_clause():
    # If the caller doesn't select any uploaded docs, the filter must not leak an
    # unscoped session_id-only clause — only official sources should be reachable.
    f = build_filter("session-A", doc_ids=None)
    nested = [c for c in f.should if hasattr(c, "must")]
    assert nested == []
