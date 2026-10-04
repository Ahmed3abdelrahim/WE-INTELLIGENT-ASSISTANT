"""Chunker: atomic units stay together when they fit, oversized ones are split on safe
boundaries (a 200-row table used to become one chunk far over the ~450-token budget)."""
from app.ingestion.chunking import chunk_blocks, count_tokens

TARGET, OVERLAP = 120, 20


def _row(i):
    return {"type": "table_row", "text": f"Package: WE Plan {i}, Data: {i * 5} GB, Price: {i * 10 + 9} EGP"}


def test_long_table_is_split_on_row_boundaries_within_budget():
    rows = [_row(i) for i in range(1, 201)]
    blocks = [{"type": "heading", "text": "Price list"}, *rows]
    chunks = chunk_blocks(blocks, target_tokens=TARGET, overlap_tokens=OVERLAP)
    assert len(chunks) > 5
    for c in chunks:
        assert count_tokens(c["text"]) <= TARGET + OVERLAP + 5  # overlap tail is carried in
    for r in rows:  # every row survives intact somewhere — never cut mid-row
        assert any(r["text"] in c["text"].split("\n") for c in chunks)
    assert all(c["section"] == "Price list" for c in chunks)


def test_long_paragraph_is_split_on_sentences():
    sentence = "The WE Air 150 package includes 20 GB and is valid for 30 days."
    text = " ".join(f"{sentence} Item {i}." for i in range(80))
    chunks = chunk_blocks([{"type": "paragraph", "text": text}], target_tokens=TARGET, overlap_tokens=0)
    assert len(chunks) > 3
    assert all(count_tokens(c["text"]) <= TARGET for c in chunks)
    assert all(c["text"].rstrip().endswith(".") for c in chunks)  # ends on a sentence boundary


def test_small_faq_pair_stays_together():
    blocks = [
        {"type": "heading", "text": "What is the contract length?"},
        {"type": "paragraph", "text": "The standard contract length is 12 months."},
    ]
    (chunk,) = chunk_blocks(blocks, target_tokens=TARGET, overlap_tokens=OVERLAP)
    assert "What is the contract length?\nThe standard contract length is 12 months." in chunk["text"]
