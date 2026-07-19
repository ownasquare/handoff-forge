from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from typer.main import get_command
from typer.testing import CliRunner

from handoff_forge.application import HandoffApplication
from handoff_forge.cli import app
from handoff_forge.extensions import ExtensionInfo
from handoff_forge.models import JobStatus, ModelRoute

runner = CliRunner()
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - dry-run argv validation only.


def test_cli_help_exposes_complete_offline_workflow() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "doctor",
        "extensions",
        "project",
        "ingest",
        "inspect",
        "search",
        "rebuild",
        "outputs",
        "generate",
        "resume",
        "cancel",
        "merge",
        "validate",
        "launch",
        "copy-path",
        "open",
        "demo",
        "ui",
    ):
        assert command in result.stdout


def test_extensions_list_has_human_and_json_workflows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extension = ExtensionInfo(
        name="local-notes",
        kind="provider",
        value="handoff_forge_local_notes:create_provider",
        enabled=False,
        status="available",
    )
    monkeypatch.setattr(HandoffApplication, "list_extensions", lambda _self: (extension,))
    root = tmp_path / "cli-data"

    human = runner.invoke(app, ["--data-root", str(root), "extensions", "list"])
    as_json = runner.invoke(
        app,
        ["--data-root", str(root), "extensions", "list", "--json"],
    )

    assert human.exit_code == 0, human.stdout
    assert "local-notes\tprovider\tavailable" in human.stdout
    assert as_json.exit_code == 0, as_json.stdout
    assert json.loads(as_json.stdout) == [
        {
            "enabled": False,
            "kind": "provider",
            "name": "local-notes",
            "reason": None,
            "status": "available",
            "value": "handoff_forge_local_notes:create_provider",
        }
    ]


def test_generate_visual_evidence_flag_populates_all_routes_exactly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[dict[int, ModelRoute]] = []

    def fake_generate(
        _self,
        _project,
        *,
        mode,
        profile,
        routes,
    ):
        del mode, profile
        captured.append(dict(routes))
        return SimpleNamespace(
            job=SimpleNamespace(status=JobStatus.COMPLETE),
            output=SimpleNamespace(stored_path=tmp_path / "generated.handoff.mdc"),
        )

    monkeypatch.setattr(HandoffApplication, "generate_handoff", fake_generate)
    root = tmp_path / "cli-data"
    enabled = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "generate",
            "--project",
            "route-project",
            "--provider",
            "openai",
            "--model",
            "gpt-test",
            "--allow-cloud-upload",
            "--include-visual-evidence",
        ],
    )
    defaulted = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "generate",
            "--project",
            "route-project",
        ],
    )

    assert enabled.exit_code == 0, enabled.stdout
    assert defaulted.exit_code == 0, defaulted.stdout
    assert len(captured) == 2
    assert set(captured[0]) == set(range(1, 13))
    assert all(route.include_visual_evidence for route in captured[0].values())
    assert all(route.allow_cloud_upload for route in captured[0].values())
    assert all(not route.include_visual_evidence for route in captured[1].values())


def test_project_commands_are_json_capable(tmp_path: Path) -> None:
    root = tmp_path / "cli-data"
    created = runner.invoke(
        app,
        ["--data-root", str(root), "project", "create", "Release Handoff", "--json"],
    )
    assert created.exit_code == 0, created.stdout
    project = json.loads(created.stdout)

    listed = runner.invoke(
        app,
        ["--data-root", str(root), "project", "list", "--json"],
    )
    assert listed.exit_code == 0, listed.stdout
    assert json.loads(listed.stdout)[0]["id"] == project["id"]


def test_doctor_never_echoes_provider_secret_values(tmp_path: Path, monkeypatch) -> None:
    canary = "provider-secret-canary-value"
    monkeypatch.setenv("OPENAI_API_KEY", canary)

    result = runner.invoke(
        app,
        ["--data-root", str(tmp_path / "doctor-data"), "doctor", "--json"],
    )

    assert result.exit_code == 0, result.stdout
    assert canary not in result.stdout
    assert "OPENAI_API_KEY" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["network_enabled"] is False


def test_ui_dry_run_includes_validated_host_and_launch_accepts_dry_run(tmp_path: Path) -> None:
    ui = runner.invoke(
        app,
        [
            "--data-root",
            str(tmp_path / "ui-data"),
            "ui",
            "--host",
            ALL_INTERFACES,
            "--port",
            "8765",
            "--dry-run",
            "--json",
        ],
    )
    launch = get_command(app).commands["launch"]

    assert ui.exit_code == 0, ui.stdout
    payload = json.loads(ui.stdout)
    assert payload["executed"] is False
    assert ALL_INTERFACES in payload["argv"]
    assert "8765" in payload["argv"]
    assert any("--dry-run" in parameter.opts for parameter in launch.params)
