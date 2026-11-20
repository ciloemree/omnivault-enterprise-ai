"""Spreadsheet and Word extraction. CSV and docx XML use the standard library."""

from __future__ import annotations

import csv
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def spreadsheet_to_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _csv(path)
    if suffix == ".xlsx":
        return _xlsx(path)
    if suffix == ".xls":
        return _xls(path)
    raise ValueError(f"Not a spreadsheet: {path.name}")


def docx_to_text(path: Path) -> str:
    try:
        import docx
    except ImportError:
        return _docx_xml(path)
    document = docx.Document(str(path))
    parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        rows = [[cell.text.strip().replace("\n", " ") for cell in row.cells] for row in table.rows]
        rendered = _markdown_table(rows)
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)


def _docx_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    parts: List[str] = []
    for paragraph in root.iter(f"{_W}p"):
        texts = [node.text or "" for node in paragraph.iter(f"{_W}t")]
        line = "".join(texts).strip()
        if line:
            parts.append(line)
    return "\n\n".join(parts)


def _markdown_table(rows: List[List[str]]) -> str:
    kept = [row for row in rows if any(cell.strip() for cell in row)]
    if not kept:
        return ""
    width = max(len(row) for row in kept)
    normalized = [row + [""] * (width - len(row)) for row in kept]
    header = normalized[0]
    body = normalized[1:] or [[""] * width]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(cell.replace("|", "/") for cell in row) + " |")
    return "\n".join(lines)


def _csv(path: Path) -> str:
    rows: List[List[str]] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(handle, dialect)
        for index, row in enumerate(reader):
            if index > 5000:
                break
            rows.append([cell.strip() for cell in row])
    return _markdown_table(rows)


def _xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed") from exc
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    blocks = []
    try:
        for sheet in workbook.worksheets:
            rows = []
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index > 5000:
                    break
                cells = ["" if value is None else str(value).strip() for value in row]
                if any(cells):
                    rows.append(cells)
            table = _markdown_table(rows)
            if table:
                blocks.append(f"# {sheet.title}\n\n{table}")
    finally:
        workbook.close()
    return "\n\n".join(blocks)


def _xls(path: Path) -> str:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError("Reading .xls needs xlrd.") from exc
    book = xlrd.open_workbook(str(path))
    blocks = []
    for sheet in book.sheets():
        rows = []
        limit = min(sheet.nrows, 5000)
        for index in range(limit):
            cells = [str(sheet.cell_value(index, col)).strip() for col in range(sheet.ncols)]
            if any(cells):
                rows.append(cells)
        table = _markdown_table(rows)
        if table:
            blocks.append(f"# {sheet.name}\n\n{table}")
    return "\n\n".join(blocks)
