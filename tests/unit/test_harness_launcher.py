from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from handoff_forge.errors import CapabilityError, ExternalActionError
from handoff_forge.harnesses.base import CustomHarnessProfile
from handoff_forge.harnesses.launcher import HarnessLauncher
from handoff_forge.harnesses.registry import HarnessRegistry, build_default_harness_registry


def _handoff(root: Path, name: str = "handoff.mdc") -> Path:
    path = root / name
    path.write_text("# Handoff\n", encoding="utf-8")
    return path


def test_codex_launch_is_a_new_session_without_shell(tmp_path: Path) -> None:
    executable = tmp_path / "codex"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    handoff = _handoff(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def executor(argv: list[str], **kwargs: object) -> object:
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=None, returncode=0)

    launcher = HarnessLauncher(
        managed_root=tmp_path,
        executable_resolver=lambda name: str(executable) if name == "codex" else None,
        executor=executor,
    )

    result = launcher.launch(
        "codex",
        handoff,
        model="gpt-5",
        working_directory=tmp_path,
        execute=True,
    )

    assert result.argv[0] == str(executable)
    assert "--resume" not in result.argv
    assert "--continue" not in result.argv
    assert result.shell is False
    assert str(handoff) in " ".join(result.argv)
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == str(tmp_path.resolve())
    assert "start_new_session" not in calls[0][1]
    assert result.returncode == 0


def test_preview_is_default_and_does_not_execute(tmp_path: Path) -> None:
    executable = tmp_path / "claude"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    handoff = _handoff(tmp_path)
    called = False

    def executor(_: list[str], **__: object) -> object:
        nonlocal called
        called = True
        return SimpleNamespace(pid=1)

    launcher = HarnessLauncher(
        managed_root=tmp_path,
        executable_resolver=lambda _: str(executable),
        executor=executor,
    )

    result = launcher.launch("claude", handoff, model="sonnet")

    assert result.executed is False
    assert called is False


def test_available_harnesses_returns_only_profiles_with_installed_executables(
    tmp_path: Path,
) -> None:
    launcher = HarnessLauncher(
        managed_root=tmp_path,
        executable_resolver=lambda name: sys.executable if name == "codex" else None,
    )

    assert launcher.available_harnesses() == ("codex",)


def test_metacharacter_filename_remains_data_in_one_argv_item(tmp_path: Path) -> None:
    executable = tmp_path / "grok"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    handoff = _handoff(tmp_path, "handoff ; touch never.mdc")
    launcher = HarnessLauncher(
        managed_root=tmp_path,
        executable_resolver=lambda _: str(executable),
    )

    result = launcher.launch("grok", handoff, model="grok-4")

    matching = [argument for argument in result.argv if str(handoff) in argument]
    assert len(matching) == 1
    assert result.shell is False


def test_model_identifier_rejects_shell_control_characters(tmp_path: Path) -> None:
    executable = tmp_path / "gemini"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    launcher = HarnessLauncher(
        managed_root=tmp_path,
        executable_resolver=lambda _: str(executable),
    )

    with pytest.raises(CapabilityError, match="model identifier"):
        launcher.launch("gemini", _handoff(tmp_path), model="gemini; rm")


def test_custom_profile_uses_argv_template_without_shell_parsing(tmp_path: Path) -> None:
    executable = tmp_path / "review-cli"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    handoff = _handoff(tmp_path)
    registry = build_default_harness_registry()
    registry.register(
        CustomHarnessProfile(
            name="review",
            executable_candidates=("review-cli",),
            arguments=("--input", "{handoff_path}", "--model", "{model}", "{prompt}"),
        )
    )
    launcher = HarnessLauncher(
        managed_root=tmp_path,
        registry=registry,
        executable_resolver=lambda _: str(executable),
    )

    result = launcher.launch("review", handoff, model="safe-model")

    assert result.argv[1:5] == ("--input", str(handoff), "--model", "safe-model")
    assert result.shell is False


def test_launcher_rejects_unmanaged_handoff_and_missing_binary(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = _handoff(tmp_path, "outside.mdc")
    launcher = HarnessLauncher(managed_root=managed, executable_resolver=lambda _: None)

    with pytest.raises(ExternalActionError, match="managed root"):
        launcher.launch("codex", outside, model="gpt-5")

    inside = _handoff(managed)
    with pytest.raises(ExternalActionError, match="not installed"):
        launcher.launch("codex", inside, model="gpt-5")


@pytest.mark.parametrize(
    ("returncode", "message"),
    [
        (7, "exited with status 7"),
        (None, "did not report a terminal exit status"),
    ],
)
def test_execute_rejects_failed_or_detached_process_results(
    tmp_path: Path,
    returncode: int | None,
    message: str,
) -> None:
    executable = tmp_path / "codex"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    launcher = HarnessLauncher(
        managed_root=tmp_path,
        executable_resolver=lambda _: str(executable),
        executor=lambda *_args, **_kwargs: SimpleNamespace(pid=41, returncode=returncode),
    )

    with pytest.raises(ExternalActionError, match=message):
        launcher.launch("codex", _handoff(tmp_path), execute=True)


def test_default_executor_waits_for_a_real_fixture_cli_and_reads_exit_status(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "fixture-session-completed.txt"
    script = tmp_path / "fixture_cli.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('complete', encoding='utf-8')\n",
        encoding="utf-8",
    )
    registry = HarnessRegistry(
        [
            CustomHarnessProfile(
                name="fixture",
                executable_candidates=("python",),
                arguments=(str(script), str(marker)),
            )
        ]
    )
    launcher = HarnessLauncher(
        managed_root=tmp_path,
        registry=registry,
        executable_resolver=lambda _: sys.executable,
    )

    result = launcher.launch("fixture", _handoff(tmp_path), execute=True)

    assert marker.read_text(encoding="utf-8") == "complete"
    assert result.executed is True
    assert result.returncode == 0
