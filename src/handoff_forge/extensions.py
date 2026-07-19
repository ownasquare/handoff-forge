"""Explicit, allowlisted extension discovery for trusted installed Python packages.

Entry-point metadata is safe to inspect without importing extension modules. Extension
code is loaded only after an operator names it explicitly through the CLI or application
factory. Installed extensions are trusted local code and run with the application's
permissions once enabled.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from handoff_forge.config import HandoffSettings
from handoff_forge.errors import CapabilityError
from handoff_forge.harnesses.base import HarnessProfileProtocol
from handoff_forge.harnesses.registry import HarnessRegistry
from handoff_forge.models import (
    GenerationRequest,
    GenerationResult,
    ProviderCapabilities,
)
from handoff_forge.providers.base import ProviderProtocol, ProviderStatus, validate_capabilities
from handoff_forge.providers.registry import ProviderRegistry

PROVIDER_ENTRY_POINT_GROUP = "handoff_forge.providers"
HARNESS_ENTRY_POINT_GROUP = "handoff_forge.harnesses"
SUPPORTED_ENTRY_POINT_GROUPS = (
    PROVIDER_ENTRY_POINT_GROUP,
    HARNESS_ENTRY_POINT_GROUP,
)

_EXTENSION_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}")


class ExtensionEntryPoint(Protocol):
    """The importlib.metadata surface used without importing extension code."""

    name: str
    group: str
    value: str

    def load(self) -> Any: ...


class ProviderExtensionFactory(Protocol):
    def __call__(
        self,
        *,
        settings: HandoffSettings,
        managed_root: Path,
    ) -> ProviderProtocol: ...


class HarnessExtensionFactory(Protocol):
    def __call__(
        self,
        *,
        settings: HandoffSettings,
        managed_root: Path,
    ) -> HarnessProfileProtocol: ...


@dataclass(frozen=True, slots=True)
class ExtensionMetadata:
    """Import-free metadata suitable for diagnostics and documentation tooling."""

    name: str
    kind: Literal["provider", "harness"]
    value: str


@dataclass(frozen=True, slots=True)
class ExtensionInfo:
    """Operator-facing extension state assembled without importing extension code."""

    name: str
    kind: Literal["provider", "harness"]
    value: str
    enabled: bool
    status: Literal["available", "enabled", "ambiguous"]
    reason: str | None = None


def normalize_extension_names(names: Iterable[str]) -> tuple[str, ...]:
    """Validate and de-duplicate an operator-provided extension allowlist."""

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = raw_name.strip().casefold()
        if not _EXTENSION_NAME.fullmatch(name):
            raise CapabilityError(
                "extension names must start with a letter and use only letters, numbers, "
                "'.', '_', or '-'"
            )
        if name not in seen:
            normalized.append(name)
            seen.add(name)
    return tuple(normalized)


def discover_extensions(
    *,
    entry_points: Iterable[ExtensionEntryPoint] | None = None,
) -> tuple[ExtensionMetadata, ...]:
    """Return supported entry-point metadata without calling ``EntryPoint.load``."""

    discovered: list[ExtensionMetadata] = []
    for entry_point in _supported_entry_points(entry_points):
        name = _supported_metadata_name(entry_point.name)
        if name is None:
            continue
        discovered.append(
            ExtensionMetadata(
                name=name,
                kind="provider" if entry_point.group == PROVIDER_ENTRY_POINT_GROUP else "harness",
                value=entry_point.value,
            )
        )
    return tuple(sorted(discovered, key=lambda item: (item.name, item.kind, item.value)))


def describe_extensions(
    enabled_names: Iterable[str] = (),
    *,
    entry_points: Iterable[ExtensionEntryPoint] | None = None,
    metadata_items: Iterable[ExtensionMetadata] | None = None,
) -> tuple[ExtensionInfo, ...]:
    """Return import-free installed extension metadata and allowlist state.

    ``metadata_items`` lets an application reuse a previously discovered snapshot. It is
    mutually exclusive with ``entry_points`` so callers cannot accidentally mix two package
    environments into one status view.
    """

    if entry_points is not None and metadata_items is not None:
        raise ValueError("entry_points and metadata_items are mutually exclusive")
    enabled = set(normalize_extension_names(enabled_names))
    discovered = (
        tuple(metadata_items)
        if metadata_items is not None
        else discover_extensions(entry_points=entry_points)
    )
    counts: dict[str, int] = {}
    for item in discovered:
        counts[item.name] = counts.get(item.name, 0) + 1

    described: list[ExtensionInfo] = []
    for item in discovered:
        ambiguous = counts[item.name] > 1
        is_enabled = item.name in enabled and not ambiguous
        described.append(
            ExtensionInfo(
                name=item.name,
                kind=item.kind,
                value=item.value,
                enabled=is_enabled,
                status="ambiguous" if ambiguous else ("enabled" if is_enabled else "available"),
                reason=(
                    "Multiple installed entry points use this name; rename one before enabling."
                    if ambiguous
                    else None
                ),
            )
        )
    return tuple(described)


def load_enabled_extensions(
    enabled_names: Iterable[str],
    *,
    settings: HandoffSettings,
    managed_root: Path,
    providers: ProviderRegistry,
    harnesses: HarnessRegistry,
    entry_points: Iterable[ExtensionEntryPoint] | None = None,
) -> tuple[str, ...]:
    """Load only explicitly allowlisted provider and harness entry points.

    Factories receive exactly two keyword arguments: the runtime settings model and the
    resolved managed data root. Provider credentials remain ambient SDK concerns;
    Handoff Forge never passes credential values into an extension factory.
    """

    enabled = normalize_extension_names(enabled_names)
    if not enabled:
        return ()

    candidates: dict[str, ExtensionEntryPoint] = {}
    for entry_point in _supported_entry_points(entry_points):
        name = _supported_metadata_name(entry_point.name)
        if name is None:
            continue
        if name in candidates:
            raise CapabilityError(f"extension name is ambiguous across installed packages: {name}")
        candidates[name] = entry_point

    missing = [name for name in enabled if name not in candidates]
    if missing:
        available = ", ".join(sorted(candidates)) or "none"
        raise CapabilityError(
            f"unknown extension {missing[0]!r}; available extensions: {available}"
        )

    root = managed_root.expanduser().resolve(strict=True)
    for name in enabled:
        entry_point = candidates[name]
        try:
            factory = entry_point.load()
        except Exception as error:
            raise CapabilityError(
                f"extension {name!r} could not be imported ({type(error).__name__})"
            ) from None
        if not callable(factory):
            raise CapabilityError(f"extension {name!r} entry point is not a factory")
        try:
            extension = cast(Callable[..., object], factory)(settings=settings, managed_root=root)
        except Exception as error:
            raise CapabilityError(
                f"extension {name!r} factory failed ({type(error).__name__})"
            ) from None

        if entry_point.group == PROVIDER_ENTRY_POINT_GROUP:
            if not _is_provider(extension):
                raise CapabilityError(
                    f"extension {name!r} did not return a provider-compatible object"
                )
            try:
                providers.register(cast(ProviderProtocol, extension))
            except Exception as error:
                raise CapabilityError(
                    f"extension {name!r} provider registration failed ({type(error).__name__})"
                ) from None
        else:
            if not _is_harness_profile(extension):
                raise CapabilityError(
                    f"extension {name!r} did not return a harness-profile-compatible object"
                )
            try:
                harnesses.register(cast(HarnessProfileProtocol, extension))
            except Exception as error:
                raise CapabilityError(
                    f"extension {name!r} harness registration failed ({type(error).__name__})"
                ) from None
    return enabled


def _supported_entry_points(
    supplied: Iterable[ExtensionEntryPoint] | None,
) -> tuple[ExtensionEntryPoint, ...]:
    if supplied is None:
        installed = metadata.entry_points()
        selected: list[ExtensionEntryPoint] = []
        for group in SUPPORTED_ENTRY_POINT_GROUPS:
            selected.extend(cast(Iterable[ExtensionEntryPoint], installed.select(group=group)))
        return tuple(selected)
    return tuple(
        entry_point for entry_point in supplied if entry_point.group in SUPPORTED_ENTRY_POINT_GROUPS
    )


def _supported_metadata_name(raw_name: str) -> str | None:
    name = raw_name.strip().casefold()
    return name if _EXTENSION_NAME.fullmatch(name) else None


def _is_provider(value: object) -> bool:
    return (
        isinstance(getattr(value, "name", None), str)
        and isinstance(getattr(value, "is_remote", None), bool)
        and isinstance(getattr(value, "capabilities", None), ProviderCapabilities)
        and callable(getattr(value, "status", None))
        and callable(getattr(value, "generate", None))
    )


def _is_harness_profile(value: object) -> bool:
    candidates = getattr(value, "executable_candidates", None)
    return (
        isinstance(getattr(value, "name", None), str)
        and isinstance(candidates, tuple)
        and bool(candidates)
        and all(isinstance(candidate, str) for candidate in candidates)
        and callable(getattr(value, "build_argv", None))
    )


__all__ = [
    "HARNESS_ENTRY_POINT_GROUP",
    "PROVIDER_ENTRY_POINT_GROUP",
    "ExtensionInfo",
    "ExtensionMetadata",
    "GenerationRequest",
    "GenerationResult",
    "HarnessExtensionFactory",
    "ProviderCapabilities",
    "ProviderExtensionFactory",
    "ProviderStatus",
    "describe_extensions",
    "discover_extensions",
    "load_enabled_extensions",
    "normalize_extension_names",
    "validate_capabilities",
]
