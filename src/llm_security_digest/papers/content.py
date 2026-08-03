from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .models import normalize_title


class PaperHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.citation_title = ""
        self.text_parts: list[str] = []
        self.sections: list[dict[str, Any]] = []
        self._ignored = 0
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("name") == "citation_title":
            self.citation_title = values.get("content") or ""
        if tag in {"script", "style", "nav", "footer"}:
            self._ignored += 1
        if tag in {"h1", "h2", "h3", "h4"} and not self._ignored:
            self._heading_tag = tag
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"} and self._ignored:
            self._ignored -= 1
        if self._heading_tag == tag:
            title = " ".join("".join(self._heading_parts).split())
            if title:
                self.sections.append({"title": title, "offset": len(" ".join(self.text_parts))})
            self._heading_tag = None
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        value = " ".join(data.split())
        if value:
            self.text_parts.append(value)
            if self._heading_tag:
                self._heading_parts.append(value)


def extract_pdf(body: bytes, expected_title: str) -> tuple[str, list[dict[str, Any]]]:
    if not body.startswith(b"%PDF-"):
        raise ValueError("response is not a PDF")
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - environment check handles this
        raise RuntimeError("PyMuPDF is required for PDF identity validation") from exc
    document = fitz.open(stream=body, filetype="pdf")
    try:
        if document.page_count < 1:
            raise ValueError("PDF has no pages")
        page_texts = [document.load_page(index).get_text("text") for index in range(document.page_count)]
    finally:
        document.close()
    identity_text = normalize_title(" ".join(page_texts[:2]))
    expected = normalize_title(expected_title)
    if not expected or expected not in identity_text:
        raise ValueError("PDF title does not match authoritative metadata")
    sections = [{"id": f"page-{index + 1}", "title": f"Page {index + 1}", "page": index + 1} for index in range(len(page_texts))]
    return "\n\n".join(f"[[PAGE {index + 1}]]\n{text.strip()}" for index, text in enumerate(page_texts)), sections


def extract_html(body: bytes, expected_title: str) -> tuple[str, list[dict[str, Any]]]:
    decoded = body.decode("utf-8", errors="replace")
    parser = PaperHtmlParser()
    parser.feed(decoded)
    if normalize_title(parser.citation_title) != normalize_title(expected_title):
        raise ValueError("HTML title does not match authoritative metadata")
    text = " ".join(parser.text_parts)
    if len(text) < 2000:
        raise ValueError("HTML does not contain a full paper")
    sections = []
    for index, section in enumerate(parser.sections):
        sections.append({"id": f"section-{index + 1}", **section})
    return html.unescape(text), sections


def persist_content(
    *,
    data_dir: Path,
    paper_id: str,
    body: bytes,
    extracted_text: str,
    sections: list[dict[str, Any]],
    extension: str,
    source_url: str,
) -> dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", paper_id)
    paper_dir = data_dir / "papers" / safe_id / digest
    paper_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = paper_dir / f"paper.{extension}"
    text_path = paper_dir / "paper.txt"
    outline_path = paper_dir / "outline.json"
    artifact_path.write_bytes(body)
    text_path.write_text(extracted_text, encoding="utf-8")
    import json

    outline_path.write_text(json.dumps(sections, ensure_ascii=False, indent=2), encoding="utf-8")

    # Keep the facts snapshot portable between runs and machines. The data
    # directory is supplied through LLMSD_DATA_DIR when Hermes reads these
    # artifacts; absolute paths from a temporary runner would otherwise make
    # progressive reading fail after the process exits.
    relative_artifact = artifact_path.relative_to(data_dir).as_posix()
    relative_text = text_path.relative_to(data_dir).as_posix()
    relative_outline = outline_path.relative_to(data_dir).as_posix()
    return {
        "sha256": digest,
        "path": relative_artifact,
        "text_path": relative_text,
        "outline_path": relative_outline,
        "source_url": source_url,
        "format": extension,
        "text_chars": len(extracted_text),
        "sections": len(sections),
    }
