from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from handoff_forge.errors import ExternalActionError
from handoff_forge.harnesses.platform import PlatformActions


def _managed_file(root: Path, name: str = "handoff # 1.mdc") -> Path:
    path = root / name
    path.write_text("handoff", encoding="utf-8")
    return path


def test_file_uri_is_percent_encoded(tmp_path: Path) -> None:
    managed_file = _managed_file(tmp_path, "handoff # résumé.mdc")
    actions = PlatformActions(managed_root=tmp_path)

    uri = actions.file_uri(managed_file)

    assert uri.startswith("file://")
    assert "%23" in uri and "%20" in uri
    assert actions.raw_path(managed_file) == str(managed_file.resolve())


def test_copy_path_uses_injected_clipboard_without_shell(tmp_path: Path) -> None:
    managed_file = _managed_file(tmp_path)
    copied: list[str] = []
    actions = PlatformActions(managed_root=tmp_path, clipboard_writer=copied.append)

    result = actions.copy_path(managed_file, as_uri=True)

    assert result.executed is True
    assert copied == [managed_file.resolve().as_uri()]
    assert result.shell is False


def test_reveal_uses_platform_argv_and_shell_false(tmp_path: Path) -> None:
    managed_file = _managed_file(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> object:
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    actions = PlatformActions(
        managed_root=tmp_path,
        platform_name="darwin",
        executable_resolver=lambda name: "/usr/bin/open" if name == "open" else None,
        runner=runner,
    )

    result = actions.reveal(managed_file)

    assert result.executed is True
    assert result.argv == ("/usr/bin/open", "-R", str(managed_file.resolve()))
    assert calls[0][1]["shell"] is False


def test_headless_reveal_returns_actionable_path(tmp_path: Path) -> None:
    managed_file = _managed_file(tmp_path)
    actions = PlatformActions(
        managed_root=tmp_path,
        platform_name="linux",
        executable_resolver=lambda _: None,
    )

    result = actions.reveal(managed_file)

    assert result.executed is False
    assert str(managed_file.resolve()) in result.message
    assert "file manager" in result.message


def test_platform_actions_reject_outside_and_symlink_paths(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = _managed_file(tmp_path, "outside.mdc")
    link = managed / "linked.mdc"
    link.symlink_to(outside)
    actions = PlatformActions(managed_root=managed)

    with pytest.raises(ExternalActionError, match="managed root"):
        actions.file_uri(outside)
    with pytest.raises(ExternalActionError, match="symlink"):
        actions.file_uri(link)
