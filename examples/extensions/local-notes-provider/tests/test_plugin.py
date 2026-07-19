from __future__ import annotations

from pathlib import Path

from handoff_forge_local_notes import create_provider

from handoff_forge.config import HandoffSettings
from handoff_forge.models import GenerationRequest, ModelRoute


def test_local_notes_provider_is_deterministic_and_offline(tmp_path: Path) -> None:
    settings = HandoffSettings(data_root=tmp_path / "data", offline=True, allow_network=False)
    provider = create_provider(settings=settings, managed_root=tmp_path)
    request = GenerationRequest(
        section_id=1,
        system_prompt="Use verified evidence.",
        user_prompt="Draft the project purpose.",
        route=ModelRoute(provider="local-notes", model="deterministic-v1"),
    )

    first = provider.generate(request)
    second = provider.generate(request)

    assert provider.is_remote is False
    assert provider.status().state == "ready"
    assert first == second
    assert first.text == "No verified notes were supplied."
