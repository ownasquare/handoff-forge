"""Security primitives for local artifact ingestion and persistence.

The module deliberately keeps uploaded names as display-only metadata. Durable
paths are derived from generated identifiers and content hashes, never from
untrusted filenames.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from handoff_forge.errors import StorageError, UnsafeUploadError
from handoff_forge.models import ArtifactKind, ParsedDocument

DIRECTORY_MODE: Final = 0o700
FILE_MODE: Final = 0o600

_MEDIA_BY_SUFFIX: Final[dict[str, tuple[str, ArtifactKind]]] = {
    ".md": ("text/markdown", ArtifactKind.MARKDOWN),
    ".mdc": ("text/markdown", ArtifactKind.MDC),
    ".pdf": ("application/pdf", ArtifactKind.PDF),
    ".png": ("image/png", ArtifactKind.IMAGE),
    ".jpg": ("image/jpeg", ArtifactKind.IMAGE),
    ".jpeg": ("image/jpeg", ArtifactKind.IMAGE),
    ".webp": ("image/webp", ArtifactKind.IMAGE),
}

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?P<prefix>"
    r"(?<![A-Za-z0-9_-])"
    r"[\"']?"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|client[_-]?secret|password|secret)"
    r"[\"']?"
    r"\s*[:=]\s*"
    r")"
    r"(?:"
    r"\"(?P<double_value>[^\"\r\n]{6,})\""
    r"|'(?P<single_value>[^'\r\n]{6,})'"
    r"|(?P<bare_value>[^\s\"',;}{\[\]]{6,})"
    r")"
)
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_PROVIDER_SECRET = re.compile(
    r"\b(?:sk|xai|voyage|AIza)[-_][A-Za-z0-9_-]{12,}\b",
    flags=re.IGNORECASE,
)
_SENSITIVE_KEY = re.compile(
    r"^(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|client[_-]?secret|password|secret)$",
    flags=re.IGNORECASE,
)


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 digest for *content*."""

    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    """Hash a regular, non-symlink file without reading it all into memory."""

    reject_symlink(path)
    if not path.is_file():
        raise StorageError(f"not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_regular_file_bounded(
    path: Path,
    *,
    max_bytes: int,
    chunk_bytes: int = 1024 * 1024,
) -> bytes:
    """Read one regular file without following its final symlink and enforce a hard limit.

    The inode is compared before and after ``open`` so a concurrent replacement fails closed.
    ``fstat`` rejects devices, FIFOs, and directories, while the ``max_bytes + 1`` read cap also
    catches a regular file that grows after the initial size check.
    """

    if max_bytes < 0 or chunk_bytes < 1:
        raise ValueError("file read limits must be non-negative with a positive chunk size")
    try:
        before = path.lstat()
    except OSError as exc:
        raise StorageError(f"could not inspect file: {path.name}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise StorageError(f"symbolic links are not allowed: {path}")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise StorageError(f"file changed while it was being opened: {path.name}")
        if not stat.S_ISREG(opened.st_mode):
            raise StorageError(f"not a regular file: {path}")
        if opened.st_size > max_bytes:
            raise UnsafeUploadError(f"file exceeds the {max_bytes}-byte limit")

        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(descriptor, min(chunk_bytes, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            raise UnsafeUploadError(f"file exceeds the {max_bytes}-byte limit")
        return b"".join(chunks)
    except (StorageError, UnsafeUploadError):
        raise
    except OSError as exc:
        raise StorageError(f"could not safely read file: {path.name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def normalize_display_name(filename: str) -> str:
    """Normalize an upload name for display without using it as a path."""

    if not isinstance(filename, str):
        raise UnsafeUploadError("upload filename must be text")
    normalized = unicodedata.normalize("NFKC", filename).replace("\\", "/")
    normalized = normalized.rsplit("/", maxsplit=1)[-1]
    normalized = _CONTROL_CHARACTERS.sub("", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    if normalized in {"", ".", ".."}:
        raise UnsafeUploadError("upload filename is empty after normalization")
    if len(normalized) > 255:
        suffix = Path(normalized).suffix
        normalized = f"{normalized[: 255 - len(suffix)]}{suffix}"
    return normalized


def classify_upload(filename: str, content: bytes) -> tuple[str, ArtifactKind, str]:
    """Validate an upload extension and signature.

    Returns ``(media_type, artifact_kind, normalized_display_name)``. A file's
    extension is treated only as a claim and is checked against its signature
    for binary formats.
    """

    display_name = normalize_display_name(filename)
    suffix = Path(display_name).suffix.casefold()
    if suffix not in _MEDIA_BY_SUFFIX:
        allowed = ", ".join(sorted(_MEDIA_BY_SUFFIX))
        raise UnsafeUploadError(
            f"unsupported upload extension {suffix or '(none)'}; allowed: {allowed}"
        )
    media_type, kind = _MEDIA_BY_SUFFIX[suffix]
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise UnsafeUploadError("PDF signature does not match the .pdf extension")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise UnsafeUploadError("PNG signature does not match the filename")
    if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise UnsafeUploadError("JPEG signature does not match the filename")
    if suffix == ".webp" and not (
        len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    ):
        raise UnsafeUploadError("WebP signature does not match the filename")
    if suffix in {".md", ".mdc"}:
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UnsafeUploadError("Markdown uploads must be valid UTF-8") from exc
    return media_type, kind, display_name


def detect_media_type(path: Path) -> str:
    """Return a conservative media type for an already validated local path."""

    suffix = path.suffix.casefold()
    configured = _MEDIA_BY_SUFFIX.get(suffix)
    if configured:
        return configured[0]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def reject_symlink(path: Path) -> None:
    """Reject a symlink even when it ultimately points to a regular file."""

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise StorageError(f"symbolic links are not allowed: {path}")


def confined_path(root: Path, candidate: Path, *, must_exist: bool = False) -> Path:
    """Resolve *candidate* and prove it remains below *root*.

    Existing path components are checked for symlinks, so containment is not
    silently granted through a link that happens to resolve inside the root.
    """

    resolved_root = root.expanduser().resolve()
    raw_candidate = candidate if candidate.is_absolute() else resolved_root / candidate

    # Inspect the lexical path before resolution. ``..`` is handled by the
    # containment check below, while existing symlink components fail closed.
    current = Path(raw_candidate.anchor) if raw_candidate.is_absolute() else Path()
    for part in raw_candidate.parts[1:] if raw_candidate.is_absolute() else raw_candidate.parts:
        current /= part
        reject_symlink(current)

    resolved = raw_candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(resolved_root):
        raise StorageError(f"path escapes the configured data root: {candidate}")
    if must_exist and not resolved.exists():
        raise StorageError(f"path does not exist: {candidate}")
    return resolved


def ensure_directory(path: Path) -> Path:
    """Create a private directory and enforce mode ``0700``."""

    reject_symlink(path)
    path.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    reject_symlink(path)
    os.chmod(path, DIRECTORY_MODE)
    return path


def enforce_private_file(path: Path) -> None:
    """Enforce mode ``0600`` on a regular, non-symlink file."""

    reject_symlink(path)
    if not path.is_file():
        raise StorageError(f"expected a regular file: {path}")
    os.chmod(path, FILE_MODE)


def redact_secrets(value: str) -> str:
    """Redact common credential shapes from user-visible persisted text."""

    def redact_assignment(match: re.Match[str]) -> str:
        if match.group("double_value") is not None:
            redacted_value = '"[REDACTED]"'
        elif match.group("single_value") is not None:
            redacted_value = "'[REDACTED]'"
        else:
            redacted_value = "[REDACTED]"
        return f"{match.group('prefix')}{redacted_value}"

    redacted = _SECRET_ASSIGNMENT.sub(redact_assignment, value)
    redacted = _BEARER_SECRET.sub("Bearer [REDACTED]", redacted)
    return _PROVIDER_SECRET.sub("[REDACTED]", redacted)


def sanitize_parsed_document(document: ParsedDocument) -> ParsedDocument:
    """Return one canonical redacted model for both persistence and retrieval.

    Immutable source bytes remain untouched. Every JSON-shaped string in the derived parsed
    model is sanitized before either the canonical JSON writer or vector index can observe it.
    """

    payload = sanitize_json_value(document.model_dump(mode="json"))
    return ParsedDocument.model_validate(payload)


def sanitize_json_value(value: Any) -> Any:
    """Recursively redact strings and values stored below sensitive mapping keys."""

    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = redact_secrets(str(key))
            sanitized[key_text] = (
                "[REDACTED]"
                if _SENSITIVE_KEY.fullmatch(str(key).strip()) and item is not None
                else sanitize_json_value(item)
            )
        return sanitized
    if isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json_value(item) for item in value]
    return value


def safe_file_uri(root: Path, path: Path) -> str:
    """Return a Unicode-safe file URI only after containment validation."""

    return confined_path(root, path, must_exist=True).as_uri()
