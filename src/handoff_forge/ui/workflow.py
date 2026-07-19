"""Pure workspace navigation and next-action helpers for the Streamlit UI."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    """A stable workspace destination and its user-facing copy."""

    key: str
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class RecommendedAction:
    """The next useful step derived from durable workspace counts."""

    view_key: str
    title: str
    description: str

    @property
    def key(self) -> str:
        """Return the destination key using the same name as ``WorkspaceView``."""

        return self.view_key


PRIMARY_WORKSPACE_VIEWS: Final[tuple[WorkspaceView, ...]] = (
    WorkspaceView("home", "Home", "Continue where you left off."),
    WorkspaceView("sources", "Files", "Add the files that explain the work."),
    WorkspaceView("create", "Create handoff", "Save progress or finish a task."),
    WorkspaceView("continue", "Start session", "Download or use a checked handoff."),
)

SECONDARY_WORKSPACE_VIEWS: Final[tuple[WorkspaceView, ...]] = (
    WorkspaceView("combine", "Combine handoffs", "Reconcile two or more handoffs."),
    WorkspaceView("settings", "Settings", "Local status, privacy, and workspace controls."),
)

WORKSPACE_VIEWS: Final[tuple[WorkspaceView, ...]] = (
    *PRIMARY_WORKSPACE_VIEWS,
    *SECONDARY_WORKSPACE_VIEWS,
)

VIEW_BY_KEY = MappingProxyType({view.key: view for view in WORKSPACE_VIEWS})

_LEGACY_VIEW_ALIASES: Final[dict[str, str]] = {
    "library": "sources",
    "sources": "sources",
    "compose": "create",
    "merge": "combine",
    "combine": "combine",
    "continue": "continue",
    "diagnostics": "settings",
}

_VIEW_ALIASES: Final[dict[str, str]] = {
    **{view.key.casefold(): view.key for view in WORKSPACE_VIEWS},
    **{view.label.casefold(): view.key for view in WORKSPACE_VIEWS},
    **_LEGACY_VIEW_ALIASES,
}


def resolve_view_key(value: str | None) -> str:
    """Resolve current keys, visible labels, and Phase 1 labels to a stable key.

    Unknown or missing session-state values safely return Home. This makes old
    and partially cleared Streamlit sessions recover without a separate state
    migration.
    """

    if value is None:
        return "home"
    return _VIEW_ALIASES.get(value.strip().casefold(), "home")


def view_label(key: str | None) -> str:
    """Return the visible label for a stable key or supported legacy alias."""

    return VIEW_BY_KEY[resolve_view_key(key)].label


def recommend_next_action(artifact_count: int, output_count: int) -> RecommendedAction:
    """Choose the normal local journey from persisted file and handoff counts."""

    if artifact_count < 0 or output_count < 0:
        raise ValueError("workspace counts cannot be negative")
    if artifact_count == 0:
        return RecommendedAction("sources", "Add files", "Start with Markdown, MDC, or PDF.")
    if output_count == 0:
        return RecommendedAction(
            "create",
            "Create a handoff",
            "Turn your files into a continuation package.",
        )
    return RecommendedAction(
        "continue",
        "Start a session",
        "Download your latest checked handoff or use it in an installed coding app.",
    )


__all__ = [
    "PRIMARY_WORKSPACE_VIEWS",
    "SECONDARY_WORKSPACE_VIEWS",
    "VIEW_BY_KEY",
    "WORKSPACE_VIEWS",
    "RecommendedAction",
    "WorkspaceView",
    "recommend_next_action",
    "resolve_view_key",
    "view_label",
]
