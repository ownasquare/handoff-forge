"""Built-in and custom harness argv profiles."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from handoff_forge.errors import CapabilityError
from handoff_forge.harnesses.base import HarnessProfile, HarnessProfileProtocol


def _with_model(prefix: list[str], model: str | None) -> list[str]:
    if model is not None:
        prefix.extend(("--model", model))
    return prefix


def _codex_argv(
    executable: Path,
    _: Path,
    model: str | None,
    cwd: Path,
    prompt: str,
) -> tuple[str, ...]:
    argv = _with_model([str(executable)], model)
    argv.extend(("-C", str(cwd), prompt))
    return tuple(argv)


def _claude_argv(
    executable: Path,
    _: Path,
    model: str | None,
    __: Path,
    prompt: str,
) -> tuple[str, ...]:
    argv = _with_model([str(executable)], model)
    argv.append(prompt)
    return tuple(argv)


def _gemini_argv(
    executable: Path,
    _: Path,
    model: str | None,
    __: Path,
    prompt: str,
) -> tuple[str, ...]:
    argv = _with_model([str(executable)], model)
    argv.append(prompt)
    return tuple(argv)


def _grok_argv(
    executable: Path,
    _: Path,
    model: str | None,
    cwd: Path,
    prompt: str,
) -> tuple[str, ...]:
    argv = _with_model([str(executable)], model)
    argv.extend(("--cwd", str(cwd), prompt))
    return tuple(argv)


_ALIASES = {
    "google": "gemini",
    "xai": "grok",
}


class HarnessRegistry:
    def __init__(self, profiles: Iterable[HarnessProfileProtocol] = ()) -> None:
        self._profiles: dict[str, HarnessProfileProtocol] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: HarnessProfileProtocol) -> None:
        if profile.name in self._profiles:
            raise CapabilityError(f"harness profile already registered: {profile.name}")
        self._profiles[profile.name] = profile

    def get(self, name: str) -> HarnessProfileProtocol:
        requested = name.strip().lower()
        canonical = _ALIASES.get(requested, requested)
        try:
            return self._profiles[canonical]
        except KeyError:
            available = ", ".join(sorted(self._profiles)) or "none"
            raise CapabilityError(
                f"unknown harness {requested!r}; available harnesses: {available}"
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


def build_default_harness_registry() -> HarnessRegistry:
    return HarnessRegistry(
        [
            HarnessProfile("codex", ("codex",), _codex_argv),
            HarnessProfile("claude", ("claude",), _claude_argv),
            HarnessProfile("gemini", ("gemini",), _gemini_argv),
            HarnessProfile("grok", ("grok",), _grok_argv),
        ]
    )
