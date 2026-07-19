"""Shared parser contracts and provenance helpers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from handoff_forge.config import HandoffSettings
from handoff_forge.errors import ParseError
from handoff_forge.models import ParsedDocument, SourceArtifact
from handoff_forge.security import (
    classify_upload,
    ensure_directory,
    safe_file_uri,
    sha256_bytes,
)


class DocumentParser(ABC):
    """Provider-neutral parser boundary."""

    parser_profile = "base-v1"

    def __init__(
        self,
        settings: HandoffSettings | None = None,
        *,
        artifact_dir: Path | None = None,
    ) -> None:
        self.settings = settings or HandoffSettings()
        self.artifact_dir = artifact_dir.expanduser().resolve() if artifact_dir else None

    @abstractmethod
    def parse(
        self,
        source: SourceArtifact | Path,
        *,
        project_id: str = "standalone",
    ) -> ParsedDocument:
        """Parse *source* into canonical blocks."""

    def coerce_artifact(
        self,
        source: SourceArtifact | Path,
        *,
        project_id: str,
    ) -> SourceArtifact:
        if isinstance(source, SourceArtifact):
            path = source.stored_path.expanduser().resolve()
            if not path.is_file() or path.is_symlink():
                raise ParseError(f"source artifact is not a regular file: {source.display_name}")
            return source.model_copy(update={"stored_path": path, "file_uri": path.as_uri()})
        path = Path(source).expanduser().resolve()
        if path.is_symlink() or not path.is_file():
            raise ParseError(f"source path is not a regular file: {path}")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ParseError(f"could not read source: {path.name}") from exc
        if len(payload) > self.settings.max_upload_bytes:
            raise ParseError(f"source exceeds the {self.settings.max_upload_bytes}-byte limit")
        try:
            media_type, kind, display_name = classify_upload(path.name, payload)
        except Exception as exc:
            if isinstance(exc, ParseError):
                raise
            raise ParseError(str(exc)) from exc
        digest = sha256_bytes(payload)
        return SourceArtifact(
            id=f"art_{digest[:24]}",
            project_id=project_id,
            display_name=display_name,
            sha256=digest,
            media_type=media_type,
            size_bytes=len(payload),
            kind=kind,
            stored_path=path,
            file_uri=safe_file_uri(path.parent, path),
            metadata={
                "untrusted_evidence": True,
                "instruction_boundary": "content_only_never_control_text",
                "ephemeral_manifest": True,
            },
        )

    def derived_directory(self, artifact: SourceArtifact) -> Path:
        if self.artifact_dir is not None:
            root = self.artifact_dir / artifact.sha256
        elif artifact.stored_path.parent.name == "originals":
            root = artifact.stored_path.parent.parent / "derived" / artifact.id
        else:
            root = artifact.stored_path.parent / f".{artifact.stored_path.stem}-derived"
        return ensure_directory(root)


def stable_block_id(
    artifact: SourceArtifact,
    *,
    kind: str,
    order: int,
    text: str,
    location: dict[str, Any] | None = None,
) -> str:
    """Create a stable block ID from source identity, content, and location."""

    payload = {
        "artifact_sha256": artifact.sha256,
        "kind": kind,
        "order": order,
        "text": text,
        "location": location or {},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"blk_{sha256_bytes(canonical.encode('utf-8'))}"


def normalized_bbox(
    bbox: tuple[float, float, float, float] | None,
    *,
    width: float,
    height: float,
) -> tuple[float, float, float, float] | None:
    """Convert a PDF-space bounding box to a clamped 0..1 tuple."""

    if bbox is None or width <= 0 or height <= 0:
        return None
    x0, top, x1, bottom = bbox
    values = (x0 / width, top / height, x1 / width, bottom / height)
    return tuple(max(0.0, min(1.0, float(value))) for value in values)  # type: ignore[return-value]
