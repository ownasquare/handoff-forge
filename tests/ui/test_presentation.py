"""Pure Streamlit presentation helper tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from handoff_forge.models import ConfidenceLevel
from handoff_forge.ui.presentation import (
    APP_CSS,
    confidence_chip,
    empty_state,
    format_bytes,
    journey_steps,
    metric_card,
    page_header,
    short_hash,
    status_badge,
    summary_strip,
    workspace_context,
)
from handoff_forge.ui.state import (
    clear_project_state,
    get_project_state,
    initialize_state,
    pop_flash,
    project_state_key,
    set_flash,
    set_project_state,
)

ROOT = Path(__file__).parents[2]


def test_streamlit_product_chrome_and_neutral_theme_configuration() -> None:
    with (ROOT / ".streamlit" / "config.toml").open("rb") as handle:
        config = tomllib.load(handle)

    assert config["client"]["toolbarMode"] == "minimal"
    assert config["theme"] == {
        "base": "light",
        "primaryColor": "#1D4ED8",
        "backgroundColor": "#F5F6F8",
        "secondaryBackgroundColor": "#F0F2F5",
        "textColor": "#1F2937",
        "font": "sans serif",
    }


def test_theme_uses_opaque_neutral_surfaces_and_responsive_summary_grid() -> None:
    assert "radial-gradient" not in APP_CSS
    assert "linear-gradient" not in APP_CSS
    assert "backdrop-filter" not in APP_CSS
    assert "--hf-primary: #1d4ed8" in APP_CSS
    assert "@media (prefers-color-scheme: dark)" not in APP_CSS
    assert '[data-testid^="stBaseButton-secondary"]' in APP_CSS
    assert "background: #ffffff" in APP_CSS
    assert "color: #344054" in APP_CSS
    assert ":disabled" in APP_CSS
    assert "background: var(--hf-surface-subtle) !important" in APP_CSS
    assert "color: var(--hf-muted) !important" in APP_CSS
    assert ":focus-visible" in APP_CSS
    assert ".hf-status[data-tooltip]:focus-visible::after" in APP_CSS
    assert '[data-testid="stRadioOption"] > div > div > div:first-child' not in APP_CSS
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in APP_CSS
    assert "min-height: 44px" in APP_CSS


def test_metric_card_escapes_untrusted_values() -> None:
    rendered = metric_card("Source", '<script>alert("x")</script>')
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "hf-card-value-compact" in rendered
    assert "hf-card-value-compact" not in metric_card("Sources", "3")


def test_page_header_and_workspace_context_escape_untrusted_values() -> None:
    rendered = page_header(
        "Sources <script>",
        'Review <img src=x onerror="alert(1)">',
        eyebrow="Local & private",
    )
    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "Sources &lt;script&gt;" in rendered
    assert "Local &amp; private" in rendered
    assert "hf-page-eyebrow" not in page_header("Home", "Continue where you left off.")

    context = workspace_context("A <workspace>", 'Sources "today"')
    assert "<workspace>" not in context
    assert "A &lt;workspace&gt;" in context
    assert "Sources &quot;today&quot;" in context
    assert "Handoff Forge" not in context


def test_journey_steps_are_compact_numbered_and_escape_untrusted_values() -> None:
    rendered = journey_steps(("Add <files>", "Create & check", "Start session"))

    assert rendered.count('class="hf-step"') == 3
    assert "Add &lt;files&gt;" in rendered
    assert "Create &amp; check" in rendered
    assert "<files>" not in rendered


def test_summary_status_and_empty_state_helpers_escape_untrusted_values() -> None:
    summary = summary_strip(
        (("Files <all>", "3 & ready"), ("Handoffs", '<script id="x">2</script>'))
    )
    assert summary.count("hf-summary-item") == 2
    assert "<all>" not in summary
    assert "<script" not in summary
    assert "Files &lt;all&gt;" in summary
    assert "3 &amp; ready" in summary

    badge = status_badge(
        "Local <only>",
        tone="success",
        description="Files stay here & are not sent to <providers>.",
    )
    assert "hf-status-success" in badge
    assert "Local &lt;only&gt;" in badge
    assert "Files stay here &amp; are not sent to &lt;providers&gt;." in badge
    assert "ⓘ" in badge
    assert 'tabindex="0"' in badge
    assert 'data-tooltip="Files stay here &amp; are not sent to &lt;providers&gt;."' in badge
    assert 'title="' not in badge
    assert "hf-status-neutral" in status_badge("Ready")
    with pytest.raises(ValueError, match="tone must be one of"):
        status_badge("Unknown", tone="glowing-violet")

    empty = empty_state("No files <yet>", "Add one & continue.")
    assert "No files &lt;yet&gt;" in empty
    assert "Add one &amp; continue." in empty


def test_confidence_and_format_helpers() -> None:
    assert "current evidence" in confidence_chip(ConfidenceLevel.HIGH)
    assert "older evidence" in confidence_chip(ConfidenceLevel.MEDIUM)
    assert "re-validation needed" in confidence_chip(ConfidenceLevel.LOW)
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(1024**2) == "1.0 MiB"
    assert format_bytes(1024**3) == "1.0 GiB"
    assert short_hash("1234567890abcdef") == "1234567890ab"
    assert short_hash("short") == "short"
    with pytest.raises(ValueError):
        format_bytes(-1)


def test_flash_state_round_trip() -> None:
    state: dict[str, object] = {}
    initialize_state(state)
    set_flash(state, "Saved", tone="success")
    assert pop_flash(state) == ("Saved", "success")
    assert pop_flash(state) is None


def test_project_state_isolated_and_cleared_without_touching_other_workspace() -> None:
    state: dict[str, object] = {}

    set_project_state(state, "project-a", "last_job_id", "job-a")
    set_project_state(state, "project-b", "last_job_id", "job-b")
    state["unrelated"] = "keep"

    assert get_project_state(state, "project-a", "last_job_id") == "job-a"
    assert get_project_state(state, "project-b", "last_job_id") == "job-b"
    assert project_state_key("project-a", "last_job_id") != project_state_key(
        "project-b", "last_job_id"
    )

    clear_project_state(state, "project-a")

    assert get_project_state(state, "project-a", "last_job_id") is None
    assert get_project_state(state, "project-b", "last_job_id") == "job-b"
    assert state["unrelated"] == "keep"
