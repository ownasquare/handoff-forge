"""Trusted presentation helpers for the Streamlit workbench."""

from __future__ import annotations

import html
from collections.abc import Sequence

import streamlit as st

from handoff_forge.models import ConfidenceLevel

APP_CSS = """
<style>
:root {
  color-scheme: light;
  --hf-bg: #f5f6f8;
  --hf-surface: #ffffff;
  --hf-surface-subtle: #f8f9fb;
  --hf-sidebar: #f0f2f5;
  --hf-text: #1f2937;
  --hf-muted: #667085;
  --hf-border: #d8dde5;
  --hf-primary: #1d4ed8;
  --hf-primary-hover: #1e40af;
  --hf-primary-soft: #eff6ff;
  --hf-success: #067647;
  --hf-success-soft: #ecfdf3;
  --hf-warning: #b54708;
  --hf-warning-soft: #fffaeb;
  --hf-danger: #b42318;
  --hf-danger-soft: #fef3f2;
  --hf-focus: rgba(29, 78, 216, 0.28);
  --hf-shadow: 0 1px 2px rgba(16, 24, 40, 0.05);
}

[data-testid="stAppViewContainer"] {
  background: var(--hf-bg);
  color: var(--hf-text);
}
[data-testid="stHeader"] {
  background: var(--hf-bg);
}
[data-testid="stSidebar"] {
  background: var(--hf-sidebar);
  border-right: 1px solid var(--hf-border);
  min-width: 244px;
  max-width: 244px;
}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
  padding-top: 1rem;
}
.hf-sidebar-brand {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: 0.15rem 0 0.85rem;
}
.hf-sidebar-brand strong {
  color: var(--hf-text);
  font-size: 1rem;
  font-weight: 750;
  line-height: 1.3;
}
.hf-sidebar-brand span {
  color: var(--hf-muted);
  font-size: 0.78rem;
  line-height: 1.4;
}
.block-container {
  max-width: 1040px;
  padding-top: 3.5rem;
  padding-bottom: 4rem;
}
h1, h2, h3 {
  color: var(--hf-text);
  letter-spacing: -0.025em;
}
p, label, [data-testid="stCaptionContainer"] {
  color: var(--hf-muted);
}

.hf-workspace-context {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.4rem;
  min-height: 28px;
  margin: 0 0 1.25rem;
  color: var(--hf-muted);
  font-size: 0.82rem;
  line-height: 1.4;
}
.hf-workspace-context .hf-context-project {
  color: var(--hf-text);
  font-weight: 600;
}
.hf-workspace-context .hf-context-view {
  border: 1px solid var(--hf-border);
  border-radius: 999px;
  background: var(--hf-surface);
  padding: 0.18rem 0.5rem;
  color: var(--hf-muted);
  font-weight: 600;
}
.hf-context-divider {
  color: var(--hf-border);
  user-select: none;
}

.hf-page-header {
  max-width: 760px;
  margin: 0 0 1.35rem;
}
.hf-page-eyebrow {
  margin-bottom: 0.35rem;
  color: var(--hf-primary);
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.hf-page-header h1 {
  margin: 0;
  font-size: clamp(1.75rem, 4vw, 2.25rem);
  line-height: 1.15;
}
.hf-page-header p {
  margin: 0.55rem 0 0;
  max-width: 680px;
  color: var(--hf-muted);
  font-size: 0.98rem;
  line-height: 1.6;
}

.hf-journey {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.6rem;
  margin: 0 0 1.35rem;
  padding: 0;
  list-style: none;
}
.hf-step {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  min-width: 0;
  color: var(--hf-muted);
  font-size: 0.86rem;
  font-weight: 650;
}
.hf-step-number {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 1.65rem;
  height: 1.65rem;
  border: 1px solid var(--hf-border);
  border-radius: 999px;
  background: var(--hf-surface);
  color: var(--hf-text);
  font-size: 0.78rem;
}
.hf-definition {
  margin: 0 0 1.1rem;
  color: var(--hf-muted);
  font-size: 0.92rem;
  line-height: 1.55;
}
.hf-definition strong {
  color: var(--hf-text);
}

.hf-summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  margin: 0 0 1.25rem;
  border: 1px solid var(--hf-border);
  border-radius: 10px;
  background: var(--hf-surface);
  box-shadow: var(--hf-shadow);
}
.hf-summary-item {
  min-width: 0;
  padding: 0.85rem 1rem;
}
.hf-summary-item + .hf-summary-item {
  border-left: 1px solid var(--hf-border);
}
.hf-summary-item span {
  display: block;
  overflow: hidden;
  color: var(--hf-muted);
  font-size: 0.76rem;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hf-summary-item strong {
  display: block;
  overflow-wrap: anywhere;
  margin-top: 0.2rem;
  color: var(--hf-text);
  font-size: 1.18rem;
  font-weight: 700;
  line-height: 1.35;
}

.hf-status {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0.2rem 0.55rem;
  border: 1px solid var(--hf-border);
  border-radius: 999px;
  background: var(--hf-surface-subtle);
  color: var(--hf-muted);
  font-size: 0.76rem;
  font-weight: 650;
  line-height: 1.2;
}
.hf-status[data-tooltip] {
  position: relative;
  cursor: help;
}
.hf-status[data-tooltip]::after {
  position: absolute;
  top: calc(100% + 0.4rem);
  left: 0;
  z-index: 20;
  width: max-content;
  max-width: min(240px, calc(100vw - 2rem));
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--hf-border);
  border-radius: 7px;
  background: var(--hf-text);
  box-shadow: var(--hf-shadow);
  color: var(--hf-surface);
  content: attr(data-tooltip);
  font-size: 0.72rem;
  font-weight: 500;
  line-height: 1.4;
  opacity: 0;
  pointer-events: none;
  transform: translateY(-2px);
  transition: opacity 120ms ease, transform 120ms ease;
  visibility: hidden;
  white-space: normal;
}
.hf-status[data-tooltip]:hover::after,
.hf-status[data-tooltip]:focus-visible::after {
  opacity: 1;
  transform: translateY(0);
  visibility: visible;
}
.hf-status[data-tooltip]:focus-visible {
  outline: 3px solid var(--hf-focus);
  outline-offset: 2px;
}
.hf-status-help {
  margin-left: 0.3rem;
  font-size: 0.74rem;
  opacity: 0.8;
}
.hf-status-neutral {
  border-color: var(--hf-border);
  background: var(--hf-surface-subtle);
  color: var(--hf-muted);
}
.hf-status-info {
  border-color: #b2ccff;
  background: var(--hf-primary-soft);
  color: var(--hf-primary);
}
.hf-status-success {
  border-color: #abefc6;
  background: var(--hf-success-soft);
  color: var(--hf-success);
}
.hf-status-warning {
  border-color: #fedf89;
  background: var(--hf-warning-soft);
  color: var(--hf-warning);
}
.hf-status-danger {
  border-color: #fecdca;
  background: var(--hf-danger-soft);
  color: var(--hf-danger);
}

.hf-empty-state {
  margin: 1rem 0;
  padding: clamp(1.5rem, 5vw, 2.5rem);
  border: 1px solid var(--hf-border);
  border-radius: 10px;
  background: var(--hf-surface);
  text-align: center;
  box-shadow: var(--hf-shadow);
}
.hf-empty-state h2 {
  margin: 0;
  font-size: 1.25rem;
}
.hf-empty-state p {
  max-width: 520px;
  margin: 0.55rem auto 0;
  color: var(--hf-muted);
  line-height: 1.55;
}

.hf-card {
  min-height: 96px;
  padding: 0.9rem 1rem;
  border: 1px solid var(--hf-border);
  border-radius: 10px;
  background: var(--hf-surface);
  box-shadow: var(--hf-shadow);
}
.hf-card-label {
  color: var(--hf-muted);
  font-size: 0.76rem;
  line-height: 1.35;
}
.hf-card-value {
  overflow-wrap: break-word;
  margin-top: 0.3rem;
  color: var(--hf-text);
  font-size: 1.4rem;
  font-weight: 700;
  line-height: 1.35;
  word-break: normal;
}
.hf-card-value-compact {
  font-size: 1rem;
}
.hf-chip {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0.2rem 0.55rem;
  border: 1px solid var(--hf-border);
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 650;
}
.hf-chip-ready {
  border-color: #abefc6;
  background: var(--hf-success-soft);
  color: var(--hf-success);
}
.hf-chip-warn {
  border-color: #fedf89;
  background: var(--hf-warning-soft);
  color: var(--hf-warning);
}
.hf-chip-low {
  border-color: #fecdca;
  background: var(--hf-danger-soft);
  color: var(--hf-danger);
}
.hf-source {
  overflow-wrap: anywhere;
  color: var(--hf-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.76rem;
}

[data-testid="stFileUploader"] {
  padding: 0.25rem;
  border: 1px dashed var(--hf-border);
  border-radius: 10px;
  background: var(--hf-surface);
}
[data-testid="stFileUploader"] button,
[data-testid="stFileUploader"] button p {
  border-color: #d0d5dd;
  background: #ffffff;
  color: #344054 !important;
}
[data-testid="stExpander"] {
  overflow: hidden;
  border-color: var(--hf-border);
  border-radius: 10px;
  background: var(--hf-surface);
}
[data-testid="stSidebar"] [data-testid="stRadioOption"] {
  min-height: 44px;
  padding: 0.45rem 0.55rem;
  border-left: 3px solid transparent;
  border-radius: 8px;
  color: var(--hf-muted);
}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div,
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div {
  width: 100%;
}
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover {
  background: var(--hf-surface);
}
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {
  border-left-color: var(--hf-primary);
  background: var(--hf-surface);
  color: var(--hf-text);
  font-weight: 700;
  box-shadow: var(--hf-shadow);
}
.stButton > button,
.stDownloadButton > button,
[data-testid="stFormSubmitButton"] > button {
  min-height: 44px;
  border-radius: 8px;
  font-weight: 650;
}
[data-testid^="stBaseButton-secondary"],
[data-testid="stDownloadButton"] button {
  border: 1px solid #d0d5dd !important;
  background: #ffffff !important;
  color: #344054 !important;
  box-shadow: var(--hf-shadow);
}
[data-testid^="stBaseButton-secondary"]:hover,
[data-testid="stDownloadButton"] button:hover {
  border-color: #98a2b3 !important;
  background: #f8f9fb !important;
  color: #1f2937 !important;
}
[data-testid^="stBaseButton-primary"] {
  border-color: var(--hf-primary) !important;
  background: var(--hf-primary) !important;
  color: #ffffff !important;
}
[data-testid^="stBaseButton-primary"]:hover {
  border-color: var(--hf-primary-hover) !important;
  background: var(--hf-primary-hover) !important;
  color: #ffffff !important;
}
.stButton > button p,
.stDownloadButton > button p,
[data-testid="stFormSubmitButton"] > button p,
[data-testid="stFileUploader"] button p {
  color: inherit;
}
.stButton > button:disabled,
.stDownloadButton > button:disabled,
[data-testid="stFormSubmitButton"] > button:disabled,
[data-testid^="stBaseButton-secondary"]:disabled,
[data-testid^="stBaseButton-primary"]:disabled {
  border-color: var(--hf-border) !important;
  background: var(--hf-surface-subtle) !important;
  color: var(--hf-muted) !important;
  opacity: 0.82;
}
.stButton > button:focus-visible,
.stDownloadButton > button:focus-visible,
[data-testid="stFormSubmitButton"] > button:focus-visible,
[data-testid="stSidebar"] [data-testid="stRadioOption"]:focus-within {
  outline: 3px solid var(--hf-focus);
  outline-offset: 2px;
}

@media (max-width: 680px) {
  .block-container {
    padding: 3.5rem 0.85rem 3rem;
  }
  .hf-workspace-context {
    margin-bottom: 1rem;
  }
  .hf-page-header {
    margin-bottom: 1.1rem;
  }
  .hf-summary-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .hf-summary-item:nth-child(odd) {
    border-left: 0;
  }
  .hf-summary-item:nth-child(n + 3) {
    border-top: 1px solid var(--hf-border);
  }
  .hf-card {
    min-height: auto;
  }
  .hf-journey {
    grid-template-columns: 1fr;
    gap: 0.45rem;
  }
}
</style>
"""

_STATUS_TONES = frozenset({"neutral", "info", "success", "warning", "danger"})


def apply_theme() -> None:
    """Render the shared workspace stylesheet once per Streamlit run."""
    st.markdown(APP_CSS, unsafe_allow_html=True)


def page_header(title: str, description: str, *, eyebrow: str | None = None) -> str:
    """Build a compact page heading from escaped, user-visible values."""
    eyebrow_markup = f'<div class="hf-page-eyebrow">{html.escape(eyebrow)}</div>' if eyebrow else ""
    return (
        '<header class="hf-page-header">'
        f"{eyebrow_markup}"
        f"<h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(description)}</p>"
        "</header>"
    )


def workspace_context(project_name: str, view_label: str) -> str:
    """Build the compact workspace and view breadcrumb."""
    return (
        '<div class="hf-workspace-context" aria-label="Current workspace">'
        f'<span class="hf-context-project">{html.escape(project_name)}</span>'
        '<span class="hf-context-divider" aria-hidden="true">/</span>'
        f'<span class="hf-context-view">{html.escape(view_label)}</span>'
        "</div>"
    )


def summary_strip(items: Sequence[tuple[str, str]]) -> str:
    """Build a responsive strip of escaped summary labels and values."""
    cells = "".join(
        f'<div class="hf-summary-item"><span>{html.escape(label)}</span>'
        f"<strong>{html.escape(value)}</strong></div>"
        for label, value in items
    )
    return f'<div class="hf-summary-strip">{cells}</div>'


def status_badge(label: str, tone: str = "neutral", *, description: str | None = None) -> str:
    """Build a safe status badge from a fixed semantic tone set."""
    if tone not in _STATUS_TONES:
        allowed = ", ".join(sorted(_STATUS_TONES))
        raise ValueError(f"tone must be one of: {allowed}")
    tooltip = ""
    help_icon = ""
    if description:
        escaped_description = html.escape(description, quote=True)
        escaped_label = html.escape(label, quote=True)
        tooltip = (
            f' tabindex="0" role="note" data-tooltip="{escaped_description}"'
            f' aria-label="{escaped_label}: {escaped_description}"'
        )
        help_icon = '<span class="hf-status-help" aria-hidden="true">ⓘ</span>'
    return (
        f'<span class="hf-status hf-status-{tone}"{tooltip}>{html.escape(label)}{help_icon}</span>'
    )


def journey_steps(labels: Sequence[str]) -> str:
    """Build a compact ordered journey from escaped labels."""
    if not labels:
        raise ValueError("journey steps cannot be empty")
    steps = "".join(
        '<li class="hf-step">'
        f'<span class="hf-step-number" aria-hidden="true">{index}</span>'
        f"<span>{html.escape(label)}</span>"
        "</li>"
        for index, label in enumerate(labels, start=1)
    )
    return f'<ol class="hf-journey" aria-label="How Handoff Forge works">{steps}</ol>'


def empty_state(title: str, description: str) -> str:
    """Build a calm empty-state panel that leaves actions to Streamlit widgets."""
    return (
        '<section class="hf-empty-state">'
        f"<h2>{html.escape(title)}</h2>"
        f"<p>{html.escape(description)}</p>"
        "</section>"
    )


def metric_card(label: str, value: str) -> str:
    """Build the Phase 1 metric card with escaped content."""
    value_class = "hf-card-value hf-card-value-compact" if len(value) > 8 else "hf-card-value"
    return (
        '<div class="hf-card">'
        f'<div class="hf-card-label">{html.escape(label)}</div>'
        f'<div class="{value_class}">{html.escape(value)}</div>'
        "</div>"
    )


def confidence_chip(level: ConfidenceLevel) -> str:
    """Build the existing confidence indicator with the neutral palette."""
    styles = {
        ConfidenceLevel.HIGH: ("hf-chip-ready", "High - current evidence"),
        ConfidenceLevel.MEDIUM: ("hf-chip-warn", "Medium - older evidence"),
        ConfidenceLevel.LOW: ("hf-chip-low", "Low - re-validation needed"),
    }
    css_class, label = styles[level]
    return f'<span class="hf-chip {css_class}">{label}</span>'


def format_bytes(size: int) -> str:
    """Format a non-negative byte count for display."""
    if size < 0:
        raise ValueError("size cannot be negative")
    units = ("B", "KiB", "MiB", "GiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unit loop must return")


def short_hash(value: str) -> str:
    """Return a compact hash prefix for non-security display uses."""
    return value[:12] if len(value) > 12 else value
