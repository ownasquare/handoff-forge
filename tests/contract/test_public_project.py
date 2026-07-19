"""Public documentation and release-boundary contract."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PRIVATE_DOC_ROOTS = {
    Path("docs/ai-harness-handoff-system"),
    Path("docs/handoff-forge"),
    Path("docs/handoffs"),
    Path("docs/superpowers"),
}


def _public_scanner_module():
    script = ROOT / "scripts" / "check_public_repo.py"
    spec = importlib.util.spec_from_file_location("check_public_repo", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_required_public_files_exist() -> None:
    required = (
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "SECURITY.md",
        "SUPPORT.md",
        "docs/getting-started.md",
        "docs/architecture.md",
        "docs/schema-profiles.md",
        "docs/providers.md",
        "docs/harness-integrations.md",
        "docs/security.md",
        "docs/operations.md",
        "docs/validation.md",
        "docs/limitations.md",
        "docs/extending.md",
        "docs/troubleshooting.md",
        "examples/README.md",
        ".gitattributes",
        ".dockerignore",
        "scripts/check_public_repo.py",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/workflows/release.yml",
    )
    assert [relative for relative in required if not (ROOT / relative).is_file()] == []


def test_readme_documents_the_honest_release_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
    for phrase in (
        "docker compose",
        "git clone",
        "files",
        "create handoff",
        "start session",
        "local",
        "troubleshooting",
        "extend",
    ):
        assert phrase in readme


def test_sample_guide_keeps_launching_optional() -> None:
    sample = (ROOT / "examples" / "README.md").read_text(encoding="utf-8").casefold()

    for phrase in (
        "checked",
        "download",
        "no destination command-line app is required",
        "optionally",
    ):
        assert phrase in sample


def test_make_validate_describes_its_local_proof_boundary() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").casefold()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8").casefold()

    for phrase in ("browser e2e", "live providers", "containers", "hosted ci"):
        assert phrase in makefile
    assert "broad local code-and-package check" in contributing
    assert "remain separate proof layers" in contributing


def test_relative_markdown_links_resolve() -> None:
    failures: list[str] = []
    markdown_files = sorted(ROOT.rglob("*.md"))
    for markdown_path in markdown_files:
        relative_path = markdown_path.relative_to(ROOT)
        if any(relative_path.is_relative_to(root) for root in PRIVATE_DOC_ROOTS):
            continue
        if any(part in {".venv", "build", "dist", "tmp"} for part in relative_path.parts):
            continue
        text = markdown_path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            clean_target = target.split("#", maxsplit=1)[0].strip("<>")
            if clean_target and not (markdown_path.parent / clean_target).resolve().exists():
                failures.append(f"{markdown_path.relative_to(ROOT)} -> {target}")
    assert failures == []


def test_public_automation_covers_supported_platforms_and_release_assets() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for operating_system in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert operating_system in ci
    for python_version in ('"3.11"', '"3.12"', '"3.13"'):
        assert python_version in ci
    assert "uv build" in release
    assert "gh release" in release


def test_public_scanner_covers_common_text_formats_and_high_confidence_tokens() -> None:
    scanner = _public_scanner_module()

    for filename in (
        "settings.ini",
        "release.sh",
        "client.ts",
        ".env.local",
        "uv.lock",
        "LICENSE",
    ):
        assert scanner._is_text_file(Path(filename))
    samples = (
        "access_token=" + "A" * 24,
        "ghp_" + "a" * 36,
        "sk-" + "a" * 30,
        "AKIA" + "A" * 16,
        "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
    )
    for sample in samples:
        assert any(pattern.search(sample) for _label, pattern in scanner.SECRET_PATTERNS)


def test_public_scanner_fails_when_private_docs_are_tracked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scanner = _public_scanner_module()
    monkeypatch.setattr(
        scanner,
        "_tracked_private_files",
        lambda: [Path("docs/handoffs/private.handoff.mdc")],
    )
    monkeypatch.setattr(scanner, "_public_text_files", lambda: [])

    assert scanner.main() == 1
    assert "private documentation must not be tracked" in capsys.readouterr().out
