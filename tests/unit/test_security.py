from __future__ import annotations

from pathlib import Path

import pytest

from handoff_forge.errors import StorageError, UnsafeUploadError
from handoff_forge.models import ArtifactKind
from handoff_forge.security import (
    classify_upload,
    confined_path,
    normalize_display_name,
    read_regular_file_bounded,
    redact_secrets,
)


def test_upload_name_is_display_only_and_extension_is_case_insensitive() -> None:
    media_type, kind, display_name = classify_upload(
        "../résumé #1.MDC",
        b"# Safe evidence\n",
    )

    assert media_type == "text/markdown"
    assert kind is ArtifactKind.MDC
    assert display_name == "résumé #1.MDC"
    assert normalize_display_name("folder\\notes.md") == "notes.md"


def test_spoofed_pdf_and_invalid_markdown_are_rejected() -> None:
    with pytest.raises(UnsafeUploadError, match="PDF signature"):
        classify_upload("project.pdf", b"not a pdf")
    with pytest.raises(UnsafeUploadError, match="valid UTF-8"):
        classify_upload("project.md", b"\xff\xfe")


def test_path_containment_rejects_traversal_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(StorageError, match="escapes"):
        confined_path(root, root / ".." / "outside.txt", must_exist=True)

    link = root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(StorageError, match="symbolic links"):
        confined_path(root, link, must_exist=True)


def test_secret_redaction_preserves_context_without_values() -> None:
    canary = " ".join(
        (
            "api_key=" + "sk-" + "example123456789",
            "token:" + " " + "abcdefghijklmnop",
            "Bearer" + " " + "qwertyuiop123456",
        )
    )
    rendered = redact_secrets(canary)

    assert rendered.count("[REDACTED]") == 3
    assert "example123" not in rendered
    assert "abcdefghijklmnop" not in rendered
    assert "qwertyuiop" not in rendered


def test_secret_redaction_handles_quoted_structured_assignments_only() -> None:
    canary = "quoted-" + "credential-canary-123456"
    sensitive_key = "api_" + "key"
    samples = (
        "{" + f'"{sensitive_key}":"{canary}"' + "}",
        "{" + f"'access_token': '{canary}'" + "}",
        f'"client_secret" = "{canary}"',
        f"password: '{canary}'",
    )

    rendered = tuple(redact_secrets(sample) for sample in samples)

    assert all(canary not in sample for sample in rendered)
    assert all("[REDACTED]" in sample for sample in rendered)
    assert redact_secrets("Token budgets and password policies are ordinary prose.") == (
        "Token budgets and password policies are ordinary prose."
    )


def test_bounded_regular_file_read_rejects_symlink_and_oversize(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"x" * 33)
    link = tmp_path / "linked.bin"
    link.symlink_to(oversized)

    with pytest.raises(UnsafeUploadError, match="32-byte limit"):
        read_regular_file_bounded(oversized, max_bytes=32)
    with pytest.raises(StorageError, match="symbolic links"):
        read_regular_file_bounded(link, max_bytes=64)


def test_bounded_regular_file_read_preserves_binary_bytes(tmp_path: Path) -> None:
    payload = b"\x89PNG\r\n\x1a\nfixture\r\nbytes\x00\xff"
    source = tmp_path / "fixture.bin"
    source.write_bytes(payload)

    assert read_regular_file_bounded(source, max_bytes=len(payload)) == payload
