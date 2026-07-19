from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from handoff_forge.errors import CapabilityError
from handoff_forge.models import (
    BlockKind,
    ContentBlock,
    GenerationRequest,
    GenerationResult,
    ModelRoute,
    ProviderCapabilities,
)
from handoff_forge.providers.base import ProviderStatus
from handoff_forge.providers.registry import (
    ProviderRegistry,
    ProviderRouter,
    build_default_registry,
)


@dataclass
class RecordingProvider:
    name: str = "recording"
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(stability="local")
    )
    is_remote: bool = False
    calls: list[GenerationRequest] = field(default_factory=list)

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            installed=True,
            configured=True,
            enabled=True,
            state="ready",
            capabilities=self.capabilities,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        return GenerationResult(
            text=f"section {request.section_id}",
            provider=self.name,
            model=request.route.model,
        )


def _image_block(tmp_path: Path) -> ContentBlock:
    image = tmp_path / "page.png"
    image.write_bytes(b"not-read-by-the-test")
    return ContentBlock(
        id="block-image",
        project_id="project",
        artifact_id="artifact",
        artifact_sha256="a" * 64,
        kind=BlockKind.IMAGE,
        text="A chart image",
        order=1,
        page_number=1,
        artifact_path=image,
        extraction_method="fixture",
    )


def test_default_registry_is_offline_and_does_not_enable_cloud() -> None:
    registry = build_default_registry(network_enabled=False)

    statuses = {status.name: status for status in registry.statuses()}

    assert statuses["offline"].state == "ready"
    assert statuses["offline"].capabilities.stability == "local"
    assert {"openai", "anthropic", "google", "xai"}.issubset(statuses)
    assert all(not statuses[name].enabled for name in ("openai", "anthropic", "google", "xai"))


def test_each_section_uses_its_exact_route() -> None:
    provider = RecordingProvider()
    registry = ProviderRegistry([provider])
    router = ProviderRouter(registry)

    for section_id in range(1, 13):
        route = ModelRoute(provider="recording", model=f"model-{section_id}")
        result = router.generate(section_id=section_id, route=route, evidence=[])
        assert result.model == f"model-{section_id}"

    assert [(call.section_id, call.route.model) for call in provider.calls] == [
        (section_id, f"model-{section_id}") for section_id in range(1, 13)
    ]


def test_vision_route_is_rejected_before_provider_call(tmp_path: Path) -> None:
    provider = RecordingProvider()
    registry = ProviderRegistry([provider])
    router = ProviderRouter(registry)

    with pytest.raises(CapabilityError, match="image_input"):
        router.generate(
            section_id=2,
            route=ModelRoute(
                provider="recording",
                model="text-only",
                include_visual_evidence=True,
            ),
            evidence=[_image_block(tmp_path)],
        )

    assert provider.calls == []


def test_remote_provider_requires_per_run_cloud_consent() -> None:
    provider = RecordingProvider(name="remote", is_remote=True)
    registry = ProviderRegistry([provider], network_enabled=True)
    request = GenerationRequest(
        section_id=1,
        system_prompt="System",
        user_prompt="User",
        route=ModelRoute(provider="remote", model="remote-model"),
    )

    with pytest.raises(CapabilityError, match="cloud-upload consent"):
        registry.generate(request)

    assert provider.calls == []


def test_global_network_policy_blocks_ready_extension_provider() -> None:
    provider = RecordingProvider(name="remote-extension", is_remote=True)
    registry = ProviderRegistry([provider], network_enabled=False)
    request = GenerationRequest(
        section_id=1,
        system_prompt="System",
        user_prompt="User",
        route=ModelRoute(
            provider="remote-extension",
            model="remote-model",
            allow_cloud_upload=True,
        ),
    )

    status = next(item for item in registry.statuses() if item.name == provider.name)
    assert status.enabled is False
    assert status.state == "disabled"
    assert registry.is_remote(provider.name) is True
    with pytest.raises(CapabilityError, match="network access is disabled"):
        registry.generate(request)

    assert provider.calls == []


def test_provider_aliases_resolve_to_canonical_adapters() -> None:
    registry = build_default_registry(network_enabled=False)

    assert registry.get("claude").name == "anthropic"
    assert registry.get("gemini").name == "google"
    assert registry.get("grok").name == "xai"
