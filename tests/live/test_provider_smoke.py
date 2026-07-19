from __future__ import annotations

import os

import pytest

from handoff_forge.models import GenerationRequest, ModelRoute
from handoff_forge.providers.registry import build_default_registry

pytestmark = pytest.mark.live


def test_explicitly_selected_live_provider() -> None:
    provider_name = os.environ.get("HANDOFF_FORGE_LIVE_PROVIDER")
    model = os.environ.get("HANDOFF_FORGE_LIVE_MODEL")
    if not provider_name or not model:
        pytest.skip("set HANDOFF_FORGE_LIVE_PROVIDER and HANDOFF_FORGE_LIVE_MODEL to opt in")

    registry = build_default_registry(network_enabled=True)
    result = registry.generate(
        GenerationRequest(
            section_id=1,
            system_prompt="Return a concise response.",
            user_prompt="Reply with the word ready.",
            route=ModelRoute(
                provider=provider_name,
                model=model,
                allow_cloud_upload=True,
                max_output_tokens=64,
            ),
        )
    )

    assert result.text.strip()
    assert result.provider == registry.get(provider_name).name
