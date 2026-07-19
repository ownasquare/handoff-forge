from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "extensions" / "local-notes-provider"


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and local paths only.
        argv,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def test_local_notes_wheel_is_discovered_without_import_then_loads_when_allowlisted(
    tmp_path: Path,
) -> None:
    wheels = tmp_path / "wheels"
    installed = tmp_path / "installed"
    wheels.mkdir()
    installed.mkdir()
    uv = shutil.which("uv")
    assert uv is not None

    for project in (ROOT, EXAMPLE):
        _run(
            [
                uv,
                "build",
                "--offline",
                "--wheel",
                "--out-dir",
                str(wheels),
                str(project),
            ],
            cwd=tmp_path,
        )

    core_wheel = next(wheels.glob("handoff_forge-0.3.0-*.whl"))
    extension_wheel = next(wheels.glob("handoff_forge_local_notes-0.1.0-*.whl"))
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-compile",
            "--no-deps",
            "--target",
            str(installed),
            str(core_wheel),
            str(extension_wheel),
        ],
        cwd=tmp_path,
    )

    probe = textwrap.dedent(
        """
        import json
        import sys
        from pathlib import Path

        installed = Path(sys.argv[1]).resolve()
        data_root = Path(sys.argv[2]).resolve()
        sys.path.insert(0, str(installed))

        import handoff_forge
        from handoff_forge.application import build_application
        from handoff_forge.cli import app
        from handoff_forge.config import HandoffSettings
        from typer.testing import CliRunner

        assert Path(handoff_forge.__file__).resolve().is_relative_to(installed)
        module_name = "handoff_forge_local_notes.provider"
        settings = HandoffSettings(data_root=data_root, offline=True, allow_network=False)

        cli_result = CliRunner().invoke(
            app,
            ["--data-root", str(data_root), "extensions", "list", "--json"],
        )
        assert cli_result.exit_code == 0, cli_result.stdout
        cli_item = next(
            item for item in json.loads(cli_result.stdout)
            if item["name"] == "local-notes"
        )
        assert cli_item["status"] == "available"
        assert module_name not in sys.modules

        disabled = build_application(settings)
        item = next(item for item in disabled.list_extensions() if item.name == "local-notes")
        assert item.status == "available"
        assert item.enabled is False
        assert module_name not in sys.modules

        enabled = build_application(settings, enabled_extensions=("local-notes",))
        enabled_item = next(
            item for item in enabled.list_extensions()
            if item.name == "local-notes"
        )
        provider = enabled.providers.get("local-notes")
        assert enabled_item.status == "enabled"
        assert enabled_item.enabled is True
        assert module_name in sys.modules
        assert provider.status().state == "ready"
        print(json.dumps({"name": enabled_item.name, "status": enabled_item.status}))
        """
    )
    result = _run(
        [
            sys.executable,
            "-I",
            "-c",
            probe,
            str(installed),
            str(tmp_path / "data"),
        ],
        cwd=tmp_path,
    )

    assert json.loads(result.stdout) == {"name": "local-notes", "status": "enabled"}


def test_extension_quickstart_is_complete_for_a_clean_checkout() -> None:
    documented_commands = (
        "uv sync --frozen",
        "uv pip install --editable examples/extensions/local-notes-provider",
        "uv run --no-sync handoff-forge",
        "--enable-extension local-notes doctor",
        'project create "Plugin demo"',
        "ingest README.md --project plugin-demo",
        "--provider local-notes",
        "outputs --project plugin-demo",
    )

    for relative in (
        Path("docs/extending.md"),
        Path("examples/extensions/local-notes-provider/README.md"),
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        positions = [text.index(command) for command in documented_commands]
        assert positions == sorted(positions)
