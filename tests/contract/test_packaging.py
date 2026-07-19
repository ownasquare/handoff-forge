"""Packaging and container release-boundary contracts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from handoff_forge import __version__

ROOT = Path(__file__).parents[2]


def test_release_version_has_one_package_source() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["project"].get("dynamic") == ["version"]
    assert "version" not in config["project"]
    assert config["tool"]["hatch"]["version"]["path"] == "src/handoff_forge/__init__.py"
    assert __version__ == "0.3.0"
    assert "Operating System :: OS Independent" not in config["project"]["classifiers"]


def test_wheel_bundles_demo_resources_at_installed_lookup_path() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "overrides>=7.7,<8" in config["project"]["dependencies"]
    force_include = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert force_include["examples/handoffs"] == "handoff_forge/resources/handoffs"
    assert (
        force_include["examples/northstar-continuity-review.pdf"]
        == "handoff_forge/resources/northstar-continuity-review.pdf"
    )
    assert "examples" not in force_include
    for relative in (
        "northstar-continuity-review.pdf",
        "handoffs/project-alpha.mdc",
        "handoffs/project-beta.mdc",
    ):
        assert (ROOT / "examples" / relative).is_file()


def test_sdist_excludes_internal_continuation_artifacts() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist_config = config["tool"]["hatch"]["build"]["targets"].get("sdist", {})

    assert {
        "/docs/ai-harness-handoff-system",
        "/docs/handoff-forge",
        "/docs/handoffs",
        "/docs/superpowers",
    } <= set(sdist_config.get("exclude", ()))

    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {
        "docs/ai-harness-handoff-system/",
        "docs/handoff-forge/",
        "docs/handoffs/",
        "docs/superpowers/",
    } <= ignored


def test_compose_uses_non_root_safe_named_persistence() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "USER 10001:10001" in dockerfile
    assert "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false" in dockerfile
    assert "HANDOFF_FORGE_DATA_DIR=/data" in dockerfile
    assert "HANDOFF_FORGE_DATA_DIR: /data" in compose
    assert "- handoff-forge-data:/data" in compose
    assert "\nvolumes:\n  handoff-forge-data:" in compose
    assert "./.data:/data" not in compose


def test_container_uses_locked_install_and_packaged_ui_contract() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY uv.lock" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert dockerfile.count("@sha256:") >= 3
    assert "COPY .streamlit" not in dockerfile


def test_docker_context_excludes_local_credentials_and_private_working_docs() -> None:
    patterns = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert patterns.index(".env*") < patterns.index("!.env.example")
    assert patterns.index("**/.env*") < patterns.index("!**/.env.example")
    assert {
        ".streamlit/secrets.toml",
        "**/.streamlit/secrets.toml",
        "secrets",
        "**/secrets",
        "tmp",
        "**/tmp",
        "docs/ai-harness-handoff-system",
        "docs/handoff-forge",
        "docs/handoffs",
        "docs/superpowers",
    } <= set(patterns)
