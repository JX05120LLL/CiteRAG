"""Private worker protocol; stdout carries results, never diagnostic document text.

Resource limits are installed before importing any document parser. This is resource
containment, not a security sandbox for arbitrary code execution.
"""

import json
import os
import sys
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARACTERS = 5_000_000
MAX_BLOCKS = 50_000
MAX_MEMORY_BYTES = 384 * 1024 * 1024


class ParseFailure(ValueError):
    pass


def _limit_resources():
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 21))
        return None

    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x100 | 0x8 | 0x2 | 0x2000
    limits.BasicLimitInformation.ActiveProcessLimit = 1
    limits.BasicLimitInformation.PerProcessUserTimeLimit = 20 * 10_000_000
    limits.ProcessMemoryLimit = MAX_MEMORY_BYTES
    if (
        not handle
        or not kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())
    ):
        raise ParseFailure("parse_unavailable")
    # Kept open until this worker exits; closing it kills the limited process.
    return handle


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if any(
        (ord(character) < 32 and character not in "\n\t")
        or character == "\ufffd"
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in text
    ):
        raise ParseFailure("invalid_document")
    return text


def _assemble(items: list[tuple[str, dict]]) -> dict:
    blocks = []
    parts = []
    offset = 0
    for text, locator in items:
        text = _clean(text).strip()
        if not text:
            continue
        if len(blocks) >= MAX_BLOCKS or offset + len(text) > MAX_TEXT_CHARACTERS:
            raise ParseFailure("parse_limit")
        blocks.append(
            {"text": text, "locator": locator, "start": offset, "end": offset + len(text)}
        )
        parts.append(text)
        offset += len(text) + 2
    if not blocks:
        raise ParseFailure("empty_document")
    return {"text": "\n\n".join(parts), "blocks": blocks}


def _text(path: Path) -> dict:
    text = _clean(path.read_bytes().decode("utf-8-sig")).rstrip("\n")
    if not text.strip():
        raise ParseFailure("empty_document")
    if len(text) > MAX_TEXT_CHARACTERS:
        raise ParseFailure("parse_limit")
    blocks = []
    offset = 0
    start = None
    start_line = 0
    lines = text.split("\n")
    for number, line in enumerate([*lines, ""], 1):
        if line.strip() and start is None:
            start, start_line = offset, number
        if not line.strip() and start is not None:
            end = offset - 1
            blocks.append(
                {
                    "text": text[start:end],
                    "start": start,
                    "end": end,
                    "locator": {"kind": "lines", "line_start": start_line, "line_end": number - 1},
                }
            )
            start = None
            if len(blocks) > MAX_BLOCKS:
                raise ParseFailure("parse_limit")
        offset += len(line) + 1
    return {"text": text, "blocks": blocks}


def _pdf(path: Path) -> dict:
    from pypdf import PdfReader

    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ParseFailure("invalid_document")
        stream.seek(0)
        reader = PdfReader(stream, strict=True)
        if reader.is_encrypted:
            raise ParseFailure("unsupported_document")
        if not 0 < len(reader.pages) <= 100:
            raise ParseFailure("parse_limit")
        items = []
        size = 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            # Do not silently omit scanned pages in mixed text/scanned documents.
            if not text.strip() and page.get("/Resources", {}).get("/XObject"):
                raise ParseFailure("unsupported_document")
            size += len(text)
            if size > MAX_TEXT_CHARACTERS:
                raise ParseFailure("parse_limit")
            items.append((text, {"kind": "page", "page": number}))
    return _assemble(items)


def _check_docx_archive(path: Path) -> None:
    with ZipFile(path) as archive:
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if (
            len(entries) > 2000
            or len(names) != len(entries)
            or "[Content_Types].xml" not in names
            or "word/document.xml" not in names
            or sum(entry.file_size for entry in entries) > 80 * 1024 * 1024
        ):
            raise ParseFailure("invalid_document")
        for entry in entries:
            if (
                PurePosixPath(entry.filename).is_absolute()
                or ".." in PurePosixPath(entry.filename).parts
                or "\\" in entry.filename
                or ":" in entry.filename
                or entry.flag_bits & 1
                or entry.file_size > MAX_FILE_BYTES
                or (entry.file_size > 1024 * 1024 and entry.file_size > 100 * entry.compress_size)
            ):
                raise ParseFailure("parse_limit")
            if entry.filename.endswith((".xml", ".rels")):
                data = archive.read(entry)
                if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
                    raise ParseFailure("unsupported_document")
                if entry.filename.startswith(("word/header", "word/footer")):
                    root = ElementTree.fromstring(data)
                    for element in root.iter():
                        tag = element.tag.rsplit("}", 1)[-1]
                        if (tag == "t" and (element.text or "").strip()) or tag in {
                            "drawing",
                            "pict",
                            "object",
                            "fldChar",
                            "fldSimple",
                        }:
                            raise ParseFailure("unsupported_document")
            if entry.filename.startswith(("word/embeddings/", "word/activeX/")):
                raise ParseFailure("unsupported_document")


def _docx(path: Path) -> dict:
    _check_docx_archive(path)
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(path)
    unsupported = {
        qn(f"w:{tag}")
        for tag in (
            "drawing",
            "pict",
            "object",
            "altChunk",
            "sdt",
            "ins",
            "del",
            "fldChar",
            "fldSimple",
            "footnoteReference",
            "endnoteReference",
            "vMerge",
        )
    }
    for element in document.element.body.iter():
        if element.tag in unsupported:
            raise ParseFailure("unsupported_document")
        if element.tag == qn("w:gridSpan") and element.get(qn("w:val")) != "1":
            raise ParseFailure("unsupported_document")
    items = []
    paragraph_number = table_number = 0
    for child in document.element.body:
        if child.tag == qn("w:p"):
            paragraph_number += 1
            paragraph = Paragraph(child, document)
            items.append((paragraph.text, {"kind": "paragraph", "paragraph": paragraph_number}))
        elif child.tag == qn("w:tbl"):
            table_number += 1
            table = Table(child, document)
            for row_number, row in enumerate(table.rows, 1):
                if row.grid_cols_before or row.grid_cols_after:
                    raise ParseFailure("unsupported_document")
                if any(cell.tables for cell in row.cells):
                    raise ParseFailure("unsupported_document")
                items.append(
                    (
                        "\t".join(cell.text for cell in row.cells),
                        {
                            "kind": "table",
                            "table": table_number,
                            "row": row_number,
                        },
                    )
                )
        elif child.tag != qn("w:sectPr"):
            raise ParseFailure("unsupported_document")
    return _assemble(items)


def main() -> None:
    try:
        _job_handle = _limit_resources()
        path, extension = Path(sys.argv[1]), sys.argv[2]
        if not 0 < path.stat().st_size <= MAX_FILE_BYTES:
            raise ParseFailure("parse_limit")
        parser = {".txt": _text, ".md": _text, ".pdf": _pdf, ".docx": _docx}.get(extension)
        if parser is None:
            raise ParseFailure("unsupported_document")
        result = parser(path)
    except ParseFailure as error:
        result = {"error": str(error)}
    except MemoryError:
        result = {"error": "parse_limit"}
    except (OSError, ValueError, BadZipFile):
        result = {"error": "invalid_document"}
    except Exception:
        # Isolation boundary: third-party parser exceptions may embed document data.
        # Fail closed with a fixed code, never propagate their messages or tracebacks.
        result = {"error": "invalid_document"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
