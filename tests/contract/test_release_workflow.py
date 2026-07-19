"""Release workflow and immutable version contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def _release_module():
    script = ROOT / "scripts" / "verify_release_tag.py"
    spec = importlib.util.spec_from_file_location("verify_release_tag", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_tag_must_exactly_match_package_version() -> None:
    module = _release_module()

    module.validate_release_tag("v0.3.0")
    with pytest.raises(ValueError, match=r"release tag must be v0\.3\.0"):
        module.validate_release_tag("v0.2.0")


def test_release_repeats_critical_gates_before_publishing() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    for command in (
        "uv run ruff format --check .",
        "uv run ruff check .",
        "uv run mypy src",
        "--cov=handoff_forge --cov-branch",
        "uv run bandit -q -r src",
        "uv run pip-audit",
        "uv run python scripts/check_public_repo.py",
        "uv run pytest tests/e2e -m e2e -q",
    ):
        assert command in workflow
    for operating_system in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert operating_system in workflow
    assert "needs: [build, compatibility, browser]" in workflow


def test_release_checksums_reference_sibling_assets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "working-directory: dist" in workflow
    assert "sha256sum *.whl *.tar.gz > SHA256SUMS" in workflow
    assert workflow.count("sha256sum --check SHA256SUMS") == 2
    assert "sha256sum dist/*.whl" not in workflow
