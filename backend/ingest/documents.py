"""PDF / TXT / Markdown ingestion with page-level granularity and table preservation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pymupdf as fitz

from backend.core.chunking import chunk_text, normalize_whitespace
from backend.ingest.ocr import ocr_pixmap_png

logger = logging.getLogger("omnivault.ingest.pdf")

ProgressFn = Optional[Callable[[str, float, str], None]]


@dataclass
class ExtractedChunk:
    text: str
    metadata: Dict


@dataclass
class DocumentExtraction:
    chunks: List[ExtractedChunk] = field(default_factory=list)
    page_count: int = 0
    title: str = ""
    has_tables: bool = False
    ocr_pages: int = 0
    char_count: int = 0


def extract_document(
    path: Path,
    *,
    doc_id: str,
    filename: str,
    chunk_size: int,
    chunk_overlap: int,
    ocr_limit: int = 30,
    progress: ProgressFn = None,
) -> DocumentExtraction:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".xlsx", ".xls"}:
        raise RuntimeError("Spreadsheets stay with the table agent and are not chunked.")
    if suffix == ".docx":
        from backend.ingest.office import docx_to_text

        raw = docx_to_text(path)
        pieces = chunk_text(raw, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        chunks = [
            ExtractedChunk(
                text=piece,
                metadata={
                    "doc_id": doc_id,
                    "source": filename,
                    "modality": "office",
                    "page": 1,
                    "chunk_index": index,
                },
            )
            for index, piece in enumerate(pieces)
        ]
        return DocumentExtraction(chunks=chunks, page_count=1, title=path.stem, char_count=len(raw), has_tables=suffix != ".docx")
    if suffix == ".pdf":
        return _extract_pdf(
            path,
            doc_id=doc_id,
            filename=filename,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            ocr_limit=ocr_limit,
            progress=progress,
        )
    return _extract_plain(
        path,
        doc_id=doc_id,
        filename=filename,
        modality="text" if suffix in {".txt", ".md", ".markdown"} else "text",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def _emit(progress: ProgressFn, stage: str, value: float, message: str) -> None:
    if progress:
        progress(stage, value, message)


def _extract_plain(
    path: Path,
    *,
    doc_id: str,
    filename: str,
    modality: str,
    chunk_size: int,
    chunk_overlap: int,
) -> DocumentExtraction:
    text = path.read_text(encoding="utf-8", errors="ignore")
    pieces = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = [
        ExtractedChunk(
            text=piece,
            metadata={
                "doc_id": doc_id,
                "source": filename,
                "modality": modality,
                "page": 1,
                "chunk_index": index,
            },
        )
        for index, piece in enumerate(pieces)
    ]
    return DocumentExtraction(
        chunks=chunks,
        page_count=1,
        title=path.stem,
        char_count=len(text),
    )


def _page_text_with_tables(page: fitz.Page) -> tuple[str, bool]:
    """Build a page string that keeps table structure as Markdown where possible."""
    tables_md: List[tuple[float, str]] = []
    table_rects = []
    has_tables = False
    try:
        found = page.find_tables()
        for table in found.tables:
            has_tables = True
            table_rects.append(fitz.Rect(table.bbox))
            try:
                markdown = table.to_markdown()
            except Exception:
                data = table.extract()
                markdown = _matrix_to_markdown(data)
            if markdown and markdown.strip():
                tables_md.append((float(table.bbox[1]), markdown.strip()))
    except Exception as exc:
        logger.debug("find_tables failed: %s", exc)

    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_DEHYPHENATE, sort=True)
    prose_parts: List[tuple[float, str]] = []
    for block in blocks.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = fitz.Rect(block["bbox"])
        if any(bbox.intersects(rect) and bbox.get_area() > 0 for rect in table_rects):
            # Prefer the structured Markdown extraction for overlapping table blocks.
            overlap = False
            for rect in table_rects:
                inter = bbox & rect
                if inter.get_area() / max(bbox.get_area(), 1) > 0.45:
                    overlap = True
                    break
            if overlap:
                continue
        lines = []
        for line in block.get("lines", []):
            spans = [span.get("text", "") for span in line.get("spans", [])]
            line_text = "".join(spans).rstrip()
            if line_text.strip():
                lines.append(line_text)
        if lines:
            prose_parts.append((float(block["bbox"][1]), "\n".join(lines)))

    ordered = sorted(prose_parts + tables_md, key=lambda item: item[0])
    text = "\n\n".join(part for _, part in ordered).strip()
    if not text:
        text = page.get_text("text", sort=True).strip()
    return text, has_tables


def _matrix_to_markdown(rows: List[List]) -> str:
    if not rows:
        return ""
    normalized = [[("" if cell is None else str(cell).replace("\n", " ").strip()) for cell in row] for row in rows]
    width = max(len(row) for row in normalized)
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    header = normalized[0]
    body = normalized[1:] or [[""] * width]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _extract_pdf(
    path: Path,
    *,
    doc_id: str,
    filename: str,
    chunk_size: int,
    chunk_overlap: int,
    ocr_limit: int,
    progress: ProgressFn,
) -> DocumentExtraction:
    document = fitz.open(path)
    try:
        title = (document.metadata or {}).get("title") or path.stem
        extraction = DocumentExtraction(page_count=document.page_count, title=title)
        ocr_used = 0
        for page_index in range(document.page_count):
            page = document[page_index]
            page_number = page_index + 1
            _emit(
                progress,
                "extract",
                0.15 + 0.45 * (page_index / max(document.page_count, 1)),
                f"Reading PDF page {page_number}/{document.page_count}",
            )
            text, has_tables = _page_text_with_tables(page)
            if has_tables:
                extraction.has_tables = True
            if len(normalize_whitespace(text)) < 40:
                # Likely a scanned page — OCR a rasterized render.
                if ocr_used < ocr_limit:
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    ocr = ocr_pixmap_png(pixmap.tobytes("png"))
                    if ocr.text.strip():
                        text = ocr.text.strip()
                        ocr_used += 1
                        extraction.ocr_pages += 1
            if not text.strip():
                continue
            extraction.char_count += len(text)
            pieces = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            if not pieces:
                pieces = [text.strip()]
            for chunk_index, piece in enumerate(pieces):
                extraction.chunks.append(
                    ExtractedChunk(
                        text=piece,
                        metadata={
                            "doc_id": doc_id,
                            "source": filename,
                            "modality": "pdf",
                            "page": page_number,
                            "chunk_index": chunk_index,
                            "has_tables": has_tables,
                        },
                    )
                )
        return extraction
    finally:
        document.close()


def render_pdf_page(path: Path, page_number: int, scale: float = 1.6) -> tuple[bytes, int, int, str]:
    """Render a 1-indexed PDF page to PNG and return ``(png_bytes, width, height, text)``."""
    document = fitz.open(path)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"Page {page_number} out of range (1–{document.page_count})")
        page = document[page_number - 1]
        text, _ = _page_text_with_tables(page)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png"), pixmap.width, pixmap.height, text
    finally:
        document.close()
