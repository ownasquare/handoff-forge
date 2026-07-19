"""Bounded multimodal PDF parsing with deterministic page preservation."""

from __future__ import annotations

import io
import math
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from handoff_forge.errors import ParseError
from handoff_forge.models import (
    BlockKind,
    ContentBlock,
    ParsedDocument,
    ParseWarning,
    SourceArtifact,
)
from handoff_forge.parsing.base import DocumentParser, normalized_bbox, stable_block_id
from handoff_forge.parsing.ocr import OCRFailure, OCRTimeout, OCRUnavailable, extract_ocr
from handoff_forge.security import FILE_MODE, ensure_directory, sha256_bytes

_MAX_RENDER_PIXELS = 25_000_000
_MAX_VISUAL_CONTEXT_CHARACTERS = 2_000
_MAX_VISUAL_OCR_CHARACTERS = 600


class PDFParser(DocumentParser):
    """Preserve native text, tables, every page render, image crops, and OCR."""

    parser_profile = "pdfplumber-pdfium-ocr-v2"

    def parse(
        self,
        source: SourceArtifact | Path,
        *,
        project_id: str = "standalone",
    ) -> ParsedDocument:
        artifact = self.coerce_artifact(source, project_id=project_id)
        if artifact.stored_path.suffix.casefold() != ".pdf":
            raise ParseError("PDFParser accepts only .pdf sources")

        warnings: list[ParseWarning] = []
        blocks: list[ContentBlock] = []
        derived = self.derived_directory(artifact)
        renders = ensure_directory(derived / "pages")
        crops = ensure_directory(derived / "images")

        pdfium_document = self._open_pdfium(artifact.stored_path)
        try:
            page_count = len(pdfium_document)
            if page_count > self.settings.max_pdf_pages:
                raise ParseError(
                    f"PDF has {page_count} pages; configured limit is {self.settings.max_pdf_pages}"
                )
            if page_count < 1:
                raise ParseError("PDF contains no pages")
            try:
                plumber_document = pdfplumber.open(artifact.stored_path)
            except Exception as exc:
                raise self._pdf_open_error(exc) from exc
            with plumber_document:
                if len(plumber_document.pages) != page_count:
                    warnings.append(
                        ParseWarning(
                            code="pdf_page_count_mismatch",
                            message="PDF render and text engines reported different page counts.",
                        )
                    )
                for page_index in range(page_count):
                    page_number = page_index + 1
                    page_block_start = len(blocks)
                    rendered, render_scale = self._render_page(pdfium_document, page_index)
                    render_path, render_digest = self._save_image(
                        rendered,
                        renders,
                        prefix=f"page-{page_number:04d}",
                    )
                    plumber_page = (
                        plumber_document.pages[page_index]
                        if page_index < len(plumber_document.pages)
                        else None
                    )
                    page_width = float(plumber_page.width) if plumber_page is not None else 1.0
                    page_height = float(plumber_page.height) if plumber_page is not None else 1.0

                    blocks.append(
                        self._block(
                            artifact,
                            BlockKind.PAGE_RENDER,
                            f"Rendered visual evidence for PDF page {page_number}.",
                            len(blocks),
                            page_number=page_number,
                            bbox=(0.0, 0.0, 1.0, 1.0),
                            artifact_path=render_path,
                            extraction_method="pypdfium2-page-render",
                            metadata={
                                "artifact_sha256": render_digest,
                                "render_scale": render_scale,
                                "pixel_width": rendered.width,
                                "pixel_height": rendered.height,
                            },
                        )
                    )

                    native_text = ""
                    if plumber_page is not None:
                        try:
                            native_text = (plumber_page.extract_text() or "").strip()
                        except Exception:
                            warnings.append(
                                ParseWarning(
                                    code="native_text_extraction_failed",
                                    message=f"Native text extraction failed on page {page_number}.",
                                    page_number=page_number,
                                )
                            )
                        if native_text:
                            blocks.append(
                                self._block(
                                    artifact,
                                    BlockKind.TEXT,
                                    native_text,
                                    len(blocks),
                                    page_number=page_number,
                                    bbox=(0.0, 0.0, 1.0, 1.0),
                                    extraction_method="pdfplumber-native-text",
                                )
                            )

                        blocks.extend(
                            self._table_blocks(
                                artifact,
                                plumber_page,
                                page_number=page_number,
                                start_order=len(blocks),
                                warnings=warnings,
                            )
                        )
                        blocks.extend(
                            self._image_blocks(
                                artifact,
                                plumber_page,
                                rendered,
                                crops,
                                page_number=page_number,
                                start_order=len(blocks),
                                page_width=page_width,
                                page_height=page_height,
                                warnings=warnings,
                            )
                        )

                    if len(native_text) < self.settings.ocr_native_text_threshold:
                        self._append_ocr_block(
                            artifact,
                            rendered,
                            blocks,
                            warnings,
                            page_number=page_number,
                            render_path=render_path,
                            render_digest=render_digest,
                        )
                    self._enrich_page_visual_blocks(
                        artifact,
                        blocks,
                        page_block_start=page_block_start,
                    )
        finally:
            pdfium_document.close()

        return ParsedDocument(
            artifact=artifact,
            blocks=blocks,
            warnings=warnings,
            parser_profile=self.parser_profile,
        )

    @staticmethod
    def _open_pdfium(path: Path) -> Any:
        try:
            return pdfium.PdfDocument(str(path))
        except Exception as exc:
            raise PDFParser._pdf_open_error(exc) from exc

    @staticmethod
    def _pdf_open_error(exc: Exception) -> ParseError:
        message = str(exc).casefold()
        if "password" in message or "encrypted" in message or "security handler" in message:
            return ParseError("encrypted PDF cannot be parsed without a password")
        return ParseError("PDF is malformed, truncated, or unsupported")

    def _render_page(self, document: Any, page_index: int) -> tuple[Image.Image, float]:
        page = document[page_index]
        try:
            width, height = page.get_size()
            configured = self.settings.pdf_render_scale
            if configured <= 0:
                raise ParseError("pdf_render_scale must be positive")
            expected_pixels = width * configured * height * configured
            scale = configured
            if expected_pixels > _MAX_RENDER_PIXELS:
                scale = math.sqrt(_MAX_RENDER_PIXELS / max(1.0, width * height))
            bitmap = page.render(scale=scale)
            try:
                rendered = bitmap.to_pil().convert("RGB").copy()
            finally:
                bitmap.close()
            return rendered, scale
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"could not render PDF page {page_index + 1}") from exc
        finally:
            page.close()

    def _table_blocks(
        self,
        artifact: SourceArtifact,
        page: Any,
        *,
        page_number: int,
        start_order: int,
        warnings: list[ParseWarning],
    ) -> list[ContentBlock]:
        try:
            tables = page.find_tables()
        except Exception:
            warnings.append(
                ParseWarning(
                    code="table_extraction_failed",
                    message=(
                        f"Table extraction failed on page {page_number}; "
                        "the page render is preserved."
                    ),
                    page_number=page_number,
                )
            )
            return []
        blocks: list[ContentBlock] = []
        for table_index, table in enumerate(tables):
            try:
                rows = table.extract() or []
            except Exception:
                warnings.append(
                    ParseWarning(
                        code="table_decode_failed",
                        message=(
                            f"A detected table on page {page_number} could not be decoded; "
                            "the page render is preserved."
                        ),
                        page_number=page_number,
                    )
                )
                continue
            text = self._table_as_markdown(rows)
            if not text:
                continue
            x0, top, x1, bottom = (float(item) for item in table.bbox)
            bbox = normalized_bbox(
                (x0, top, x1, bottom),
                width=float(page.width),
                height=float(page.height),
            )
            blocks.append(
                self._block(
                    artifact,
                    BlockKind.TABLE,
                    text,
                    start_order + len(blocks),
                    page_number=page_number,
                    bbox=bbox,
                    extraction_method="pdfplumber-table",
                    metadata={"table_index": table_index},
                )
            )
        return blocks

    def _image_blocks(
        self,
        artifact: SourceArtifact,
        page: Any,
        rendered: Image.Image,
        crops_directory: Path,
        *,
        page_number: int,
        start_order: int,
        page_width: float,
        page_height: float,
        warnings: list[ParseWarning],
    ) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for image_index, image_info in enumerate(page.images):
            bbox = self._image_bbox(image_info, page_height=page_height)
            if bbox is None:
                warnings.append(
                    ParseWarning(
                        code="image_bbox_missing",
                        message=f"An image on page {page_number} had no usable bounding box.",
                        page_number=page_number,
                    )
                )
                continue
            x0, top, x1, bottom = bbox
            pixel_box = (
                max(0, round(x0 / page_width * rendered.width)),
                max(0, round(top / page_height * rendered.height)),
                min(rendered.width, round(x1 / page_width * rendered.width)),
                min(rendered.height, round(bottom / page_height * rendered.height)),
            )
            if pixel_box[2] - pixel_box[0] < 4 or pixel_box[3] - pixel_box[1] < 4:
                continue
            crop = rendered.crop(pixel_box)
            crop_path, crop_digest = self._save_image(
                crop,
                crops_directory,
                prefix=f"page-{page_number:04d}-image-{image_index:03d}",
            )
            blocks.append(
                self._block(
                    artifact,
                    BlockKind.IMAGE,
                    f"Embedded visual evidence {image_index + 1} on PDF page {page_number}.",
                    start_order + len(blocks),
                    page_number=page_number,
                    bbox=normalized_bbox(bbox, width=page_width, height=page_height),
                    artifact_path=crop_path,
                    extraction_method="pdfplumber-image-bbox+pypdfium2-crop",
                    metadata={
                        "artifact_sha256": crop_digest,
                        "image_index": image_index,
                        "pixel_width": crop.width,
                        "pixel_height": crop.height,
                    },
                )
            )
        return blocks

    def _append_ocr_block(
        self,
        artifact: SourceArtifact,
        rendered: Image.Image,
        blocks: list[ContentBlock],
        warnings: list[ParseWarning],
        *,
        page_number: int,
        render_path: Path,
        render_digest: str,
    ) -> None:
        try:
            result = extract_ocr(
                rendered,
                language=self.settings.ocr_language,
                timeout_seconds=self.settings.ocr_timeout_seconds,
            )
        except OCRUnavailable:
            warnings.append(
                ParseWarning(
                    code="ocr_unavailable",
                    message=(
                        f"Page {page_number} has little native text. Its render is preserved, "
                        "but Tesseract is unavailable; install it to enable OCR."
                    ),
                    page_number=page_number,
                )
            )
            return
        except OCRTimeout:
            warnings.append(
                ParseWarning(
                    code="ocr_timeout",
                    message=(
                        f"OCR timed out on page {page_number}; increase the configured timeout "
                        "or inspect the preserved render."
                    ),
                    page_number=page_number,
                )
            )
            return
        except OCRFailure:
            warnings.append(
                ParseWarning(
                    code="ocr_failed",
                    message=f"OCR failed on page {page_number}; the page render is preserved.",
                    page_number=page_number,
                )
            )
            return
        if not result.text:
            warnings.append(
                ParseWarning(
                    code="ocr_no_text",
                    message=(
                        f"OCR found no text on page {page_number}; the page render is preserved."
                    ),
                    page_number=page_number,
                    actionable=False,
                )
            )
            return
        blocks.append(
            self._block(
                artifact,
                BlockKind.OCR,
                result.text,
                len(blocks),
                page_number=page_number,
                bbox=(0.0, 0.0, 1.0, 1.0),
                artifact_path=render_path,
                extraction_method="pytesseract-page-ocr",
                confidence=result.confidence,
                metadata={"artifact_sha256": render_digest, "language": self.settings.ocr_language},
            )
        )

    def _enrich_page_visual_blocks(
        self,
        artifact: SourceArtifact,
        blocks: list[ContentBlock],
        *,
        page_block_start: int,
    ) -> None:
        """Attach bounded extracted page meaning to retrievable visual artifacts."""

        page_blocks = blocks[page_block_start:]
        native_blocks = [
            block for block in page_blocks if block.kind in {BlockKind.TEXT, BlockKind.TABLE}
        ]
        ocr_blocks = [block for block in page_blocks if block.kind is BlockKind.OCR]
        if ocr_blocks and native_blocks:
            native_budget = _MAX_VISUAL_CONTEXT_CHARACTERS - _MAX_VISUAL_OCR_CHARACTERS
            ocr_budget = _MAX_VISUAL_OCR_CHARACTERS
        elif native_blocks:
            native_budget = _MAX_VISUAL_CONTEXT_CHARACTERS
            ocr_budget = 0
        else:
            native_budget = 0
            ocr_budget = _MAX_VISUAL_CONTEXT_CHARACTERS
        native_context, native_truncated = self._bounded_context(native_blocks, native_budget)
        ocr_context, ocr_truncated = self._bounded_context(ocr_blocks, ocr_budget)
        context_kinds = list(
            dict.fromkeys(block.kind.value for block in [*native_blocks, *ocr_blocks])
        )

        for index in range(page_block_start, len(blocks)):
            block = blocks[index]
            if block.kind not in {BlockKind.IMAGE, BlockKind.CHART, BlockKind.PAGE_RENDER}:
                continue
            text_parts = [block.text]
            if native_context:
                text_parts.append(f"Same-page native text/table context:\n{native_context}")
            if ocr_context:
                text_parts.append(f"Optional OCR-derived visual text:\n{ocr_context}")
            enriched_text = "\n\n".join(text_parts)
            metadata = {
                **block.metadata,
                "visual_context_characters": len(native_context) + len(ocr_context),
                "visual_context_includes_ocr": bool(ocr_context),
                "visual_context_source_kinds": context_kinds,
                "visual_context_truncated": native_truncated or ocr_truncated,
            }
            location = {"page_number": block.page_number, "bbox": block.bbox}
            blocks[index] = block.model_copy(
                update={
                    "id": stable_block_id(
                        artifact,
                        kind=block.kind.value,
                        order=block.order,
                        text=enriched_text,
                        location=location,
                    ),
                    "text": enriched_text,
                    "metadata": metadata,
                }
            )

    @staticmethod
    def _bounded_context(
        blocks: list[ContentBlock],
        character_limit: int,
    ) -> tuple[str, bool]:
        if character_limit <= 0 or not blocks:
            return "", False
        fragments: list[str] = []
        seen: set[str] = set()
        used = 0
        truncated = False
        for block in blocks:
            normalized = " ".join(block.text.split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            label = {
                BlockKind.TEXT: "Native text",
                BlockKind.TABLE: "Extracted table",
                BlockKind.OCR: "OCR",
            }.get(block.kind, block.kind.value)
            fragment = f"{label}: {normalized}"
            separator_size = 1 if fragments else 0
            remaining = character_limit - used - separator_size
            if remaining <= 0:
                truncated = True
                break
            if len(fragment) > remaining:
                fragment = fragment[: max(0, remaining - 3)].rstrip() + "..."
                truncated = True
            fragments.append(fragment)
            used += separator_size + len(fragment)
            if truncated:
                break
        return "\n".join(fragments), truncated

    def _block(
        self,
        artifact: SourceArtifact,
        kind: BlockKind,
        text: str,
        order: int,
        *,
        page_number: int,
        bbox: tuple[float, float, float, float] | None,
        artifact_path: Path | None = None,
        extraction_method: str,
        confidence: float = 1.0,
        metadata: dict[str, object] | None = None,
    ) -> ContentBlock:
        location = {"page_number": page_number, "bbox": bbox}
        return ContentBlock(
            id=stable_block_id(
                artifact,
                kind=kind.value,
                order=order,
                text=text,
                location=location,
            ),
            project_id=artifact.project_id,
            artifact_id=artifact.id,
            artifact_sha256=artifact.sha256,
            kind=kind,
            text=text,
            order=order,
            page_number=page_number,
            bbox=bbox,
            artifact_path=artifact_path,
            extraction_method=extraction_method,
            confidence=confidence,
            metadata={
                "parser_version": self.parser_profile,
                "untrusted_evidence": True,
                **(metadata or {}),
            },
        )

    @staticmethod
    def _table_as_markdown(rows: list[list[str | None]]) -> str:
        normalized: list[list[str]] = []
        width = 0
        for row in rows:
            cells = [
                str(cell or "").replace("|", "\\|").replace("\n", "<br>").strip() for cell in row
            ]
            if any(cells):
                normalized.append(cells)
                width = max(width, len(cells))
        if not normalized or width == 0:
            return ""
        padded = [row + [""] * (width - len(row)) for row in normalized]
        header = padded[0]
        if not any(header):
            header = [f"Column {index + 1}" for index in range(width)]
        rendered = [f"| {' | '.join(header)} |", f"| {' | '.join(['---'] * width)} |"]
        rendered.extend(f"| {' | '.join(row)} |" for row in padded[1:])
        return "\n".join(rendered)

    @staticmethod
    def _image_bbox(
        image_info: dict[str, Any],
        *,
        page_height: float,
    ) -> tuple[float, float, float, float] | None:
        try:
            x0 = float(image_info["x0"])
            x1 = float(image_info["x1"])
            if "top" in image_info and "bottom" in image_info:
                top = float(image_info["top"])
                bottom = float(image_info["bottom"])
            else:
                top = page_height - float(image_info["y1"])
                bottom = page_height - float(image_info["y0"])
        except (KeyError, TypeError, ValueError):
            return None
        if x1 <= x0 or bottom <= top:
            return None
        return x0, top, x1, bottom

    @staticmethod
    def _save_image(image: Image.Image, directory: Path, *, prefix: str) -> tuple[Path, str]:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", compress_level=6, optimize=False)
        payload = buffer.getvalue()
        digest = sha256_bytes(payload)
        destination = directory / f"{prefix}-{digest[:16]}.png"
        if destination.exists():
            return destination.resolve(), digest
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{prefix}.", dir=directory)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, FILE_MODE)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, FILE_MODE)
        except Exception:
            with suppress(OSError):
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise
        return destination.resolve(), digest
