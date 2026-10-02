#!/usr/bin/env python3
"""te.eg crawler (SPEC.md section 7).

probe(): reachability/robots/JS-rendering check (Phase 0).
crawl(): BFS from seed sitemap pages, discovering real nav links only (never invents
paths), fetches + cleans pages, rate-limited to 1 req/s, saves raw HTML + metadata.jsonl
under data/website/ and the fetched URL list to config/te_urls.txt.
"""
import hashlib
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import SETTINGS, config
from .loaders import load_html

BASE = "https://te.eg"
UA = "WE-Assistant-PoC/0.1 (+case-study; contact: internal)"

SEED_PATHS = [
    "/",
    "/personal/sitemap/",
    "/en/personal",
    "/en/about-te",
    # Mirrors of real Arabic-side sections discovered by the first crawl pass, tried
    # under /en/ too (English-side BFS discovery was thin on its own — these paths were
    # confirmed to exist before being added, not invented).
    "/en/personal/mobile",
    "/en/personal/home",
    "/en/personal/home-5g",
    "/en/personal/5g",
    "/en/personal/devices",
    "/en/personal/services",
    "/en/personal/promotions",
    "/en/about-te/faq",
    "/en/about-te/contractual-terms",
    "/en/about-te/history",
]

# SPEC.md section 7: cover mobile and home-internet packages, renewal/recharge, FAQs,
# terms, and support, in both languages. te.eg serves Arabic by default on bare paths
# (confirmed by probing); /en/ is the real English prefix. /ar/... paths serve the same
# Arabic content as the bare paths (confirmed too), so they're excluded to avoid
# crawling near-duplicate pages under two different URLs.
ALLOWED_PREFIXES = [
    "/personal",
    "/en/personal",
    "/about-te",
    "/en/about-te",
]
EXCLUDE_PATH_PREFIXES = ["/ar/"]
EXCLUDE_SUBSTRINGS = ["/store-locator", "/devices/", "/accessories"]


def probe() -> dict:
    report = {"base": BASE}
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{BASE}/robots.txt")
    try:
        resp = httpx.get(f"{BASE}/robots.txt", headers={"User-Agent": UA}, timeout=15.0, follow_redirects=True)
        report["robots_status"] = resp.status_code
        report["robots_txt_excerpt"] = resp.text[:500] if resp.status_code == 200 else None
        rp.parse(resp.text.splitlines())
        report["can_fetch_root"] = rp.can_fetch(UA, BASE + "/")
    except Exception as e:  # noqa: BLE001
        report["robots_error"] = str(e)

    try:
        t0 = time.time()
        resp = httpx.get(BASE, headers={"User-Agent": UA}, timeout=20.0, follow_redirects=True)
        report["home_status"] = resp.status_code
        report["home_fetch_ms"] = round((time.time() - t0) * 1000, 1)
        report["final_url"] = str(resp.url)
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(separator=" ", strip=True)
        report["raw_html_bytes"] = len(resp.content)
        report["visible_text_chars"] = len(text)
        report["text_to_html_ratio"] = round(len(text) / max(len(resp.content), 1), 4)
        nav_links = soup.select("a[href]")
        report["anchor_count"] = len(nav_links)
        sample_links = sorted({a.get("href", "") for a in nav_links if a.get("href", "").startswith("/")})
        report["sample_internal_links"] = sample_links[:20]
        report["likely_js_rendered"] = report["text_to_html_ratio"] < 0.01 or len(sample_links) < 3
    except Exception as e:  # noqa: BLE001
        report["home_error"] = str(e)

    return report


def _allowed(path: str) -> bool:
    if any(path.startswith(p) for p in EXCLUDE_PATH_PREFIXES):
        return False
    if any(sub in path for sub in EXCLUDE_SUBSTRINGS):
        return False
    return any(path == p.rstrip("/") or path.startswith(p) for p in ALLOWED_PREFIXES)


def _normalize_link(href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    parsed = urlparse(urljoin(BASE, href))
    if parsed.netloc and parsed.netloc != urlparse(BASE).netloc:
        return None
    path = parsed.path or "/"
    return path


def crawl(max_pages: int | None = None, rate_limit_per_sec: float | None = None) -> dict:
    max_pages = max_pages or SETTINGS["crawl"]["max_pages"]
    rate_limit_per_sec = rate_limit_per_sec or SETTINGS["crawl"]["rate_limit_per_sec"]
    delay = 1.0 / rate_limit_per_sec

    website_dir = config.WEBSITE_DIR
    website_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = website_dir / "metadata.jsonl"
    te_urls_path = Path(__file__).resolve().parents[3] / "config" / "te_urls.txt"

    visited: set[str] = set()
    queue: list[str] = list(SEED_PATHS)
    fetched_meta = []

    # te.eg sets a language-preference cookie on first request: a single cookie-jarred
    # client would "stick" to English after the first /en/ page and start returning
    # English content for bare/Arabic paths too. Two independent clients (separate
    # cookie jars) keep the language tracks from cross-contaminating.
    with httpx.Client(headers={"User-Agent": UA}, timeout=20.0, follow_redirects=True) as client_default, \
         httpx.Client(headers={"User-Agent": UA}, timeout=20.0, follow_redirects=True) as client_en:
        while queue and len(visited) < max_pages:
            path = queue.pop(0)
            if path in visited:
                continue
            visited.add(path)
            url = urljoin(BASE, path)
            client = client_en if path.startswith("/en/") else client_default
            try:
                resp = client.get(url)
                time.sleep(delay)
            except Exception as e:  # noqa: BLE001
                print(f"  [skip] {path}: {e}", flush=True)
                continue
            if resp.status_code != 200:
                print(f"  [skip] {path}: HTTP {resp.status_code}", flush=True)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.select("a[href]"):
                link_path = _normalize_link(a.get("href", ""))
                if link_path and _allowed(link_path) and link_path not in visited and link_path not in queue:
                    if len(visited) + len(queue) < max_pages * 3:
                        queue.append(link_path)

            if not _allowed(path) and path not in ("/",) and path not in [s for s in SEED_PATHS]:
                continue

            title, blocks = load_html(resp.text)
            if not blocks:
                print(f"  [skip-empty] {path}", flush=True)
                continue

            lang = "en" if path.startswith("/en/") else "ar"
            sha256 = hashlib.sha256(resp.content).hexdigest()
            fname = sha256[:16] + ".html"
            (website_dir / fname).write_text(resp.text, encoding="utf-8")

            fetched_meta.append(
                {
                    "url": url,
                    "path": path,
                    "title": title,
                    "lang": lang,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": sha256,
                    "html_file": fname,
                }
            )
            print(f"  [ok] {path} ({lang}, {len(blocks)} blocks)", flush=True)

    with open(metadata_path, "w", encoding="utf-8") as f:
        for m in fetched_meta:
            f.write(__import__("json").dumps(m, ensure_ascii=False) + "\n")

    with open(te_urls_path, "w", encoding="utf-8") as f:
        f.write("# Auto-generated by backend/app/ingestion/crawl_te.py — real site navigation, not invented.\n")
        for m in fetched_meta:
            f.write(m["url"] + "\n")

    return {"pages_fetched": len(fetched_meta), "metadata_path": str(metadata_path), "te_urls_path": str(te_urls_path)}


def main():
    import json

    if "--probe" in sys.argv:
        report = probe()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    result = crawl()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
