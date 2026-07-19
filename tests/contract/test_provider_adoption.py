from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_provider_recipes_keep_network_and_upload_consent_as_separate_gates() -> None:
    guide = (ROOT / "docs" / "providers.md").read_text(encoding="utf-8")

    assert "uv sync --no-dev --frozen --extra providers" in guide
    assert "handoff-forge[providers] @ file:///ABSOLUTE/PATH/" in guide
    assert (
        "uv run --no-dev --frozen --extra providers handoff-forge --allow-network doctor" in guide
    )
    assert "--allow-cloud-upload" in guide
    assert "HANDOFF_FORGE_OFFLINE=false" in guide
    assert "HANDOFF_FORGE_ALLOW_NETWORK=true" in guide
    for credential_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "XAI_API_KEY",
    ):
        assert credential_name in guide
        assert f"{credential_name}=" not in guide


def test_provider_container_is_an_explicit_opt_in_build() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    guide = (ROOT / "docs" / "providers.md").read_text(encoding="utf-8")

    assert "ARG HANDOFF_FORGE_INSTALL_PROVIDERS=false" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable --extra providers" in dockerfile
    assert "--build-arg HANDOFF_FORGE_INSTALL_PROVIDERS=true" in guide
    assert "--env OPENAI_API_KEY" in guide
