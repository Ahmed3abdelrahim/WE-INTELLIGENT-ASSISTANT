"""Format loaders -> a flat list of content blocks for chunking.py.

Each block: {"type": "heading"|"paragraph"|"table_row", "text": str, "level": int|None,
             "page": int|None, "section": str|None}

HTML is implemented here for Phase 1 (website ingestion). PDF/DOCX/TXT/images are
added in Phase 2 (SPEC.md section 7, uploads).
"""
from bs4 import BeautifulSoup, NavigableString, Tag

STRIP_TAGS = ["nav", "header", "footer", "script", "style", "noscript", "svg", "form"]
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}


def _table_to_rows(table: Tag) -> list[dict]:
    rows_raw = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows_raw.append(cells)
    if not rows_raw:
        return []
    header = rows_raw[0]
    blocks = []
    body_rows = rows_raw[1:] if len(rows_raw) > 1 else rows_raw
    for row in body_rows:
        pairs = []
        for i, val in enumerate(row):
            key = header[i] if i < len(header) and header[i] else f"col{i + 1}"
            if val:
                pairs.append(f"{key}: {val}")
        if pairs:
            blocks.append({"type": "table_row", "text": ", ".join(pairs)})
    return blocks


def load_html(html: str) -> tuple[str | None, list[dict]]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.select(",".join(STRIP_TAGS)):
        tag.decompose()

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    root = soup.body or soup
    blocks: list[dict] = []
    seen_tables: set[int] = set()

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name == "table":
                if id(child) not in seen_tables:
                    seen_tables.add(id(child))
                    blocks.extend(_table_to_rows(child))
                continue
            if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = child.get_text(" ", strip=True)
                if text:
                    blocks.append({"type": "heading", "level": int(name[1]), "text": text})
                continue
            if name in ("p", "li", "blockquote"):
                text = child.get_text(" ", strip=True)
                if text:
                    blocks.append({"type": "paragraph", "text": text})
                continue
            # container element (div/section/article/ul/...): recurse
            walk(child)

    walk(root)
    return title, blocks


def load_txt(data: bytes) -> str:
    """SPEC.md section 7: decode as UTF-8, falling back to cp1256."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1256", errors="replace")
