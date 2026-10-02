#!/usr/bin/env python3
"""Native-run shim: scripts/crawl_te.py -> backend.app.ingestion.crawl_te
(the real crawler lives in backend/app/ingestion per SPEC.md's repo structure)."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

from app.ingestion.crawl_te import main  # noqa: E402

if __name__ == "__main__":
    main()
