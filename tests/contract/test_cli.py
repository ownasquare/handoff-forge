from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from typer.main import get_command
from typer.testing import CliRunner

from handoff_forge.application import HandoffApplication
from handoff_forge.cli import app
from handoff_forge.extensions import ExtensionInfo
from handoff_forge.harnesses.lifecycle import (
    CodexPreCompactCapability,
    LifecycleArtifact,
)
from handoff_forge.models import JobStatus, ModelRoute

runner = CliRunner()
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - dry-run argv validation only.


def test_cli_help_exposes_complete_offline_workflow() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "doctor",
        "extensions",
        "lifecycle",
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


def test_lifecycle_cli_exposes_safe_codex_management_commands() -> None:
    lifecycle_result = runner.invoke(app, ["lifecycle", "--help"])
    result = runner.invoke(app, ["lifecycle", "codex", "--help"])

    assert lifecycle_result.exit_code == 0, lifecycle_result.stdout
    assert "run" in lifecycle_result.stdout
    assert result.exit_code == 0, result.stdout
    for command in ("install", "verify", "disable", "uninstall"):
        assert command in result.stdout


def test_lifecycle_cli_manages_a_temporary_hook_file_without_losing_other_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "handoff_forge.harnesses.lifecycle.probe_codex_precompact",
        lambda _executable, *, cwd=None: CodexPreCompactCapability(
            feature_enabled=True,
            version="fixture",
            feature_evidence="features-list",
        ),
    )
    root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    hooks_file = tmp_path / "codex-home" / "hooks.json"
    hooks_file.parent.mkdir()
    hooks_file.write_text(
        json.dumps(
            {
                "description": "keep this valid metadata",
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "/usr/bin/keep-stop"}]}]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    created = runner.invoke(
        app,
        ["--data-root", str(root), "project", "create", "Lifecycle CLI", "--json"],
    )
    project_id = json.loads(created.stdout)["id"]

    installed = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "lifecycle",
            "codex",
            "install",
            "--project",
            project_id,
            "--workspace",
            str(workspace),
            "--hooks-file",
            str(hooks_file),
            "--json",
        ],
    )
    assert installed.exit_code == 0, installed.stdout
    binding_id = json.loads(installed.stdout)["id"]

    human_install = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "lifecycle",
            "codex",
            "install",
            "--project",
            project_id,
            "--workspace",
            str(workspace),
            "--hooks-file",
            str(hooks_file),
        ],
    )
    assert human_install.exit_code == 0, human_install.stdout
    assert "Configured Codex PreCompact binding" in human_install.stdout
    assert "/hooks" in human_install.stdout
    assert "review and trust" in human_install.stdout
    assert "before relying on delivery" in human_install.stdout

    verified = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "lifecycle",
            "codex",
            "verify",
            binding_id,
            "--hooks-file",
            str(hooks_file),
            "--json",
        ],
    )
    assert verified.exit_code == 0, verified.stdout
    verified_payload = json.loads(verified.stdout)
    assert verified_payload["configured"] is True
    assert verified_payload["binding_enabled"] is True
    assert verified_payload["feature_enabled"] is True
    assert verified_payload["feature_evidence"] == "features-list"
    assert verified_payload["trust_status"] == "unverified"
    assert verified_payload["runtime_activation"] == "unverified"
    assert "capability_supported" not in verified_payload

    human_verified = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "lifecycle",
            "codex",
            "verify",
            binding_id,
            "--hooks-file",
            str(hooks_file),
        ],
    )
    assert human_verified.exit_code == 0, human_verified.stdout
    assert "configured=True" in human_verified.stdout
    assert "binding_enabled=True" in human_verified.stdout
    assert "feature_enabled=True" in human_verified.stdout
    assert "trust=unverified" in human_verified.stdout
    assert "runtime_activation=unverified" in human_verified.stdout
    assert "/hooks" in human_verified.stdout
    assert "runtime-authoritative" not in human_verified.stdout.casefold()

    disabled = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "lifecycle",
            "codex",
            "disable",
            binding_id,
            "--json",
        ],
    )
    assert disabled.exit_code == 0, disabled.stdout
    assert json.loads(disabled.stdout)["enabled"] is False

    uninstalled = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "lifecycle",
            "codex",
            "uninstall",
            binding_id,
            "--hooks-file",
            str(hooks_file),
            "--json",
        ],
    )
    assert uninstalled.exit_code == 0, uninstalled.stdout
    rendered = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert rendered["hooks"]["Stop"] == [
        {"hooks": [{"type": "command", "command": "/usr/bin/keep-stop"}]}
    ]


def test_lifecycle_run_exposes_explicit_deduplicated_post_task_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output_path = tmp_path / "post-task.handoff.mdc"
    content = "# Verified post-task handoff\n"
    output_path.write_text(content, encoding="utf-8")
    generated: list[str] = []

    def fake_generate(_self, project_reference, *, event, lifecycle_event_id):
        generated.append(f"{event.value}:{lifecycle_event_id}")
        return LifecycleArtifact(
            output_id="out_post_task",
            project_id=project_reference,
            path=output_path,
            sha256=hashlib.sha256(content.encode()).hexdigest(),
            profile="codex-post-chat-v1",
        )

    monkeypatch.setattr(HandoffApplication, "generate_lifecycle_handoff", fake_generate)
    created = runner.invoke(
        app,
        ["--data-root", str(root), "project", "create", "Post-task CLI", "--json"],
    )
    project_id = json.loads(created.stdout)["id"]
    argv = [
        "--data-root",
        str(root),
        "lifecycle",
        "run",
        "--event",
        "post-task",
        "--project",
        project_id,
        "--workspace",
        str(workspace),
        "--event-key",
        "task-contract-1",
        "--json",
    ]

    first = runner.invoke(app, argv)
    duplicate = runner.invoke(app, argv)

    assert first.exit_code == 0, first.stdout
    assert duplicate.exit_code == 0, duplicate.stdout
    assert json.loads(first.stdout) == json.loads(duplicate.stdout)
    assert len(generated) == 1
    assert output_path.name in json.loads(first.stdout)["systemMessage"]

    failed = runner.invoke(
        app,
        [
            "--data-root",
            str(root),
            "lifecycle",
            "run",
            "--event",
            "post-task",
            "--project",
            project_id,
            "--workspace",
            str(tmp_path / "private missing workspace"),
            "--event-key",
            "task-contract-failure",
            "--json",
        ],
    )
    assert failed.exit_code == 2
    assert str(tmp_path) not in failed.output


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
