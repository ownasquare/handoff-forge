"""Preview-first harness launcher that never invokes a shell."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from pathlib import Path

from handoff_forge.errors import ExternalActionError
from handoff_forge.harnesses.base import (
    LaunchResult,
    ensure_working_directory,
    handoff_prompt,
    resolve_managed_file,
)
from handoff_forge.harnesses.registry import HarnessRegistry, build_default_harness_registry

ExecutableResolver = Callable[[str], str | None]
Executor = Callable[..., object]


class HarnessLauncher:
    """Resolve a known executable and pass one validated argv vector to it."""

    def __init__(
        self,
        *,
        managed_root: Path,
        registry: HarnessRegistry | None = None,
        executable_resolver: ExecutableResolver = shutil.which,
        executor: Executor | None = None,
    ) -> None:
        self.managed_root = managed_root.expanduser().resolve(strict=True)
        self.registry = registry or build_default_harness_registry()
        self._resolve_executable = executable_resolver
        self._executor = executor or subprocess.run

    def _executable(self, candidates: tuple[str, ...]) -> Path:
        executable = self._resolved_executable(candidates)
        if executable is not None:
            return executable
        names = ", ".join(candidates)
        raise ExternalActionError(f"harness executable is not installed: {names}")

    def _resolved_executable(self, candidates: tuple[str, ...]) -> Path | None:
        for candidate in candidates:
            resolved = self._resolve_executable(candidate)
            if resolved is None:
                continue
            try:
                path = Path(resolved).expanduser().resolve(strict=True)
            except OSError:
                continue
            if path.is_file() and os.access(path, os.X_OK):
                return path
        return None

    def available_harnesses(self) -> tuple[str, ...]:
        """Return only registered profiles with a currently executable CLI."""

        return tuple(
            name
            for name in self.registry.names()
            if self._resolved_executable(self.registry.get(name).executable_candidates) is not None
        )

    def launch(
        self,
        harness: str,
        handoff_path: Path,
        *,
        model: str | None = None,
        working_directory: Path | None = None,
        execute: bool = False,
    ) -> LaunchResult:
        managed_handoff = resolve_managed_file(self.managed_root, handoff_path)
        profile = self.registry.get(harness)
        executable = self._executable(profile.executable_candidates)
        cwd = ensure_working_directory(working_directory or managed_handoff.parent)
        prompt = handoff_prompt(managed_handoff)
        argv = profile.build_argv(executable, managed_handoff, model, cwd, prompt)
        if not argv or argv[0] != str(executable):
            raise ExternalActionError("harness profile produced an invalid executable argv")
        forbidden = {"--resume", "--continue", "-r", "-c"}
        if any(
            argument in forbidden
            or argument.startswith("--resume=")
            or argument.startswith("--continue=")
            for argument in argv[1:]
        ):
            raise ExternalActionError("harness profile attempted to resume an existing session")

        if not execute:
            return LaunchResult(harness=profile.name, argv=argv, cwd=cwd)

        try:
            process = self._executor(
                list(argv),
                cwd=str(cwd),
                shell=False,
            )
        except Exception as error:
            raise ExternalActionError(
                f"could not start {profile.name} ({type(error).__name__})"
            ) from None
        pid = getattr(process, "pid", None)
        returncode = getattr(process, "returncode", None)
        if not isinstance(returncode, int):
            raise ExternalActionError(
                f"{profile.name} execution did not report a terminal exit status"
            )
        if returncode != 0:
            raise ExternalActionError(f"{profile.name} exited with status {returncode}")
        return LaunchResult(
            harness=profile.name,
            argv=argv,
            cwd=cwd,
            executed=True,
            pid=pid if isinstance(pid, int) else None,
            returncode=returncode,
        )
