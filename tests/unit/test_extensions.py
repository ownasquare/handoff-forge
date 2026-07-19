from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from handoff_forge.application import build_application
from handoff_forge.config import HandoffSettings
from handoff_forge.errors import CapabilityError
from handoff_forge.extensions import (
    HARNESS_ENTRY_POINT_GROUP,
    PROVIDER_ENTRY_POINT_GROUP,
    describe_extensions,
    discover_extensions,
    load_enabled_extensions,
)
from handoff_forge.harnesses.base import CustomHarnessProfile
from handoff_forge.harnesses.registry import build_default_harness_registry
from handoff_forge.models import (
    GenerationRequest,
    GenerationResult,
    ProviderCapabilities,
)
from handoff_forge.providers.base import ProviderStatus
from handoff_forge.providers.registry import build_default_registry


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: str
    factory: Callable[..., object]
    loaded: bool = False

    def load(self) -> Callable[..., object]:
        self.loaded = True
        return self.factory


@dataclass
class NotesProvider:
    name: str = "notes"
    is_remote: bool = False
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(stability="local")
    )

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
        return GenerationResult(
            text="local notes",
            provider=self.name,
            model=request.route.model,
        )


def _settings(tmp_path: Path) -> HandoffSettings:
    return HandoffSettings(data_root=tmp_path / "data", offline=True, allow_network=False)


def test_discovery_reports_metadata_without_importing_extension_code() -> None:
    entry_point = FakeEntryPoint(
        name="notes-provider",
        group=PROVIDER_ENTRY_POINT_GROUP,
        value="example.provider:create_provider",
        factory=lambda **_: NotesProvider(),
    )

    discovered = discover_extensions(entry_points=[entry_point])

    assert [(item.name, item.kind, item.value) for item in discovered] == [
        ("notes-provider", "provider", "example.provider:create_provider")
    ]
    assert entry_point.loaded is False


def test_extension_statuses_are_import_free_and_flag_ambiguous_names() -> None:
    first = FakeEntryPoint(
        name="notes-provider",
        group=PROVIDER_ENTRY_POINT_GROUP,
        value="example.first:create_provider",
        factory=lambda **_: NotesProvider(),
    )
    second = FakeEntryPoint(
        name="notes-provider",
        group=HARNESS_ENTRY_POINT_GROUP,
        value="example.second:create_harness",
        factory=lambda **_: CustomHarnessProfile(
            name="review",
            executable_candidates=("review-cli",),
            arguments=("--handoff", "{handoff_path}"),
        ),
    )

    statuses = describe_extensions(entry_points=[first, second])

    assert [item.status for item in statuses] == ["ambiguous", "ambiguous"]
    assert all(item.enabled is False for item in statuses)
    assert all(item.reason is not None for item in statuses)
    assert first.loaded is False
    assert second.loaded is False


def test_empty_allowlist_does_not_discover_or_import_installed_extensions(
    tmp_path: Path,
) -> None:
    entry_point = FakeEntryPoint(
        name="notes-provider",
        group=PROVIDER_ENTRY_POINT_GROUP,
        value="example.provider:create_provider",
        factory=lambda **_: NotesProvider(),
    )

    loaded = load_enabled_extensions(
        (),
        settings=_settings(tmp_path),
        managed_root=tmp_path,
        providers=build_default_registry(),
        harnesses=build_default_harness_registry(),
        entry_points=[entry_point],
    )

    assert loaded == ()
    assert entry_point.loaded is False


def test_unknown_extension_fails_closed_without_importing_candidates(tmp_path: Path) -> None:
    entry_point = FakeEntryPoint(
        name="notes-provider",
        group=PROVIDER_ENTRY_POINT_GROUP,
        value="example.provider:create_provider",
        factory=lambda **_: NotesProvider(),
    )

    with pytest.raises(CapabilityError, match="unknown extension"):
        load_enabled_extensions(
            ("missing",),
            settings=_settings(tmp_path),
            managed_root=tmp_path,
            providers=build_default_registry(),
            harnesses=build_default_harness_registry(),
            entry_points=[entry_point],
        )

    assert entry_point.loaded is False


def test_enabled_provider_and_harness_factories_receive_only_safe_context(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    managed_root = tmp_path.resolve()
    calls: list[tuple[str, dict[str, Any]]] = []

    def provider_factory(**kwargs: object) -> NotesProvider:
        calls.append(("provider", kwargs))
        return NotesProvider()

    def harness_factory(**kwargs: object) -> CustomHarnessProfile:
        calls.append(("harness", kwargs))
        return CustomHarnessProfile(
            name="review",
            executable_candidates=("review-cli",),
            arguments=("--handoff", "{handoff_path}"),
        )

    provider_entry = FakeEntryPoint(
        name="notes-provider",
        group=PROVIDER_ENTRY_POINT_GROUP,
        value="example.provider:create_provider",
        factory=provider_factory,
    )
    harness_entry = FakeEntryPoint(
        name="review-harness",
        group=HARNESS_ENTRY_POINT_GROUP,
        value="example.harness:create_harness",
        factory=harness_factory,
    )
    providers = build_default_registry()
    harnesses = build_default_harness_registry()

    enabled = load_enabled_extensions(
        ("notes-provider", "review-harness", "notes-provider"),
        settings=settings,
        managed_root=managed_root,
        providers=providers,
        harnesses=harnesses,
        entry_points=[provider_entry, harness_entry],
    )

    assert enabled == ("notes-provider", "review-harness")
    assert providers.get("notes").name == "notes"
    assert harnesses.get("review").name == "review"
    assert calls == [
        ("provider", {"settings": settings, "managed_root": managed_root}),
        ("harness", {"settings": settings, "managed_root": managed_root}),
    ]


def test_extension_factory_contract_failure_is_sanitized(tmp_path: Path) -> None:
    def broken_factory(**_: object) -> object:
        raise RuntimeError("private provider response")

    entry_point = FakeEntryPoint(
        name="broken",
        group=PROVIDER_ENTRY_POINT_GROUP,
        value="private.module:create",
        factory=broken_factory,
    )

    with pytest.raises(CapabilityError, match="RuntimeError") as captured:
        load_enabled_extensions(
            ("broken",),
            settings=_settings(tmp_path),
            managed_root=tmp_path,
            providers=build_default_registry(),
            harnesses=build_default_harness_registry(),
            entry_points=[entry_point],
        )

    assert "private provider response" not in str(captured.value)


def test_application_factory_reports_and_propagates_the_exact_allowlist(tmp_path: Path) -> None:
    entry_point = FakeEntryPoint(
        name="notes-provider",
        group=PROVIDER_ENTRY_POINT_GROUP,
        value="example.provider:create_provider",
        factory=lambda **_: NotesProvider(),
    )
    application = build_application(
        _settings(tmp_path),
        enabled_extensions=("notes-provider",),
        extension_entry_points=[entry_point],
    )
    invocation: dict[str, object] = {}

    def capture_executor(argv: list[str], **kwargs: object) -> object:
        invocation.update({"argv": argv, **kwargs})
        return SimpleNamespace(pid=42, returncode=0)

    application._ui_executor = capture_executor

    report = application.doctor()
    extensions = application.list_extensions()
    launched = application.launch_ui(port=8765, execute=True)

    assert report["enabled_extensions"] == ["notes-provider"]
    assert report["extensions"] == [
        {
            "name": "notes-provider",
            "kind": "provider",
            "value": "example.provider:create_provider",
            "enabled": True,
            "status": "enabled",
            "reason": None,
        }
    ]
    assert extensions[0].enabled is True
    assert extensions[0].status == "enabled"
    assert application.providers.get("notes").name == "notes"
    environment = invocation["env"]
    assert isinstance(environment, dict)
    assert environment["HANDOFF_FORGE_ENABLED_EXTENSIONS"] == "notes-provider"
    assert launched.executed is True


def test_application_lists_disabled_extension_without_importing_it(tmp_path: Path) -> None:
    entry_point = FakeEntryPoint(
        name="notes-provider",
        group=PROVIDER_ENTRY_POINT_GROUP,
        value="example.provider:create_provider",
        factory=lambda **_: NotesProvider(),
    )

    application = build_application(
        _settings(tmp_path),
        extension_entry_points=[entry_point],
    )

    assert application.list_extensions()[0].status == "available"
    assert application.list_extensions()[0].enabled is False
    assert entry_point.loaded is False
