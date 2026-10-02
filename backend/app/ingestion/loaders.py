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
