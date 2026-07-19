"""Public package metadata contract."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_project_contract_exposes_cli_and_supported_python() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    dependencies = " ".join(project["dependencies"])
    assert project["requires-python"] == ">=3.11,<3.14"
    assert project["scripts"]["handoff-forge"] == "handoff_forge.cli:app"
    for name in ("streamlit", "chromadb", "llama-index-core", "pdfplumber", "pytesseract"):
        assert name in dependencies


def test_lock_and_container_contracts_exist() -> None:
    for relative in ("uv.lock", "Dockerfile", "compose.yaml", ".streamlit/config.toml"):
        assert (ROOT / relative).is_file(), relative


def test_global_testing_policy_is_not_violated() -> None:
    cypress_files = list(ROOT.rglob("cypress.config.*")) + list(ROOT.rglob("cypress/e2e/*"))
    assert cypress_files == []
    assert (ROOT / "tests/e2e").exists() or not list(ROOT.glob("tests/e2e/*"))
