from __future__ import annotations

import json
import os
import time
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from handoff_forge.config import HandoffSettings
from handoff_forge.models import GenerationRequest, GenerationResult, ModelRoute
from handoff_forge.providers.registry import build_default_registry
from handoff_forge.security import redact_secrets

_CANARY_RESPONSE = {"canary": "handoff-forge-live-v1", "status": "ready"}
_LIVE_LATENCY_LIMIT_SECONDS = 60
_LIVE_PROVIDER_TIMEOUT_SECONDS = 45
_LIVE_MAX_OUTPUT_TOKENS = 64
_PROOF_SCHEMA = "handoff-forge.live-provider-proof.v1"
_TRUE_VALUES = {"1", "on", "true", "yes"}


def _parse_canary_response(text: str) -> dict[str, str]:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise AssertionError("provider did not return the fixed live-provider canary") from None
    if payload != _CANARY_RESPONSE:
        raise AssertionError("provider did not return the fixed live-provider canary")
    return dict(_CANARY_RESPONSE)


def _safe_proof_metadata(value: str) -> str:
    sanitized = redact_secrets(value.strip())
    if (
        not sanitized
        or "\n" in sanitized
        or "\r" in sanitized
        or PurePosixPath(sanitized).is_absolute()
        or PureWindowsPath(sanitized).is_absolute()
    ):
        return "[REDACTED]"
    return sanitized[:256]


def _live_proof_json(result: GenerationResult, *, elapsed_seconds: float) -> str:
    if elapsed_seconds < 0:
        raise AssertionError("live-provider elapsed time cannot be negative")
    canary = _parse_canary_response(result.text)
    proof = {
        "canary": canary["canary"],
        "elapsed_ms": round(elapsed_seconds * 1_000),
        "latency_limit_seconds": _LIVE_LATENCY_LIMIT_SECONDS,
        "max_output_tokens": _LIVE_MAX_OUTPUT_TOKENS,
        "model": _safe_proof_metadata(result.model),
        "passed": True,
        "proof_schema": _PROOF_SCHEMA,
        "provider": _safe_proof_metadata(result.provider),
        "text_only": True,
    }
    return json.dumps(proof, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _environment_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in _TRUE_VALUES


def test_canary_response_requires_the_exact_structured_contract() -> None:
    expected = {"canary": "handoff-forge-live-v1", "status": "ready"}

    assert _parse_canary_response(json.dumps(expected)) == expected


@pytest.mark.parametrize(
    "response",
    [
        "ready",
        '```json\n{"canary":"handoff-forge-live-v1","status":"ready"}\n```',
        '{"canary":"handoff-forge-live-v1","status":"ready","extra":true}',
        '{"canary":"wrong","status":"ready"}',
    ],
)
def test_canary_response_rejects_unstructured_or_inexact_output(response: str) -> None:
    with pytest.raises(AssertionError, match="fixed live-provider canary"):
        _parse_canary_response(response)


def test_live_proof_json_contains_only_sanitized_allowlisted_fields() -> None:
    private_path = "/" + "Users/example/private/path"
    secret_marker = "sk-" + "live-request-id-must-not-escape"
    result = GenerationResult(
        text='{"canary":"handoff-forge-live-v1","status":"ready"}',
        provider="openai",
        model="gpt-4.1-mini",
        request_id=secret_marker,
        finish_reason=private_path,
    )

    proof = json.loads(_live_proof_json(result, elapsed_seconds=1.234))

    assert proof == {
        "canary": "handoff-forge-live-v1",
        "elapsed_ms": 1234,
        "latency_limit_seconds": 60,
        "max_output_tokens": 64,
        "model": "gpt-4.1-mini",
        "passed": True,
        "proof_schema": "handoff-forge.live-provider-proof.v1",
        "provider": "openai",
        "text_only": True,
    }
    serialized = json.dumps(proof, sort_keys=True)
    assert "request-id" not in serialized
    assert private_path not in serialized
    assert "Return exactly" not in serialized

    unsafe_metadata = result.model_copy(
        update={
            "provider": secret_marker,
            "model": "/" + "Users/example/private/model",
        }
    )
    sanitized = json.loads(_live_proof_json(unsafe_metadata, elapsed_seconds=0.5))
    assert sanitized["provider"] == "[REDACTED]"
    assert sanitized["model"] == "[REDACTED]"


def test_live_opt_in_flag_requires_an_explicit_truthy_value(monkeypatch) -> None:
    monkeypatch.delenv("HANDOFF_FORGE_LIVE_ALLOW_CLOUD_UPLOAD", raising=False)
    assert _environment_flag("HANDOFF_FORGE_LIVE_ALLOW_CLOUD_UPLOAD") is False

    monkeypatch.setenv("HANDOFF_FORGE_LIVE_ALLOW_CLOUD_UPLOAD", "false")
    assert _environment_flag("HANDOFF_FORGE_LIVE_ALLOW_CLOUD_UPLOAD") is False

    monkeypatch.setenv("HANDOFF_FORGE_LIVE_ALLOW_CLOUD_UPLOAD", " TRUE ")
    assert _environment_flag("HANDOFF_FORGE_LIVE_ALLOW_CLOUD_UPLOAD") is True


@pytest.mark.live
def test_explicitly_selected_live_provider() -> None:
    provider_name = os.environ.get("HANDOFF_FORGE_LIVE_PROVIDER")
    model = os.environ.get("HANDOFF_FORGE_LIVE_MODEL")
    if not provider_name or not model:
        pytest.skip("set HANDOFF_FORGE_LIVE_PROVIDER and HANDOFF_FORGE_LIVE_MODEL to opt in")
    provider_name = provider_name.strip()
    model = model.strip()

    settings = HandoffSettings()
    if not settings.network_enabled:
        pytest.skip(
            "set HANDOFF_FORGE_ALLOW_NETWORK=true and HANDOFF_FORGE_OFFLINE=false to opt in"
        )
    if not _environment_flag("HANDOFF_FORGE_LIVE_ALLOW_CLOUD_UPLOAD"):
        pytest.skip("set HANDOFF_FORGE_LIVE_ALLOW_CLOUD_UPLOAD=true to consent to the canary")

    registry = build_default_registry(
        network_enabled=settings.network_enabled,
        timeout_seconds=_LIVE_PROVIDER_TIMEOUT_SECONDS,
        max_retries=0,
    )
    provider = registry.get(provider_name)
    if not provider.is_remote:
        pytest.fail("the live-provider canary requires a remote provider")
    status = provider.status()
    if not status.installed:
        pytest.skip(f"install the providers extra for {provider.name}")
    credential_names = tuple(getattr(provider, "credential_names", ()))
    if not credential_names or not any(os.environ.get(name) for name in credential_names):
        names = " or ".join(credential_names) or "the provider credential"
        pytest.skip(f"set {names} for the selected live provider")

    started = time.perf_counter()
    result = registry.generate(
        GenerationRequest(
            section_id=1,
            system_prompt=(
                "Return only one valid JSON object with exactly the requested keys and values. "
                "Do not use Markdown fences or add prose."
            ),
            user_prompt=(
                "Return exactly this JSON object: "
                '{"canary":"handoff-forge-live-v1","status":"ready"}'
            ),
            route=ModelRoute(
                provider=provider_name,
                model=model,
                allow_cloud_upload=True,
                include_visual_evidence=False,
                max_output_tokens=_LIVE_MAX_OUTPUT_TOKENS,
            ),
        )
    )
    elapsed_seconds = time.perf_counter() - started

    _parse_canary_response(result.text)
    assert elapsed_seconds <= _LIVE_LATENCY_LIMIT_SECONDS, (
        f"live provider exceeded {_LIVE_LATENCY_LIMIT_SECONDS}s latency limit"
    )
    assert result.provider == provider.name
    assert result.model == model
    print(_live_proof_json(result, elapsed_seconds=elapsed_seconds))
