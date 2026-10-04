"""Format loaders -> a flat list of content blocks for chunking.py.

Each block: {"type": "heading"|"paragraph"|"table_row", "text": str, "level": int|None,
             "page": int|None, "section": str|None}

HTML is implemented here for Phase 1 (website ingestion). PDF/DOCX/TXT/images are
added in Phase 2 (SPEC.md section 7, uploads).
"""
import io
import re

import pymupdf
from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from PIL import Image

from .arabic import is_garbled, normalize
from .ocr import ocr_image

STRIP_TAGS = ["nav", "header", "footer", "script", "style", "noscript", "svg", "form"]
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}


def _span(cell: Tag, attr: str) -> int:
    try:
        return max(1, min(int(cell.get(attr, 1)), 50))
    except (TypeError, ValueError):
        return 1


def _table_grid(table: Tag) -> list[list[tuple[str, bool]]]:
    """Expand colspan/rowspan into a rectangular grid of (text, is_th). Without this, te.eg's
    two-row-header price tables (e.g. FAQ "Fixed Internet Bundle" x WE Gold price points)
    lost every column after the first span and came out as "col3: 775, col4: 1050"."""
    grid: list[list[tuple[str, bool] | None]] = []
    for r, tr in enumerate(table.find_all("tr")):
        while len(grid) <= r:
            grid.append([])
        row = grid[r]
        c = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while c < len(row) and row[c] is not None:
                c += 1  # skip slots already filled by a rowspan from above
            value = (cell.get_text(" ", strip=True), cell.name == "th")
            for dr in range(_span(cell, "rowspan")):
                while len(grid) <= r + dr:
                    grid.append([])
                target = grid[r + dr]
                for dc in range(_span(cell, "colspan")):
                    while len(target) <= c + dc:
                        target.append(None)
                    target[c + dc] = value
            c += _span(cell, "colspan")
    width = max((len(row) for row in grid), default=0)
    return [[cell or ("", False) for cell in row] + [("", False)] * (width - len(row)) for row in grid]


def _table_to_rows(table: Tag) -> list[dict]:
    """SPEC.md section 7: one "header: value" block per table row. Leading rows made mostly
    of <th> cells are all header rows; their labels are combined per column ("WE Gold &
    Fixed Internet Upgrade Fees (in EGP) / 260")."""
    grid = [row for row in _table_grid(table) if any(text for text, _ in row)]
    if not grid:
        return []
    n_header = 0
    for row in grid[:-1]:  # always leave at least one body row
        if sum(is_th for _, is_th in row) * 2 < len(row):
            break
        n_header += 1
    n_header = n_header or 1
    # Per column: (group label from the upper header rows, leaf label from the lowest one).
    headers = []
    for col in range(len(grid[0])):
        parts: list[str] = []
        for row in grid[:n_header]:
            text = row[col][0]
            if text and text not in parts:
                parts.append(text)
        headers.append((" / ".join(parts[:-1]), parts[-1] if parts else ""))

    blocks = []
    for row in grid[n_header:] if len(grid) > n_header else grid:
        pieces: list[str] = []
        group_items: list[str] = []
        group = None
        seen = set()

        def flush():
            if group_items:
                pieces.append(f"{group}: " + "; ".join(group_items))
                group_items.clear()

        for col, (val, _) in enumerate(row):
            grp, leaf = headers[col]
            if not val or val in ("-", "–") or val in (leaf, grp) or (grp, leaf, val) in seen:
                continue  # empty / not-applicable / a header cell rowspanned down / colspan copy
            seen.add((grp, leaf, val))
            if grp:  # "WE Gold & ... Fees (in EGP): 260 = Free; 525 = Free"
                if grp != group:
                    flush()
                    group = grp
                group_items.append(f"{leaf} = {val}")
            else:
                flush()
                group = None
                pieces.append(f"{leaf}: {val}" if leaf else val)
        flush()
        if pieces:
            blocks.append({"type": "table_row", "text": ", ".join(pieces)})
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


def load_pdf(data: bytes) -> tuple[str | None, list[dict]]:
    """SPEC.md section 7: per-page text extraction, NFKC-normalize, OCR garbled pages
    (rendered at 300 DPI). Returns (title, blocks) with each block tagged page/ocr."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    title = (doc.metadata or {}).get("title") or None
    blocks: list[dict] = []

    for page_index in range(len(doc)):
        page = doc[page_index]
        page_num = page_index + 1
        raw_text = page.get_text()
        ocr = False

        if is_garbled(raw_text):
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            raw_text = ocr_image(img, lang="ara+eng")
            ocr = True

        text = normalize(raw_text)
        for para in re.split(r"\n\s*\n+", text):
            para = para.strip()
            if para:
                blocks.append({"type": "paragraph", "text": para, "page": page_num, "ocr": ocr})

    doc.close()
    return title, blocks


def load_docx(data: bytes) -> tuple[str | None, list[dict]]:
    """SPEC.md section 7: walk document.element.body in order (paragraphs + tables);
    headings set the section."""
    doc = Document(io.BytesIO(data))
    blocks: list[dict] = []
    title = None

    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            para = DocxParagraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            style_name = (para.style.name or "").lower() if para.style else ""
            if "heading" in style_name or "title" in style_name:
                if title is None:
                    title = text
                level_match = re.search(r"\d+", style_name)
                level = int(level_match.group()) if level_match else 1
                blocks.append({"type": "heading", "level": level, "text": text})
            else:
                blocks.append({"type": "paragraph", "text": text})
        elif child.tag.endswith("}tbl"):
            table = DocxTable(child, doc)
            rows_raw = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            rows_raw = [r for r in rows_raw if any(r)]
            if not rows_raw:
                continue
            header = rows_raw[0]
            body_rows = rows_raw[1:] if len(rows_raw) > 1 else rows_raw
            for row in body_rows:
                pairs = []
                for i, val in enumerate(row):
                    key = header[i] if i < len(header) and header[i] else f"col{i + 1}"
                    if val:
                        pairs.append(f"{key}: {val}")
                if pairs:
                    blocks.append({"type": "table_row", "text": ", ".join(pairs)})

    return title, blocks


MAGIC_SIGNATURES = {
    "pdf": (b"%PDF",),
    "docx": (b"PK\x03\x04",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
}


def detect_doc_type(filename: str, data: bytes) -> str | None:
    """SPEC.md section 7: check both extension and magic bytes. Returns the doc type
    (pdf/docx/txt/html/png/jpg) or None if they disagree or the type isn't supported."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext = "jpg" if ext == "jpeg" else ext

    if ext in MAGIC_SIGNATURES:
        if any(data.startswith(sig) for sig in MAGIC_SIGNATURES[ext]):
            return ext
        return None

    if ext in ("txt", "html", "htm"):
        try:
            data[: 1024 * 1024].decode("utf-8")
        except UnicodeDecodeError:
            try:
                data[: 1024 * 1024].decode("cp1256")
            except UnicodeDecodeError:
                return None
        return "txt" if ext == "txt" else "html"

    return None


def load_image(data: bytes) -> tuple[str | None, list[dict]]:
    """SPEC.md section 7: Tesseract ara+eng, ocr=true on resulting chunks."""
    img = Image.open(io.BytesIO(data))
    text = normalize(ocr_image(img, lang="ara+eng"))
    blocks = []
    for para in re.split(r"\n\s*\n+", text):
        para = para.strip()
        if para:
            blocks.append({"type": "paragraph", "text": para, "ocr": True})
    return None, blocks


def load_document(doc_type: str, data: bytes) -> tuple[str | None, list[dict]]:
    """Dispatch to the right loader for an upload (SPEC.md section 7)."""
    if doc_type == "pdf":
        return load_pdf(data)
    if doc_type == "docx":
        return load_docx(data)
    if doc_type == "txt":
        return None, [{"type": "paragraph", "text": p.strip()} for p in re.split(r"\n\s*\n+", load_txt(data)) if p.strip()]
    if doc_type == "html":
        return load_html(load_txt(data))
    if doc_type in ("png", "jpg"):
        return load_image(data)
    raise ValueError(f"unsupported doc_type: {doc_type}")
