"""Clipboard, URI, and file-manager actions without shell interpolation."""

from __future__ import annotations

import platform
import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from pathlib import Path

from handoff_forge.errors import ExternalActionError
from handoff_forge.harnesses.base import ActionResult, resolve_managed_file

ExecutableResolver = Callable[[str], str | None]
Runner = Callable[..., object]
ClipboardWriter = Callable[[str], object]


class PlatformActions:
    def __init__(
        self,
        *,
        managed_root: Path,
        platform_name: str | None = None,
        executable_resolver: ExecutableResolver = shutil.which,
        runner: Runner = subprocess.run,
        clipboard_writer: ClipboardWriter | None = None,
    ) -> None:
        self.managed_root = managed_root.expanduser().resolve(strict=True)
        self.platform_name = (platform_name or platform.system()).strip().lower()
        self._resolve_executable = executable_resolver
        self._runner = runner
        self._clipboard_writer = clipboard_writer

    def _file(self, path: Path) -> Path:
        return resolve_managed_file(self.managed_root, path)

    def raw_path(self, path: Path) -> str:
        return str(self._file(path))

    def file_uri(self, path: Path) -> str:
        return self._file(path).as_uri()

    def _clipboard_argv(self) -> tuple[str, ...]:
        if self.platform_name in {"darwin", "mac", "macos"}:
            executable = self._resolve_executable("pbcopy")
            return (executable,) if executable else ()
        if self.platform_name.startswith("win"):
            executable = self._resolve_executable("clip.exe") or self._resolve_executable("clip")
            return (executable,) if executable else ()
        executable = self._resolve_executable("wl-copy")
        if executable:
            return (executable,)
        executable = self._resolve_executable("xclip")
        return (executable, "-selection", "clipboard") if executable else ()

    def copy_path(self, path: Path, *, as_uri: bool = False) -> ActionResult:
        managed = self._file(path)
        payload = managed.as_uri() if as_uri else str(managed)
        if self._clipboard_writer is not None:
            try:
                self._clipboard_writer(payload)
            except Exception as error:
                raise ExternalActionError(
                    f"clipboard adapter failed ({type(error).__name__})"
                ) from None
            return ActionResult(
                action="copy",
                path=managed,
                payload=payload,
                executed=True,
                message="Path copied to the clipboard.",
            )

        argv = self._clipboard_argv()
        if not argv:
            return ActionResult(
                action="copy",
                path=managed,
                payload=payload,
                executed=False,
                message=f"No clipboard tool is available; copy this value: {payload}",
            )
        try:
            completed = self._runner(
                list(argv),
                input=payload,
                text=True,
                capture_output=True,
                check=False,
                shell=False,
            )
        except Exception as error:
            raise ExternalActionError(f"clipboard action failed ({type(error).__name__})") from None
        returncode = getattr(completed, "returncode", 0)
        if returncode != 0:
            raise ExternalActionError(f"clipboard action failed with status {returncode}")
        return ActionResult(
            action="copy",
            path=managed,
            payload=payload,
            executed=True,
            message="Path copied to the clipboard.",
            argv=argv,
            returncode=returncode,
        )

    def _reveal_argv(self, path: Path) -> tuple[str, ...]:
        if self.platform_name in {"darwin", "mac", "macos"}:
            executable = self._resolve_executable("open")
            return (executable, "-R", str(path)) if executable else ()
        if self.platform_name.startswith("win"):
            executable = self._resolve_executable("explorer.exe") or self._resolve_executable(
                "explorer"
            )
            return (executable, f"/select,{path}") if executable else ()
        executable = self._resolve_executable("xdg-open")
        return (executable, str(path.parent)) if executable else ()

    def reveal(self, path: Path) -> ActionResult:
        managed = self._file(path)
        argv = self._reveal_argv(managed)
        payload = str(managed)
        if not argv:
            return ActionResult(
                action="reveal",
                path=managed,
                payload=payload,
                executed=False,
                message=f"No file manager is available; use this path: {payload}",
            )
        try:
            completed = self._runner(
                list(argv),
                text=True,
                capture_output=True,
                check=False,
                shell=False,
            )
        except Exception as error:
            raise ExternalActionError(
                f"file-manager action failed ({type(error).__name__})"
            ) from None
        returncode = getattr(completed, "returncode", 0)
        if returncode != 0:
            raise ExternalActionError(f"file-manager action failed with status {returncode}")
        return ActionResult(
            action="reveal",
            path=managed,
            payload=payload,
            executed=True,
            message="File revealed in the platform file manager.",
            argv=argv,
            returncode=returncode,
        )
