from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from handoff_forge.ingestion.nodes import NodeBuilder
from handoff_forge.models import BlockKind, ContentBlock
from handoff_forge.parsing.pdf import PDFParser
from handoff_forge.retrieval.embeddings import DeterministicHashEmbedding
from handoff_forge.retrieval.index import ChromaIndex


def _write_multimodal_pdf(path: Path, image_path: Path) -> None:
    image = Image.new("RGB", (240, 100), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((10, 50, 60, 90), fill="navy")
    drawing.rectangle((80, 25, 130, 90), fill="teal")
    drawing.rectangle((150, 10, 200, 90), fill="purple")
    image.save(image_path)

    document = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    document.drawString(72, 740, "Performance evidence")
    left, bottom, width, height = 72, 620, 260, 72
    document.rect(left, bottom, width, height)
    document.line(left, bottom + 36, left + width, bottom + 36)
    document.line(left + 130, bottom, left + 130, bottom + height)
    document.drawString(left + 8, bottom + 50, "Metric")
    document.drawString(left + 138, bottom + 50, "Value")
    document.drawString(left + 8, bottom + 14, "Latency")
    document.drawString(left + 138, bottom + 14, "42 ms")
    document.drawImage(ImageReader(str(image_path)), 72, 450, width=240, height=100)
    document.showPage()
    document.save()


def test_pdf_preserves_text_table_and_visual_provenance(tmp_path: Path, settings) -> None:
    source = tmp_path / "multimodal.pdf"
    image_path = tmp_path / "chart.png"
    _write_multimodal_pdf(source, image_path)
    parser_settings = settings.model_copy(update={"ocr_native_text_threshold": 0})

    parsed = PDFParser(parser_settings).parse(source, project_id="project-a")

    assert any(block.kind is BlockKind.TABLE and "Latency" in block.text for block in parsed.blocks)
    assert any(
        block.kind is BlockKind.IMAGE and block.artifact_path and block.artifact_path.is_file()
        for block in parsed.blocks
    )
    assert any(block.kind is BlockKind.PAGE_RENDER for block in parsed.blocks)
    assert all(block.page_number and block.source_id for block in parsed.blocks)
    assert all(block.metadata["parser_version"] == parsed.parser_profile for block in parsed.blocks)
    visuals = [
        block for block in parsed.blocks if block.kind in {BlockKind.IMAGE, BlockKind.PAGE_RENDER}
    ]
    assert visuals and all("Latency" in block.text and "42 ms" in block.text for block in visuals)
    assert all(block.metadata["visual_context_source_kinds"] for block in visuals)


def test_chart_meaning_retrieves_a_managed_visual_block(tmp_path: Path, settings) -> None:
    source = tmp_path / "multimodal.pdf"
    image_path = tmp_path / "chart.png"
    _write_multimodal_pdf(source, image_path)
    parser_settings = settings.model_copy(update={"ocr_native_text_threshold": 0})
    parsed = PDFParser(parser_settings).parse(source, project_id="project-a")
    distractors = [
        ContentBlock(
            id=f"distractor-{index}",
            project_id="project-a",
            artifact_id="distractor-artifact",
            artifact_sha256="d" * 64,
            kind=BlockKind.TEXT,
            text=f"Unrelated release process note {index}.",
            order=100 + index,
            extraction_method="fixture",
        )
        for index in range(8)
    ]
    nodes = NodeBuilder(settings).build([*parsed.blocks, *distractors])
    index = ChromaIndex(tmp_path / "visual-index", DeterministicHashEmbedding(dimensions=64))
    index.upsert(nodes)

    hits = index.search(
        project_id="project-a",
        query="performance latency metric 42 ms chart",
        limit=4,
    )

    visual_hits = [
        hit
        for hit in hits
        if hit.metadata.get("block_kind") in {BlockKind.IMAGE.value, BlockKind.PAGE_RENDER.value}
    ]
    assert visual_hits
    assert all(Path(str(hit.metadata["artifact_path"])).is_file() for hit in visual_hits)
