from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

import pytest

from handoff_forge.errors import CapabilityError, StorageError
from handoff_forge.harnesses import lifecycle as lifecycle_module
from handoff_forge.harnesses.lifecycle import (
    ADAPTER_ID,
    CodexHookConfigManager,
    CodexLifecycleAdapter,
    CodexPreCompactCapability,
    LifecycleArtifact,
    LifecycleStateStore,
    probe_codex_precompact,
)
from handoff_forge.models import HandoffMode


def _artifact(
    root: Path,
    *,
    event: HandoffMode = HandoffMode.PRE_COMPACT,
    content: str = "# Verified handoff\n",
) -> LifecycleArtifact:
    profile = "codex-precompact-v1" if event is HandoffMode.PRE_COMPACT else "codex-post-chat-v1"
    path = root / f"verified.{event.value}.handoff.mdc"
    path.write_text(content, encoding="utf-8")
    return LifecycleArtifact(
        output_id="out_verified",
        project_id="prj_verified",
        path=path,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        profile=profile,
    )


def _enabled_feature(version: str = "0.120.0") -> CodexPreCompactCapability:
    return CodexPreCompactCapability(
        feature_enabled=True,
        version=version,
        feature_evidence="features-list",
    )


def _payload(
    workspace: Path,
    *,
    event: str = "PreCompact",
    transcript_path: Path | None = None,
) -> dict[str, object]:
    transcript = transcript_path or workspace / "codex-transcript.jsonl"
    if not transcript.exists():
        transcript.write_text('{"type":"fixture"}\n', encoding="utf-8")
    return {
        "session_id": "session-safe-fixture",
        "turn_id": "turn-safe-fixture",
        "cwd": str(workspace),
        "hook_event_name": event,
        "trigger": "manual",
        "model": "fixture-model",
        "transcript_path": str(transcript),
    }


def test_install_is_capability_gated_and_preserves_existing_hooks(tmp_path: Path) -> None:
    hooks_path = tmp_path / "codex-home" / "hooks.json"
    hooks_path.parent.mkdir()
    original = {
        "description": "keep this metadata",
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/usr/bin/example-stop-hook",
                        }
                    ]
                }
            ],
        },
    }
    hooks_path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    before = hooks_path.read_bytes()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LifecycleStateStore(tmp_path / "data")
    unsupported = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=store,
        capability_resolver=lambda _executable, _workspace: CodexPreCompactCapability(
            feature_enabled=False,
            version="999.0.0",
            feature_evidence="features-list",
        ),
    )

    with pytest.raises(CapabilityError, match="PreCompact"):
        unsupported.install(
            project_id="prj_fixture",
            workspace=workspace,
            executable=Path("/opt/handoff-forge"),
        )

    assert hooks_path.read_bytes() == before
    assert store.list_bindings() == ()

    supported = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=store,
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )
    installed = supported.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/handoff-forge"),
    )
    repeated = supported.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/handoff-forge"),
    )
    rendered = json.loads(hooks_path.read_text(encoding="utf-8"))

    assert installed.binding_id == repeated.binding_id
    assert rendered["description"] == original["description"]
    assert rendered["hooks"]["Stop"] == original["hooks"]["Stop"]
    owned = [
        handler
        for group in rendered["hooks"]["PreCompact"]
        for handler in group["hooks"]
        if "handoff-forge-codex-precompact-v1" in handler.get("command", "")
    ]
    assert len(owned) == 1


def test_fresh_hooks_file_matches_official_schema_without_version(tmp_path: Path) -> None:
    hooks_path = tmp_path / "codex-home" / "hooks.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=LifecycleStateStore(tmp_path / "data"),
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )

    manager.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/handoff-forge"),
    )

    rendered = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert set(rendered) == {"hooks"}
    assert isinstance(rendered["hooks"]["PreCompact"], list)


def test_install_rejects_unknown_top_level_hook_fields_before_mutation(tmp_path: Path) -> None:
    hooks_path = tmp_path / "codex-home" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text('{"version":1,"hooks":{}}\n', encoding="utf-8")
    before = hooks_path.read_bytes()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LifecycleStateStore(tmp_path / "data")
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=store,
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )

    with pytest.raises(StorageError, match="unsupported top-level"):
        manager.install(
            project_id="prj_fixture",
            workspace=workspace,
            executable=Path("/opt/handoff-forge"),
        )

    assert hooks_path.read_bytes() == before
    assert store.list_bindings() == ()


def test_reinstall_replaces_stale_owned_handler(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=LifecycleStateStore(tmp_path / "data"),
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )

    binding = manager.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/old/handoff-forge"),
    )
    before_reinstall = json.loads(hooks_path.read_text(encoding="utf-8"))
    handlers = before_reinstall["hooks"]["PreCompact"][0]["hooks"]
    handlers.insert(0, {"type": "command", "command": "/usr/bin/keep-before"})
    handlers.append({"type": "command", "command": "/usr/bin/keep-after"})
    hooks_path.write_text(json.dumps(before_reinstall) + "\n", encoding="utf-8")
    manager.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/current/handoff-forge"),
    )

    rendered = json.loads(hooks_path.read_text(encoding="utf-8"))
    owned_argv = [
        shlex.split(handler["command"])
        for group in rendered["hooks"]["PreCompact"]
        for handler in group["hooks"]
        if handler.get("type") == "command"
        and shlex.split(handler["command"])[-4:]
        == ["--binding", binding.id, "--adapter-id", ADAPTER_ID]
    ]
    assert len(owned_argv) == 1
    assert owned_argv[0][0] == "/opt/current/handoff-forge"
    commands = [handler["command"] for handler in rendered["hooks"]["PreCompact"][0]["hooks"]]
    assert commands[0] == "/usr/bin/keep-before"
    assert shlex.split(commands[1])[0] == "/opt/current/handoff-forge"
    assert commands[2] == "/usr/bin/keep-after"


def test_reinstall_moves_owned_handler_back_to_the_supported_matcher(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=LifecycleStateStore(tmp_path / "data"),
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )
    binding = manager.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/old/handoff-forge"),
    )
    stale = json.loads(hooks_path.read_text(encoding="utf-8"))
    stale["hooks"]["PreCompact"][0]["matcher"] = "^manual$"
    hooks_path.write_text(json.dumps(stale) + "\n", encoding="utf-8")

    manager.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/current/handoff-forge"),
    )

    rendered = json.loads(hooks_path.read_text(encoding="utf-8"))
    owned_groups = [
        group
        for group in rendered["hooks"]["PreCompact"]
        if any(
            handler.get("type") == "command"
            and shlex.split(handler["command"])[-4:]
            == ["--binding", binding.id, "--adapter-id", ADAPTER_ID]
            for handler in group["hooks"]
        )
    ]
    assert len(owned_groups) == 1
    assert owned_groups[0]["matcher"] == "^(manual|auto)$"
    assert manager.verify(binding.id).configured is True


def test_disable_verify_and_uninstall_touch_only_the_owned_binding(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "description": "keep this valid metadata",
                "hooks": {
                    "PreCompact": [
                        {
                            "matcher": "^manual$",
                            "hooks": [{"type": "command", "command": "/usr/bin/keep-me"}],
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LifecycleStateStore(tmp_path / "data")
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=store,
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )
    binding = manager.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/handoff-forge"),
    )

    report = manager.verify(binding.id, codex_executable="codex")
    assert report.configured is True
    assert report.binding_enabled is True
    assert report.feature_enabled is True
    assert report.trust_status == "unverified"
    assert report.runtime_activation == "unverified"

    disabled = manager.disable(binding.id)
    assert disabled.enabled is False
    disabled_report = manager.verify(binding.id, codex_executable="codex")
    assert disabled_report.configured is True
    assert disabled_report.binding_enabled is False

    manager.uninstall(binding.id)
    rendered = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert rendered["hooks"]["PreCompact"] == [
        {
            "matcher": "^manual$",
            "hooks": [{"type": "command", "command": "/usr/bin/keep-me"}],
        }
    ]
    assert store.get_binding(binding.id) is None


def test_uninstall_preserves_unrelated_empty_groups(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    unrelated_group = {"matcher": "^never$", "hooks": []}
    hooks_path.write_text(
        json.dumps({"hooks": {"PreCompact": [unrelated_group]}}) + "\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=LifecycleStateStore(tmp_path / "data"),
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )
    binding = manager.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/handoff-forge"),
    )

    manager.uninstall(binding.id)

    rendered = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert unrelated_group in rendered["hooks"]["PreCompact"]


def test_uninstall_does_not_claim_substring_command(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LifecycleStateStore(tmp_path / "data")
    binding = store.create_binding(project_id="prj_fixture", workspace=workspace)
    unrelated_handler = {
        "type": "command",
        "command": f"/usr/bin/echo {ADAPTER_ID} {binding.id}",
    }
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreCompact": [
                        {"matcher": "^manual$", "hooks": [unrelated_handler]},
                    ]
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=store,
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )
    manager.install(
        project_id="prj_fixture",
        workspace=workspace,
        executable=Path("/opt/handoff-forge"),
    )

    manager.uninstall(binding.id)

    rendered = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert unrelated_handler in [
        handler for group in rendered["hooks"]["PreCompact"] for handler in group["hooks"]
    ]


def test_install_rolls_back_new_binding_when_hook_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_path = tmp_path / "hooks.json"
    original = '{"description":"fixture","hooks":{"Stop":[]}}\n'
    hooks_path.write_text(original, encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LifecycleStateStore(tmp_path / "data")
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=store,
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )

    def interrupt_write(_document) -> None:
        raise OSError("simulated hooks write interruption")

    monkeypatch.setattr(manager, "_write_document", interrupt_write)

    with pytest.raises(OSError, match="hooks write interruption"):
        manager.install(
            project_id="prj_fixture",
            workspace=workspace,
            executable=Path("/opt/handoff-forge"),
        )

    assert hooks_path.read_text(encoding="utf-8") == original
    assert store.list_bindings() == ()


def test_install_restores_exact_file_when_write_fails_after_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_path = tmp_path / "hooks.json"
    original = b'{"description":"exact fixture bytes","hooks":{"Stop":[]}}\n'
    hooks_path.write_bytes(original)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LifecycleStateStore(tmp_path / "data")
    manager = CodexHookConfigManager(
        hooks_path=hooks_path,
        state_store=store,
        capability_resolver=lambda _executable, _workspace: _enabled_feature(),
    )
    real_write = manager._write_document

    def interrupt_after_replacement(document) -> None:
        real_write(document)
        raise OSError("simulated post-replacement interruption")

    monkeypatch.setattr(manager, "_write_document", interrupt_after_replacement)

    with pytest.raises(OSError, match="post-replacement interruption"):
        manager.install(
            project_id="prj_fixture",
            workspace=workspace,
            executable=Path("/opt/handoff-forge"),
        )

    assert hooks_path.read_bytes() == original
    assert store.list_bindings() == ()


def test_adapter_routes_only_precompact_and_deduplicates_after_readback(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    artifact = _artifact(tmp_path)
    calls: list[str] = []

    def generate(_binding, event: HandoffMode, event_id: str) -> LifecycleArtifact:
        calls.append(f"{event.value}:{event_id}")
        return artifact

    adapter = CodexLifecycleAdapter(state_store=state, generator=generate)
    first = adapter.handle(_payload(workspace), binding.id)
    duplicate = adapter.handle(_payload(workspace), binding.id)
    stop = adapter.handle(_payload(workspace, event="Stop"), binding.id)

    assert len(calls) == 1
    assert first["continue"] is True
    assert str(artifact.path.resolve()) in first["systemMessage"]
    assert duplicate == first
    assert stop == {"continue": True}
    receipt = state.list_receipts()[0]
    assert receipt.status == "complete"
    assert receipt.output_id == artifact.output_id
    assert receipt.output_sha256 == artifact.sha256


def test_same_turn_distinct_transcript_revisions_create_distinct_receipts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = workspace / "codex-transcript.jsonl"
    transcript.write_text('{"message":"first"}\n', encoding="utf-8")
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    artifact = _artifact(tmp_path)
    calls: list[str] = []

    def generate(_binding, _event: HandoffMode, event_id: str) -> LifecycleArtifact:
        calls.append(event_id)
        return artifact

    adapter = CodexLifecycleAdapter(state_store=state, generator=generate)
    payload = _payload(workspace, transcript_path=transcript)
    first = adapter.handle(payload, binding.id)
    duplicate = adapter.handle(payload, binding.id)
    transcript.write_text(
        '{"message":"first"}\n{"message":"second-private-canary"}\n',
        encoding="utf-8",
    )
    revised = adapter.handle(payload, binding.id)

    assert first == duplicate == revised
    assert len(calls) == 2
    assert calls[0] != calls[1]
    receipts = state.list_receipts()
    assert len(receipts) == 2
    persisted = "\n".join(receipt.model_dump_json() for receipt in receipts)
    assert str(transcript) not in persisted
    assert "second-private-canary" not in persisted


def test_symlink_transcript_is_rejected_without_creating_a_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "real-transcript.jsonl"
    target.write_text('{"message":"private-canary"}\n', encoding="utf-8")
    transcript = workspace / "transcript-link.jsonl"
    transcript.symlink_to(target)
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    calls = 0

    def generate(_binding, _event: HandoffMode, _event_id: str) -> LifecycleArtifact:
        nonlocal calls
        calls += 1
        return _artifact(tmp_path)

    adapter = CodexLifecycleAdapter(state_store=state, generator=generate)
    response = adapter.handle(
        _payload(workspace, transcript_path=transcript),
        binding.id,
    )

    assert response["continue"] is True
    assert "manual" in str(response["systemMessage"]).casefold()
    assert calls == 0
    assert state.list_receipts() == ()


def test_null_transcript_path_continues_with_manual_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    calls = 0

    def generate(_binding, _event: HandoffMode, _event_id: str) -> LifecycleArtifact:
        nonlocal calls
        calls += 1
        return _artifact(tmp_path)

    payload = _payload(workspace)
    payload["transcript_path"] = None
    adapter = CodexLifecycleAdapter(state_store=state, generator=generate)

    response = adapter.handle(payload, binding.id)

    assert response["continue"] is True
    assert "manual" in str(response["systemMessage"]).casefold()
    assert calls == 0
    assert state.list_receipts() == ()


def test_adapter_failure_is_sanitized_and_same_event_can_retry(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    artifact = _artifact(tmp_path)
    attempts = 0

    def generate(_binding, _event: HandoffMode, _event_id: str) -> LifecycleArtifact:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError(
                f"temporary failure at {workspace / 'private' / 'state.json'} "
                "sk-secret-canary-value"
            )
        return artifact

    adapter = CodexLifecycleAdapter(state_store=state, generator=generate)
    failed = adapter.handle(_payload(workspace), binding.id)
    failed_receipt = state.list_receipts()[0]
    recovered = adapter.handle(_payload(workspace), binding.id)

    assert "manual" in failed["systemMessage"].casefold()
    assert "secret-canary" not in failed["systemMessage"]
    assert str(artifact.path.resolve()) in recovered["systemMessage"]
    assert str(tmp_path) not in failed_receipt.model_dump_json()
    assert failed_receipt.output_path is None
    receipt = state.list_receipts()[0]
    assert receipt.status == "complete"
    assert receipt.attempt_count == 2
    assert "secret-canary" not in receipt.model_dump_json()


def test_disabled_and_outside_workspace_events_are_noops(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    calls = 0

    def generate(_binding, _event: HandoffMode, _event_id: str) -> LifecycleArtifact:
        nonlocal calls
        calls += 1
        return _artifact(tmp_path)

    adapter = CodexLifecycleAdapter(state_store=state, generator=generate)

    assert adapter.handle(_payload(outside), binding.id) == {"continue": True}
    blank_cwd = _payload(workspace)
    blank_cwd["cwd"] = "   "
    assert adapter.handle(blank_cwd, binding.id) == {"continue": True}
    state.set_binding_enabled(binding.id, False)
    assert adapter.handle(_payload(workspace), binding.id) == {"continue": True}
    assert calls == 0
    assert state.list_receipts() == ()


def test_explicit_post_task_routes_deduplicates_even_when_native_binding_is_disabled(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    artifact = _artifact(tmp_path, event=HandoffMode.POST_TASK)
    calls: list[tuple[HandoffMode, str]] = []

    def generate(_binding, event: HandoffMode, event_id: str) -> LifecycleArtifact:
        calls.append((event, event_id))
        return artifact

    adapter = CodexLifecycleAdapter(state_store=state, generator=generate)
    first = adapter.run_explicit(
        event=HandoffMode.POST_TASK,
        binding_id=binding.id,
        cwd=workspace,
        event_key="task-fixture-1",
    )
    duplicate = adapter.run_explicit(
        event=HandoffMode.POST_TASK,
        binding_id=binding.id,
        cwd=workspace,
        event_key="task-fixture-1",
    )

    assert first == duplicate
    assert calls[0][0] is HandoffMode.POST_TASK
    assert len(calls) == 1
    assert str(artifact.path.resolve()) in first["systemMessage"]
    receipt = state.list_receipts()[0]
    assert receipt.event is HandoffMode.POST_TASK
    assert receipt.output_sha256 == artifact.sha256

    state.set_binding_enabled(binding.id, False)
    manual = adapter.run_explicit(
        event=HandoffMode.POST_TASK,
        binding_id=binding.id,
        cwd=workspace,
        event_key="task-fixture-2",
    )
    assert str(artifact.path.resolve()) in manual["systemMessage"]
    assert len(calls) == 2
    disabled_binding = state.get_binding(binding.id)
    assert disabled_binding is not None
    assert disabled_binding.enabled is False


def test_failed_complete_receipt_drops_raw_output_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    artifact = _artifact(tmp_path)

    adapter = CodexLifecycleAdapter(
        state_store=state,
        generator=lambda _binding, _event, _event_id: artifact,
    )
    adapter.handle(_payload(workspace), binding.id)
    artifact.path.unlink()
    adapter.handle(_payload(workspace), binding.id)

    receipt = state.list_receipts()[0]
    assert receipt.status == "failed"
    assert receipt.output_path is None
    assert str(tmp_path) not in receipt.model_dump_json()


@pytest.mark.parametrize(
    ("feature_output", "expected_enabled", "expected_evidence"),
    [
        ("hooks stable true\n", True, "features-list"),
        ("hooks stable false\n", False, "features-list"),
        ("history stable true\n", None, "hooks-row-missing"),
        ("hooks stable sometimes\n", None, "hooks-row-malformed"),
    ],
    ids=["enabled", "disabled", "absent", "malformed"],
)
def test_probe_parses_official_effective_hooks_feature_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feature_output: str,
    expected_enabled: bool | None,
    expected_evidence: str,
) -> None:
    calls: list[tuple[list[str], Path | None]] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def runtime(argv, **kwargs):
        calls.append((argv, kwargs.get("cwd")))
        if argv[1:] == ["--version"]:
            return lifecycle_module.subprocess.CompletedProcess(argv, 0, "codex 0.120.0", "")
        return lifecycle_module.subprocess.CompletedProcess(argv, 0, feature_output, "")

    monkeypatch.setattr(lifecycle_module.subprocess, "run", runtime)
    capability = probe_codex_precompact("codex-fixture", cwd=workspace)

    assert capability.feature_enabled is expected_enabled
    assert capability.version == "0.120.0"
    assert capability.feature_evidence == expected_evidence
    assert calls == [
        (["codex-fixture", "--version"], workspace),
        (["codex-fixture", "features", "list"], workspace),
    ]


def test_probe_reports_feature_command_failure_without_inferring_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def runtime(argv, **_kwargs):
        if argv[1:] == ["--version"]:
            return lifecycle_module.subprocess.CompletedProcess(argv, 0, "codex 0.120.0", "")
        return lifecycle_module.subprocess.CompletedProcess(argv, 137, "", "killed")

    monkeypatch.setattr(lifecycle_module.subprocess, "run", runtime)
    capability = probe_codex_precompact("codex-fixture", cwd=workspace)

    assert capability.feature_enabled is None
    assert capability.feature_evidence == "features-command-failed"


@pytest.mark.parametrize(
    ("feature_output", "feature_returncode"),
    [
        ("hooks stable false\n", 0),
        ("history stable true\n", 0),
        ("hooks stable sometimes\n", 0),
        ("", 137),
    ],
    ids=["disabled", "absent", "malformed", "exit-137"],
)
def test_install_fails_before_mutation_without_enabled_effective_feature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feature_output: str,
    feature_returncode: int,
) -> None:
    def runtime(argv, **_kwargs):
        if argv[1:] == ["--version"]:
            return lifecycle_module.subprocess.CompletedProcess(
                argv,
                137 if feature_returncode == 137 else 0,
                "" if feature_returncode == 137 else "codex 0.120.0",
                "killed" if feature_returncode == 137 else "",
            )
        return lifecycle_module.subprocess.CompletedProcess(
            argv,
            feature_returncode,
            feature_output,
            "fixture feature probe failure" if feature_returncode else "",
        )

    monkeypatch.setattr(lifecycle_module.subprocess, "run", runtime)
    hooks_path = tmp_path / "codex-home" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text('{"hooks":{"Stop":[]}}\n', encoding="utf-8")
    before = hooks_path.read_bytes()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LifecycleStateStore(tmp_path / "data")
    manager = CodexHookConfigManager(hooks_path=hooks_path, state_store=store)

    with pytest.raises(CapabilityError, match="feature"):
        manager.install(
            project_id="prj_fixture",
            workspace=workspace,
            executable=Path("/opt/handoff-forge"),
            codex_executable="codex-fixture",
        )

    assert hooks_path.read_bytes() == before
    assert store.list_bindings() == ()


def test_interrupted_atomic_binding_update_keeps_last_verified_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = LifecycleStateStore(tmp_path / "data")
    binding = state.create_binding(project_id="prj_verified", workspace=workspace)
    real_replace = lifecycle_module.os.replace
    interrupted = False

    def fail_once(source, destination):
        nonlocal interrupted
        if not interrupted and Path(destination).name == f"{binding.id}.json":
            interrupted = True
            raise OSError("simulated atomic replace interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(lifecycle_module.os, "replace", fail_once)

    with pytest.raises(OSError, match="simulated atomic"):
        state.set_binding_enabled(binding.id, False)

    assert interrupted is True
    assert state.get_binding(binding.id) == binding
