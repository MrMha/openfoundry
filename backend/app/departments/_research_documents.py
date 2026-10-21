"""Offline parsing with process deadlines, byte limits and Linux address-space limits."""

from __future__ import annotations

import base64
import contextlib
import importlib
import io
import json
import re
import sys
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from backend.app.core.research_worker import ResearchWorkerError, run_research_worker
from backend.app.departments._research_web import MAX_SOURCE_BYTES, SourceError, html_to_markdown

MAX_TEXT_CHARACTERS = 500_000


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARACTERS)
    backend: Literal["docling", "pypdf", "text", "html"]
    sections: list[str] = Field(max_length=1000)
    warnings: list[str] = Field(max_length=10)


def _docling(data: bytes, name: str) -> str:
    converter_module = importlib.import_module("docling.document_converter")
    models = importlib.import_module("docling.datamodel.base_models")
    # NativePdfFormatOption needs no layout/OCR weights or network downloads.
    # Older installations fall back rather than starting an unbounded model pipeline.
    native = converter_module.NativePdfFormatOption
    converter = converter_module.DocumentConverter(
        format_options={models.InputFormat.PDF: native()},
    )
    stream = models.DocumentStream(name=name, stream=io.BytesIO(data))
    result = converter.convert(stream, max_num_pages=100, max_file_size=MAX_SOURCE_BYTES)
    text: object = result.document.export_to_markdown()
    if not isinstance(text, str) or not text.strip():
        raise SourceError("Docling extracted no text")
    return text


def parse_bytes(data: bytes, name: str, *, prefer_docling: bool = True) -> ParsedDocument:
    """Synchronous parser entry point; applications should use parse_document instead."""
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise SourceError("Document is empty or exceeds byte limit")
    name = PurePosixPath(name).name[:255] or "document.txt"
    suffix = PurePosixPath(name).suffix.lower()
    warnings: list[str] = []
    backend: Literal["docling", "pypdf", "text", "html"] = "text"
    text: str | None = None
    if prefer_docling:
        try:
            text = _docling(data, name)
            backend = "docling"
        except Exception as error:
            warnings.append(
                f"Docling unavailable or unsuccessful ({type(error).__name__}); used fallback"
            )
    if text is None:
        if data.startswith(b"%PDF-") or suffix == ".pdf":
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted or len(reader.pages) > 100:
                raise SourceError("PDF is encrypted or exceeds 100 pages")
            pages: list[str] = []
            size = 0
            has_text = False
            for index, page in enumerate(reader.pages, 1):
                extracted = page.extract_text() or ""
                has_text = has_text or bool(extracted.strip())
                size += len(extracted)
                if size > MAX_TEXT_CHARACTERS:
                    raise SourceError("PDF exceeds extracted text limit")
                pages.append(f"## Page {index}\n\n{extracted.strip()}")
            if not has_text:
                raise SourceError("PDF has no text layer; OCR is required")
            text, backend = "\n\n".join(pages), "pypdf"
        else:
            if suffix not in {
                ".txt",
                ".md",
                ".markdown",
                ".json",
                ".csv",
                ".html",
                ".htm",
                ".xml",
                ".log",
            }:
                raise SourceError("Document format requires a working Docling installation")
            try:
                decoded = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise SourceError("Text documents must use UTF-8") from None
            if "\x00" in decoded:
                raise SourceError("Document contains binary data")
            if suffix in {".html", ".htm"}:
                _, text = html_to_markdown(decoded, "https://document.invalid/")
                backend = "html"
            elif suffix == ".json":
                text = (
                    "```json\n"
                    + json.dumps(json.loads(decoded), ensure_ascii=False, indent=2)
                    + "\n```"
                )
            else:
                text = decoded
    if not text or not text.strip() or len(text) > MAX_TEXT_CHARACTERS:
        raise SourceError("Document has no text or exceeds the extracted text limit")
    sections = re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)[:1000]
    return ParsedDocument(
        title=name, text=text.strip(), backend=backend, sections=sections, warnings=warnings
    )


async def parse_document(
    data: bytes, name: str, *, prefer_docling: bool = True, timeout_seconds: float = 30
) -> ParsedDocument:
    """Kill and reap the parser on timeout/cancellation; never expose provider credentials."""
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise SourceError("Document is empty or exceeds byte limit")
    if not 0 < timeout_seconds <= 60:
        raise ValueError("Parser timeout must be between zero and 60 seconds")
    payload = json.dumps(
        {"data": base64.b64encode(data).decode(), "name": name, "prefer_docling": prefer_docling}
    ).encode()
    try:
        output = await run_research_worker(
            "backend.app.departments._research_documents",
            payload,
            timeout_seconds=timeout_seconds,
            max_output_bytes=4 * MAX_SOURCE_BYTES,
        )
    except ResearchWorkerError:
        raise SourceError(
            "Document parsing failed; unsupported, corrupt, or resource limit exceeded"
        ) from None
    return ParsedDocument.model_validate_json(output)


def _resource_limits() -> list[str]:
    warnings: list[str] = []
    if sys.platform != "win32":
        import resource

        limits = [
            (resource.RLIMIT_CPU, 20),
            (resource.RLIMIT_FSIZE, 16 * MAX_SOURCE_BYTES),
            (resource.RLIMIT_CORE, 0),
        ]
        if sys.platform == "linux":
            limits.append((resource.RLIMIT_AS, 1536 * 1024 * 1024))
        else:
            warnings.append("No address-space cap on this platform; byte and time limits remain")
        for kind, requested in limits:
            _, hard = resource.getrlimit(kind)
            cap = min(requested, hard) if hard != resource.RLIM_INFINITY else requested
            resource.setrlimit(kind, (cap, cap))
    return warnings


def _worker() -> None:
    limit_warnings = _resource_limits()

    def offline(event: str, args: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise OSError("Document conversion runs offline")

    sys.addaudithook(offline)
    request = json.loads(sys.stdin.buffer.read(3 * MAX_SOURCE_BYTES))
    data = base64.b64decode(request["data"], validate=True)
    with contextlib.redirect_stdout(sys.stderr):
        result = parse_bytes(data, request["name"], prefer_docling=request["prefer_docling"])
    result.warnings = (limit_warnings + result.warnings)[:10]
    # This is a byte protocol, independent of Windows' legacy console encoding.
    sys.stdout.buffer.write(result.model_dump_json().encode("utf-8"))


if __name__ == "__main__":
    _worker()
