"""Credential-safe diagnostics tests."""

from __future__ import annotations

from handoff_forge.diagnostics import diagnostics_ready, run_diagnostics


def test_required_diagnostics_are_ready_without_provider_keys(settings) -> None:
    checks = run_diagnostics(settings)
    by_name = {check.name: check for check in checks}
    assert diagnostics_ready(checks)
    assert by_name["network-policy"].detail == "offline; outbound providers disabled"
    assert by_name["data-root"].status == "ready"


def test_diagnostics_never_contain_secret_values(settings, monkeypatch) -> None:
    canary = "provider-secret-canary-value"
    monkeypatch.setenv("OPENAI_API_KEY", canary)
    rendered = repr(run_diagnostics(settings))
    assert canary not in rendered
    assert "OPENAI_API_KEY" not in rendered
