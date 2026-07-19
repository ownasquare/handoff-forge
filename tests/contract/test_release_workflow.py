"""Release workflow and immutable version contracts."""

from __future__ import annotations

import importlib.util
import re
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

    module.validate_release_tag("v0.4.0")
    with pytest.raises(ValueError, match=r"release tag must be v0\.4\.0"):
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
    assert "attest:\n    needs: [build, compatibility, browser]" in workflow
    assert "uv lock --check" in workflow


def test_release_checksums_reference_sibling_assets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "working-directory: dist" in workflow
    assert "sha256sum *.whl *.tar.gz > SHA256SUMS" in workflow
    assert workflow.count("sha256sum --check SHA256SUMS") == 3
    assert "sha256sum dist/*.whl" not in workflow


def test_release_attests_and_verifies_provenance_before_publishing() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert (
        "attest:\n"
        "    needs: [build, compatibility, browser]\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: read\n"
        "      id-token: write\n"
        "      attestations: write"
    ) in workflow
    attest_action = "actions/attest@f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6 # v4.2.0"
    assert attest_action in workflow
    assert "subject-checksums: dist/SHA256SUMS" in workflow
    assert "artifact-metadata:" not in workflow
    assert (
        "release:\n"
        "    needs: attest\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: write\n"
        "      attestations: read"
    ) in workflow
    assert "for asset in dist/*.whl dist/*.tar.gz; do" in workflow
    verification = 'gh attestation verify "$asset"'
    assert verification in workflow
    assert '--repo "$GITHUB_REPOSITORY"' in workflow
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/release.yml"' in workflow
    assert workflow.index(
        'git merge-base --is-ancestor "$GITHUB_SHA" origin/main'
    ) < workflow.index(attest_action)
    assert workflow.index(verification) < workflow.index('gh release create "$GITHUB_REF_NAME"')


def test_workflow_actions_are_commit_pinned_with_reviewable_version_comments() -> None:
    pattern = re.compile(r"(?:-\s+)?uses:\s+[^@\s]+@(?P<sha>[0-9a-f]{40})\s+#\s+v\S+$")

    for filename in ("ci.yml", "release.yml"):
        workflow = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        uses_lines = [line.strip() for line in workflow.splitlines() if "uses:" in line]
        assert uses_lines
        assert all(pattern.fullmatch(line) for line in uses_lines)

    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci_workflow.count("persist-credentials: false") == 3
