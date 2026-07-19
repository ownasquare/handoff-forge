from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from handoff_forge.config import HandoffSettings
from handoff_forge.errors import ParseError
from handoff_forge.models import BlockKind
from handoff_forge.parsing.pdf import PDFParser


def _write_text_pdf(path: Path, *, pages: int = 1) -> None:
    document = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    for page in range(pages):
        document.drawString(72, 720, f"Validation blocker page {page + 1}")
        document.showPage()
    document.save()


def test_pdf_preserves_native_text_and_every_page_render(tmp_path: Path, settings) -> None:
    source = tmp_path / "evidence.pdf"
    _write_text_pdf(source)
    parser_settings = settings.model_copy(update={"ocr_native_text_threshold": 0})

    parsed = PDFParser(parser_settings).parse(source, project_id="project-a")

    assert any(
        block.kind is BlockKind.TEXT and "Validation blocker" in block.text
        for block in parsed.blocks
    )
    render = next(block for block in parsed.blocks if block.kind is BlockKind.PAGE_RENDER)
    assert render.artifact_path and render.artifact_path.is_file()
    assert render.metadata["artifact_sha256"]
    assert all(block.page_number == 1 and block.source_id for block in parsed.blocks)
    assert all(block.bbox is not None for block in parsed.blocks)


def test_pdf_page_limit_and_malformed_pdf_fail_closed(tmp_path: Path, settings) -> None:
    source = tmp_path / "two-pages.pdf"
    _write_text_pdf(source, pages=2)
    bounded = HandoffSettings(data_root=settings.data_root, max_pdf_pages=1)

    with pytest.raises(ParseError, match="configured limit"):
        PDFParser(bounded).parse(source)

    malformed = tmp_path / "malformed.pdf"
    malformed.write_bytes(b"%PDF-not-a-document")
    with pytest.raises(ParseError, match="malformed"):
        PDFParser(settings).parse(malformed)
