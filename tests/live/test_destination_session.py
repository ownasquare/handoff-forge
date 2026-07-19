from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - explicit opt-in live integration proof.
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from handoff_forge.application import build_application
from handoff_forge.config import HandoffSettings
from handoff_forge.harnesses.base import handoff_prompt, validate_model_id
from handoff_forge.models import HandoffMode, JobStatus, TemplateProfile

_DESTINATION_ENV = "HANDOFF_FORGE_LIVE_DESTINATION"
_MODEL_ENV = "HANDOFF_FORGE_LIVE_DESTINATION_MODEL"
_SUPPORTED_DESTINATION = "claude"
_SESSION_TIMEOUT_SECONDS = 180


def _build_claude_argv(
    executable: Path,
    *,
    prompt: str,
    session_id: str,
    model: str | None,
) -> list[str]:
    argv = [
        str(executable),
        "--print",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(
            {
                "type": "object",
                "properties": {"proof": {"type": "string"}},
                "required": ["proof"],
                "additionalProperties": False,
            },
            separators=(",", ":"),
        ),
        "--tools",
        "Read",
        "--permission-mode",
        "dontAsk",
        "--max-turns",
        "1",
        "--session-id",
        session_id,
        "--no-session-persistence",
        "--no-chrome",
    ]
    if model is not None:
        argv.extend(("--model", model))
    argv.append(prompt)
    return argv


def _failure_diagnostic(payload: Mapping[str, Any], *, returncode: int) -> dict[str, Any]:
    permission_denials = payload.get("permission_denials")
    denial_count = len(permission_denials) if isinstance(permission_denials, list) else 0
    turns = payload.get("num_turns")
    turn_count = turns if isinstance(turns, int) and not isinstance(turns, bool) else None
    return {
        "exit_status": returncode,
        "failure_kind": _failure_kind(payload.get("result")),
        "is_error": payload.get("is_error") is True,
        "permission_denial_count": denial_count,
        "structured_output_present": bool(_structured_output(payload)),
        "turn_count": turn_count,
    }


def test_claude_argv_is_new_read_only_and_non_persistent() -> None:
    session_id = "00000000-0000-4000-8000-000000000001"

    argv = _build_claude_argv(
        Path("claude"),
        prompt="Read the checked handoff and return the fixed proof token.",
        session_id=session_id,
        model="claude-sonnet-4-6",
    )

    assert argv[0] == "claude"
    assert argv[argv.index("--session-id") + 1] == session_id
    assert argv[argv.index("--tools") + 1] == "Read"
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert "--no-session-persistence" in argv
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert not {"--resume", "--continue", "-r", "-c"}.intersection(argv)


def test_failure_diagnostic_is_an_allowlist_without_sensitive_details() -> None:
    private_path = "/" + "Users/example/private/handoff.mdc"
    payload = {
        "subtype": "account@example.invalid",
        "is_error": True,
        "num_turns": 1,
        "permission_denials": [{"tool": "Read", "path": private_path}],
        "request_id": "request-private-identifier",
        "result": f"authentication_error while reading {private_path}",
    }

    diagnostic = _failure_diagnostic(payload, returncode=1)

    assert diagnostic == {
        "exit_status": 1,
        "failure_kind": "authentication_error",
        "is_error": True,
        "permission_denial_count": 1,
        "structured_output_present": False,
        "turn_count": 1,
    }
    serialized = json.dumps(diagnostic, sort_keys=True)
    assert private_path not in serialized
    assert "request-private-identifier" not in serialized
    assert "example.invalid" not in serialized


def test_structured_output_requires_one_proof_property() -> None:
    assert _structured_output({"structured_output": {"proof": "token"}}) == {"proof": "token"}
    assert _structured_output({"structured_output": ["token"]}) == {}


def _require_opt_in() -> tuple[Path, str | None]:
    destination = os.environ.get(_DESTINATION_ENV, "").strip().lower()
    if not destination:
        pytest.skip(f"set {_DESTINATION_ENV}=claude to opt in")
    if destination != _SUPPORTED_DESTINATION:
        pytest.fail(
            "authenticated destination proof currently supports Claude Code only",
            pytrace=False,
        )
    executable = shutil.which("claude")
    if executable is None:
        pytest.skip("Claude Code is not installed")
    try:
        resolved = Path(executable).resolve(strict=True)
    except OSError:
        pytest.skip("Claude Code executable is unavailable")
    model = os.environ.get(_MODEL_ENV, "").strip() or None
    try:
        safe_model = validate_model_id(model)
    except Exception:
        pytest.fail("the selected destination model identifier is invalid", pytrace=False)
    return resolved, safe_model


def _authenticated(executable: Path) -> bool:
    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603 - fixed executable/argv.
            [str(executable), "auth", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("loggedIn") is True or payload.get("logged_in") is True


def _structured_output(payload: Mapping[str, Any]) -> dict[str, Any]:
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            decoded = json.loads(result)
        except json.JSONDecodeError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _failure_kind(result: object) -> str:
    message = str(result).lower()
    for marker in (
        "authentication_error",
        "rate_limit",
        "permission",
        "model",
        "network",
    ):
        if marker in message:
            return marker
    return "destination_error" if message else "empty_result"


@pytest.mark.live
def test_authenticated_claude_reads_checked_handoff_in_a_new_session(tmp_path: Path) -> None:
    executable, model = _require_opt_in()
    if not _authenticated(executable):
        pytest.skip("Claude Code auth status did not report an authenticated session")

    token = f"hf-destination-{uuid.uuid4().hex}"
    try:
        service = build_application(
            HandoffSettings(data_root=tmp_path / "data", offline=True, allow_network=False)
        )
        project = service.create_project(
            "Destination session proof",
            f"destination-proof-token: {token}",
        )
        generated = service.generate_handoff(
            project.id,
            mode=HandoffMode.POST_TASK,
            profile=TemplateProfile.CODEX_POST_CHAT_V1,
        )
    except Exception as error:
        pytest.fail(
            f"could not create the checked destination handoff ({type(error).__name__})",
            pytrace=False,
        )
    if (
        generated.job.status is not JobStatus.COMPLETE
        or generated.output is None
        or generated.validation is None
        or not generated.validation.valid
    ):
        pytest.fail("could not create a schema-valid destination handoff", pytrace=False)
    try:
        checked_path = generated.output.stored_path.resolve(strict=True)
        checked_content = checked_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        pytest.fail(
            f"could not read the checked destination handoff ({type(error).__name__})",
            pytrace=False,
        )
    if token not in checked_content:
        pytest.fail("checked destination handoff omitted the proof token", pytrace=False)

    prompt = (
        f"{handoff_prompt(checked_path)} "
        "For this verification only, read the destination-proof-token field and return it "
        "as the proof property. Do not summarize the file or perform any next action."
    )
    argv = _build_claude_argv(
        executable,
        prompt=prompt,
        session_id=str(uuid.uuid4()),
        model=model,
    )

    forbidden = {"--resume", "--continue", "-r", "-c"}
    if forbidden.intersection(argv):
        pytest.fail("destination proof attempted to resume a session", pytrace=False)
    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603 - fixed CLI/argv, no shell.
            argv,
            cwd=str(tmp_path),
            check=False,
            capture_output=True,
            text=True,
            timeout=_SESSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"Claude destination proof exceeded {_SESSION_TIMEOUT_SECONDS}s",
            pytrace=False,
        )
    except OSError as error:
        pytest.fail(
            f"could not start Claude destination proof ({type(error).__name__})",
            pytrace=False,
        )

    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        pytest.fail("Claude destination proof returned invalid JSON", pytrace=False)
    if not isinstance(payload, dict):
        pytest.fail("Claude destination proof returned an invalid JSON shape", pytrace=False)
    diagnostic = _failure_diagnostic(payload, returncode=completed.returncode)
    if completed.returncode != 0 or payload.get("is_error") is True:
        pytest.fail(
            f"Claude destination proof did not complete: {json.dumps(diagnostic, sort_keys=True)}",
            pytrace=False,
        )
    if _structured_output(payload) != {"proof": token}:
        pytest.fail(
            f"Claude destination proof did not match: {json.dumps(diagnostic, sort_keys=True)}",
            pytrace=False,
        )
    print(
        json.dumps(
            {
                "destination": "claude",
                "model_selection": "explicit" if model else "account_default",
                "persistence": False,
                "proof_schema": "handoff-forge.destination-session-proof.v1",
                "session": "new",
                "status": "passed",
                "token_matched": True,
                "tools": ["Read"],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
