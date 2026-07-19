"""Provider registry and exact per-section routing."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from handoff_forge.errors import CapabilityError
from handoff_forge.models import ContentBlock, GenerationRequest, GenerationResult, ModelRoute
from handoff_forge.providers.anthropic import AnthropicProvider
from handoff_forge.providers.base import (
    ProviderProtocol,
    ProviderStatus,
    validate_capabilities,
)
from handoff_forge.providers.google import GoogleProvider
from handoff_forge.providers.offline import OfflineProvider
from handoff_forge.providers.openai import OpenAIProvider
from handoff_forge.providers.xai import XAIProvider

_ALIASES = {
    "claude": "anthropic",
    "gemini": "google",
    "grok": "xai",
}


class ProviderRegistry:
    """Own provider discovery without importing or initializing cloud SDKs."""

    def __init__(
        self,
        providers: Iterable[ProviderProtocol] = (),
        *,
        network_enabled: bool = False,
    ) -> None:
        self._providers: dict[str, ProviderProtocol] = {}
        self._network_enabled = bool(network_enabled)
        for provider in providers:
            self.register(provider)

    @property
    def network_enabled(self) -> bool:
        """Return the registry-owned global network policy."""

        return self._network_enabled

    def set_network_enabled(self, enabled: bool) -> None:
        """Apply one global network policy to built-in and extension providers."""

        self._network_enabled = bool(enabled)

    def register(self, provider: ProviderProtocol) -> None:
        name = provider.name.strip().lower()
        if not name:
            raise ValueError("provider name cannot be empty")
        if name in self._providers:
            raise ValueError(f"provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> ProviderProtocol:
        requested = name.strip().lower()
        canonical = _ALIASES.get(requested, requested)
        try:
            return self._providers[canonical]
        except KeyError:
            available = ", ".join(sorted(self._providers)) or "none"
            raise CapabilityError(
                f"unknown provider {requested!r}; available providers: {available}"
            ) from None

    def is_remote(self, name: str) -> bool:
        """Report a provider's declared data-boundary classification."""

        return bool(self.get(name).is_remote)

    def statuses(self) -> tuple[ProviderStatus, ...]:
        statuses: list[ProviderStatus] = []
        for name in sorted(self._providers):
            provider = self._providers[name]
            status = provider.status()
            if provider.is_remote and not self._network_enabled:
                status = status.model_copy(
                    update={
                        "enabled": False,
                        "state": "disabled",
                        "reason": "Network access is disabled for this run.",
                    }
                )
            statuses.append(status)
        return tuple(statuses)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        provider = self.get(request.route.provider)
        if provider.is_remote:
            if not self._network_enabled:
                raise CapabilityError(f"{provider.name} network access is disabled")
            if not request.route.allow_cloud_upload:
                raise CapabilityError(
                    f"{provider.name} requires explicit per-run cloud-upload consent"
                )
        status = provider.status()
        if not status.enabled:
            raise CapabilityError(
                f"{provider.name} provider is {status.state}: {status.reason or 'not ready'}"
            )
        validate_capabilities(provider.name, provider.capabilities, request)
        return provider.generate(request)


class ProviderRouter:
    """Create one canonical request for the exact route assigned to a section."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    def generate(
        self,
        *,
        section_id: int,
        route: ModelRoute,
        evidence: list[ContentBlock],
        system_prompt: str = "Use only verified evidence and state unknowns explicitly.",
        user_prompt: str | None = None,
    ) -> GenerationResult:
        request = GenerationRequest(
            section_id=section_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt or f"Draft handoff section {section_id}.",
            evidence=evidence,
            route=route,
        )
        return self.registry.generate(request)


def build_default_registry(
    *,
    network_enabled: bool = False,
    managed_root: Path | None = None,
    timeout_seconds: int = 90,
    max_retries: int = 2,
) -> ProviderRegistry:
    """Build all adapters without importing optional SDK modules."""

    return ProviderRegistry(
        [
            OfflineProvider(),
            OpenAIProvider(
                network_enabled=network_enabled,
                managed_root=managed_root,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            ),
            AnthropicProvider(
                network_enabled=network_enabled,
                managed_root=managed_root,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            ),
            GoogleProvider(
                network_enabled=network_enabled,
                managed_root=managed_root,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            ),
            XAIProvider(
                network_enabled=network_enabled,
                managed_root=managed_root,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            ),
        ],
        network_enabled=network_enabled,
    )
