"""Line-faithful Markdown and MDC parsing without remote asset fetching."""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from handoff_forge.config import HandoffSettings
from handoff_forge.errors import ParseError, StorageError, UnsafeUploadError
from handoff_forge.models import (
    ArtifactKind,
    BlockKind,
    ContentBlock,
    DocumentReference,
    ParsedDocument,
    ParseWarning,
    SourceArtifact,
)
from handoff_forge.parsing.base import DocumentParser, stable_block_id
from handoff_forge.security import (
    FILE_MODE,
    classify_upload,
    confined_path,
    ensure_directory,
    read_regular_file_bounded,
    sha256_bytes,
)

yaml: Any = import_module("yaml")

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_TABLE_DIVIDER_CELL = re.compile(r"^:?-{3,}:?$")
_INLINE_REFERENCE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
_INLINE_IMAGE_REFERENCE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)")
_REFERENCE_DEFINITION = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)", flags=re.MULTILINE)
_HTML_REFERENCE = re.compile(r"<(?:img|a)\b[^>]+(?:src|href)=[\"']([^\"']+)", re.IGNORECASE)
_HTML_IMAGE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
_HTML_ALT = re.compile(r"\balt=[\"']([^\"']*)[\"']", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _ImageOccurrence:
    reference: str
    alt_text: str
    line_number: int


class MarkdownParser(DocumentParser):
    """Parse Markdown/MDC into stable source-positioned canonical blocks."""

    parser_profile = "markdown-mdc-v2"

    def __init__(
        self,
        settings: HandoffSettings | None = None,
        *,
        artifact_dir: Path | None = None,
        reference_root: Path | None = None,
    ) -> None:
        super().__init__(settings, artifact_dir=artifact_dir)
        if reference_root is None:
            self.reference_root = None
            return
        raw_reference_root = reference_root.expanduser()
        if raw_reference_root.is_symlink():
            raise ParseError("Markdown reference root cannot be a symlink")
        resolved_reference_root = raw_reference_root.resolve(strict=True)
        if not resolved_reference_root.is_dir():
            raise ParseError("Markdown reference root must be a real directory")
        self.reference_root = resolved_reference_root

    def parse(
        self,
        source: SourceArtifact | Path,
        *,
        project_id: str = "standalone",
    ) -> ParsedDocument:
        artifact = self.coerce_artifact(source, project_id=project_id)
        if artifact.stored_path.suffix.casefold() not in {".md", ".mdc"}:
            raise ParseError("MarkdownParser accepts only .md and .mdc sources")
        raw = artifact.stored_path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParseError("Markdown source must be valid UTF-8") from exc
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if len(text) > self.settings.max_markdown_characters:
            raise ParseError(
                "Markdown source exceeds the "
                f"{self.settings.max_markdown_characters}-character limit"
            )

        lines = text.splitlines()
        frontmatter, body_start, warnings = self._extract_frontmatter(lines)
        blocks = self._parse_blocks(lines, body_start, artifact)
        references, reference_warnings = self._extract_references(artifact, blocks)
        image_blocks, image_warnings = self._image_reference_blocks(
            artifact,
            blocks,
            references,
        )
        blocks.extend(image_blocks)
        warnings.extend(reference_warnings)
        warnings.extend(image_warnings)
        return ParsedDocument(
            artifact=artifact,
            blocks=blocks,
            warnings=warnings,
            frontmatter=frontmatter,
            references=references,
            parser_profile=self.parser_profile,
        )

    def _extract_frontmatter(
        self,
        lines: list[str],
    ) -> tuple[dict[str, object], int, list[ParseWarning]]:
        if not lines or lines[0].strip() != "---":
            return {}, 0, []
        closing = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.strip() in {"---", "..."}
            ),
            None,
        )
        if closing is None:
            return (
                {},
                0,
                [
                    ParseWarning(
                        code="unterminated_frontmatter",
                        message="YAML frontmatter opens with --- but has no closing delimiter.",
                    )
                ],
            )
        payload = "\n".join(lines[1:closing])
        try:
            loaded = yaml.safe_load(payload) if payload.strip() else {}
        except yaml.YAMLError as exc:
            raise ParseError("MDC/Markdown frontmatter is not valid YAML") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ParseError("MDC/Markdown frontmatter must be a YAML mapping")
        return {str(key): value for key, value in loaded.items()}, closing + 1, []

    def _parse_blocks(
        self,
        lines: list[str],
        body_start: int,
        artifact: SourceArtifact,
    ) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        index = body_start
        while index < len(lines):
            if not lines[index].strip():
                index += 1
                continue
            line_start = index + 1
            current = lines[index]
            fence = self._fence_marker(current)
            if fence:
                index += 1
                captured: list[str] = []
                while index < len(lines) and not lines[index].lstrip().startswith(fence):
                    captured.append(lines[index])
                    index += 1
                if index < len(lines):
                    index += 1
                block_text = "\n".join(captured).strip() or "(empty fenced code block)"
                blocks.append(
                    self._block(
                        artifact,
                        BlockKind.CODE,
                        block_text,
                        len(blocks),
                        line_start,
                        index,
                        metadata={
                            "fence": fence,
                            "language": current.strip()[len(fence) :].strip(),
                        },
                    )
                )
                continue

            heading = _HEADING.match(current)
            if heading:
                index += 1
                blocks.append(
                    self._block(
                        artifact,
                        BlockKind.HEADING,
                        heading.group(2).strip(),
                        len(blocks),
                        line_start,
                        index,
                        metadata={"level": len(heading.group(1))},
                    )
                )
                continue

            if self._starts_table(lines, index):
                captured = [current, lines[index + 1]]
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    captured.append(lines[index])
                    index += 1
                blocks.append(
                    self._block(
                        artifact,
                        BlockKind.TABLE,
                        "\n".join(captured),
                        len(blocks),
                        line_start,
                        index,
                    )
                )
                continue

            if _LIST_ITEM.match(current):
                captured = [current]
                index += 1
                while index < len(lines):
                    candidate = lines[index]
                    if _LIST_ITEM.match(candidate) or (
                        candidate.startswith(("  ", "\t")) and candidate.strip()
                    ):
                        captured.append(candidate)
                        index += 1
                        continue
                    break
                blocks.append(
                    self._block(
                        artifact,
                        BlockKind.LIST,
                        "\n".join(captured),
                        len(blocks),
                        line_start,
                        index,
                    )
                )
                continue

            captured = [current]
            index += 1
            while index < len(lines) and lines[index].strip():
                if (
                    self._fence_marker(lines[index])
                    or _HEADING.match(lines[index])
                    or _LIST_ITEM.match(lines[index])
                    or self._starts_table(lines, index)
                ):
                    break
                captured.append(lines[index])
                index += 1
            blocks.append(
                self._block(
                    artifact,
                    BlockKind.TEXT,
                    "\n".join(captured),
                    len(blocks),
                    line_start,
                    index,
                )
            )
        return blocks

    def _block(
        self,
        artifact: SourceArtifact,
        kind: BlockKind,
        text: str,
        order: int,
        line_start: int,
        line_end: int,
        *,
        artifact_path: Path | None = None,
        extraction_method: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ContentBlock:
        location = {"line_start": line_start, "line_end": line_end}
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
            line_start=line_start,
            line_end=line_end,
            artifact_path=artifact_path,
            extraction_method=extraction_method or f"markdown-{kind.value}",
            metadata={
                "parser_version": self.parser_profile,
                "untrusted_evidence": True,
                **(metadata or {}),
            },
        )

    def _extract_references(
        self,
        artifact: SourceArtifact,
        blocks: list[ContentBlock],
    ) -> tuple[list[DocumentReference], list[ParseWarning]]:
        # Fenced code is evidence, but link-looking text inside it is not a
        # document asset dependency.
        searchable = "\n".join(block.text for block in blocks if block.kind != BlockKind.CODE)
        raw_references = [match.group(1) for match in _INLINE_REFERENCE.finditer(searchable)]
        raw_references.extend(
            match.group(1) for match in _REFERENCE_DEFINITION.finditer(searchable)
        )
        raw_references.extend(match.group(1) for match in _HTML_REFERENCE.finditer(searchable))

        references: list[DocumentReference] = []
        warnings: list[ParseWarning] = []
        seen: set[str] = set()
        for raw_reference in raw_references:
            reference = raw_reference.strip().strip("<>")
            if not reference or reference in seen:
                continue
            seen.add(reference)
            split = urlsplit(reference)
            if split.scheme.casefold() in {"http", "https"} or split.netloc:
                references.append(DocumentReference(reference=reference, kind="remote"))
                warnings.append(
                    ParseWarning(
                        code="external_url_not_fetched",
                        message=f"External reference was recorded but not fetched: {reference}",
                    )
                )
                continue
            if reference.startswith("#"):
                references.append(DocumentReference(reference=reference, kind="anchor"))
                continue
            if split.scheme and split.scheme.casefold() not in {"file"}:
                references.append(DocumentReference(reference=reference, kind="remote"))
                warnings.append(
                    ParseWarning(
                        code="unsupported_reference_scheme",
                        message=f"Reference scheme was recorded but not opened: {reference}",
                    )
                )
                continue

            relative_text = unquote(split.path)
            if not relative_text:
                continue
            source_directory = self.reference_root or artifact.stored_path.parent.resolve()
            candidate = source_directory / relative_text
            try:
                resolved = confined_path(source_directory, candidate, must_exist=True)
            except (OSError, StorageError):
                references.append(DocumentReference(reference=reference, kind="missing"))
                warnings.append(
                    ParseWarning(
                        code="missing_or_unsafe_relative_asset",
                        message=(
                            "Relative asset is missing or outside the source directory: "
                            f"{reference}"
                        ),
                    )
                )
                continue
            try:
                payload = read_regular_file_bounded(
                    resolved,
                    max_bytes=self.settings.max_upload_bytes,
                )
            except UnsafeUploadError:
                references.append(DocumentReference(reference=reference, kind="missing"))
                warnings.append(
                    ParseWarning(
                        code="relative_asset_too_large",
                        message=f"Relative asset exceeds the configured byte limit: {reference}",
                    )
                )
                continue
            except StorageError:
                references.append(DocumentReference(reference=reference, kind="missing"))
                warnings.append(
                    ParseWarning(
                        code="unsafe_relative_asset",
                        message=f"Relative asset is not a regular non-symlink file: {reference}",
                    )
                )
                continue
            digest = sha256_bytes(payload)
            preserved = self._preserve_asset(artifact, resolved, payload, digest)
            references.append(
                DocumentReference(
                    reference=reference,
                    kind="local",
                    resolved_path=preserved,
                    artifact_id=f"asset_{digest}",
                )
            )
        return references, warnings

    def _image_reference_blocks(
        self,
        artifact: SourceArtifact,
        blocks: list[ContentBlock],
        references: list[DocumentReference],
    ) -> tuple[list[ContentBlock], list[ParseWarning]]:
        """Promote valid local Markdown images into retrievable visual blocks."""

        local_references = {
            item.reference: item
            for item in references
            if item.kind == "local" and item.resolved_path is not None
        }
        visual_blocks: list[ContentBlock] = []
        warnings: list[ParseWarning] = []
        for occurrence in self._image_occurrences(blocks):
            reference = local_references.get(occurrence.reference)
            if reference is None or reference.resolved_path is None:
                continue
            payload = reference.resolved_path.read_bytes()
            try:
                media_type, kind, _display_name = classify_upload(
                    reference.resolved_path.name,
                    payload,
                )
            except UnsafeUploadError:
                warnings.append(
                    ParseWarning(
                        code="unsupported_local_image_asset",
                        message=(
                            "A local Markdown image was preserved but not indexed as a visual "
                            "because its content did not match a supported image: "
                            f"{occurrence.reference}"
                        ),
                    )
                )
                continue
            if kind is not ArtifactKind.IMAGE:
                continue
            digest = sha256_bytes(payload)
            description = occurrence.alt_text or self._reference_description(occurrence.reference)
            text = f"Referenced Markdown image: {description}."
            visual_blocks.append(
                self._block(
                    artifact,
                    BlockKind.IMAGE,
                    text,
                    len(blocks) + len(visual_blocks),
                    occurrence.line_number,
                    occurrence.line_number,
                    artifact_path=reference.resolved_path,
                    extraction_method="markdown-local-image-reference",
                    metadata={
                        "alt_text": occurrence.alt_text,
                        "source_reference": occurrence.reference,
                        "visual_artifact_id": reference.artifact_id,
                        "visual_artifact_sha256": digest,
                        "visual_media_type": media_type,
                    },
                )
            )
        return visual_blocks, warnings

    @staticmethod
    def _image_occurrences(blocks: list[ContentBlock]) -> list[_ImageOccurrence]:
        occurrences: list[_ImageOccurrence] = []
        seen: set[str] = set()
        for block in blocks:
            if block.kind is BlockKind.CODE:
                continue
            line_start = block.line_start or 1
            for offset, line in enumerate(block.text.splitlines()):
                for match in _INLINE_IMAGE_REFERENCE.finditer(line):
                    reference = match.group(2).strip().strip("<>")
                    if not reference or reference in seen:
                        continue
                    seen.add(reference)
                    occurrences.append(
                        _ImageOccurrence(
                            reference=reference,
                            alt_text=" ".join(match.group(1).split()),
                            line_number=line_start + offset,
                        )
                    )
                for match in _HTML_IMAGE.finditer(line):
                    reference = match.group(1).strip().strip("<>")
                    if not reference or reference in seen:
                        continue
                    seen.add(reference)
                    alt_match = _HTML_ALT.search(match.group(0))
                    occurrences.append(
                        _ImageOccurrence(
                            reference=reference,
                            alt_text=(" ".join(alt_match.group(1).split()) if alt_match else ""),
                            line_number=line_start + offset,
                        )
                    )
        return occurrences

    @staticmethod
    def _reference_description(reference: str) -> str:
        path = Path(unquote(urlsplit(reference).path))
        description = re.sub(r"[-_]+", " ", path.stem).strip()
        return description or "local visual evidence"

    def _preserve_asset(
        self,
        artifact: SourceArtifact,
        source: Path,
        payload: bytes,
        digest: str,
    ) -> Path:
        assets = ensure_directory(self.derived_directory(artifact) / "assets")
        suffix = source.suffix.casefold()
        destination = assets / f"{digest}{suffix}"
        if destination.exists():
            return destination.resolve()
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", dir=assets)
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
        return destination.resolve()

    @staticmethod
    def _fence_marker(line: str) -> str | None:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            return "```"
        if stripped.startswith("~~~"):
            return "~~~"
        return None

    @staticmethod
    def _starts_table(lines: list[str], index: int) -> bool:
        if index + 1 >= len(lines) or "|" not in lines[index] or "|" not in lines[index + 1]:
            return False
        cells = [cell.strip() for cell in lines[index + 1].strip().strip("|").split("|")]
        return bool(cells) and all(_TABLE_DIVIDER_CELL.fullmatch(cell) for cell in cells)
