#!/usr/bin/env python3
"""Crawl (if needed) te.eg, chunk, embed, and upsert into Qdrant (SPEC.md Phase 1)."""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

from app.config import config  # noqa: E402
from app.ingestion.chunking import chunk_blocks  # noqa: E402
from app.ingestion.crawl_te import crawl  # noqa: E402
from app.ingestion.loaders import load_html  # noqa: E402
from app.retrieval.embed import encode  # noqa: E402
from app.retrieval.index import upsert_chunks  # noqa: E402


def main():
    metadata_path = config.WEBSITE_DIR / "metadata.jsonl"
    if not metadata_path.exists():
        print("No crawled data found, crawling te.eg first...")
        result = crawl()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    pages = []
    with open(metadata_path, encoding="utf-8") as f:
        for line in f:
            pages.append(json.loads(line))
    print(f"{len(pages)} pages to ingest")

    total_chunks = 0
    for page in pages:
        html_path = config.WEBSITE_DIR / page["html_file"]
        html = html_path.read_text(encoding="utf-8")
        title, blocks = load_html(html)
        title = title or page["title"] or page["url"]
        chunks = chunk_blocks(blocks)
        if not chunks:
            continue

        texts = [c["text"] for c in chunks]
        embeddings = encode(texts)

        payloads = []
        for i, c in enumerate(chunks):
            chunk_id = f"{page['sha256']}_{i}"
            payloads.append(
                {
                    "chunk_id": chunk_id,
                    "source_type": "official",
                    "url": page["url"],
                    "title": title,
                    "lang": page["lang"],
                    "doc_id": page["sha256"],
                    "filename": None,
                    "page": c.get("page"),
                    "section": c.get("section"),
                    "session_id": None,
                    "ocr": c.get("ocr", False),
                    "text": c["text"],
                }
            )
        upsert_chunks(payloads, embeddings["dense"], embeddings["sparse"])
        total_chunks += len(payloads)
        print(f"  [ok] {page['url']}: {len(payloads)} chunks")

    print(f"\ningested {total_chunks} chunks from {len(pages)} pages into Qdrant collection '{config.QDRANT_COLLECTION}'")


if __name__ == "__main__":
    main()
