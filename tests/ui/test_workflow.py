"""Tests for the pure workspace navigation model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from handoff_forge.ui.workflow import (
    PRIMARY_WORKSPACE_VIEWS,
    SECONDARY_WORKSPACE_VIEWS,
    VIEW_BY_KEY,
    WORKSPACE_VIEWS,
    RecommendedAction,
    WorkspaceView,
    recommend_next_action,
    resolve_view_key,
    view_label,
)


def test_workspace_view_catalog_has_stable_ordered_keys_and_copy() -> None:
    assert tuple(view.key for view in WORKSPACE_VIEWS) == (
        "home",
        "sources",
        "create",
        "continue",
        "combine",
        "settings",
    )
    assert tuple(view.label for view in WORKSPACE_VIEWS) == (
        "Home",
        "Files",
        "Create handoff",
        "Start session",
        "Combine handoffs",
        "Settings",
    )
    assert tuple(view.key for view in PRIMARY_WORKSPACE_VIEWS) == (
        "home",
        "sources",
        "create",
        "continue",
    )
    assert tuple(view.key for view in SECONDARY_WORKSPACE_VIEWS) == ("combine", "settings")
    assert tuple(VIEW_BY_KEY) == tuple(view.key for view in WORKSPACE_VIEWS)
    assert all(view.description for view in WORKSPACE_VIEWS)


def test_workspace_models_are_immutable() -> None:
    view = WorkspaceView("example", "Example", "Example description")
    action = RecommendedAction("sources", "Add your files", "Start locally.")

    with pytest.raises(FrozenInstanceError):
        view.label = "Changed"
    with pytest.raises(FrozenInstanceError):
        action.title = "Changed"

    assert action.key == "sources"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Library", "sources"),
        ("Compose", "create"),
        ("Merge", "combine"),
        ("Continue", "continue"),
        ("Start session", "continue"),
        ("Combine handoffs", "combine"),
        ("Diagnostics", "settings"),
        ("Create handoff", "create"),
        ("  SOURCES  ", "sources"),
        ("settings", "settings"),
    ],
)
def test_resolve_view_key_accepts_current_labels_and_legacy_aliases(
    value: str,
    expected: str,
) -> None:
    assert resolve_view_key(value) == expected


@pytest.mark.parametrize("value", [None, "", "unknown view"])
def test_resolve_view_key_recovers_invalid_session_state_to_home(value: str | None) -> None:
    assert resolve_view_key(value) == "home"


def test_view_label_uses_resolved_stable_key() -> None:
    assert view_label("home") == "Home"
    assert view_label("Library") == "Files"
    assert view_label("Continue") == "Start session"
    assert view_label("not-a-view") == "Home"


@pytest.mark.parametrize(
    ("artifact_count", "output_count", "expected"),
    [
        (0, 0, RecommendedAction("sources", "Add files", "Start with Markdown, MDC, or PDF.")),
        (0, 4, RecommendedAction("sources", "Add files", "Start with Markdown, MDC, or PDF.")),
        (
            3,
            0,
            RecommendedAction(
                "create",
                "Create a handoff",
                "Turn your files into a continuation package.",
            ),
        ),
        (
            3,
            2,
            RecommendedAction(
                "continue",
                "Start a session",
                "Download your latest checked handoff or use it in an installed coding app.",
            ),
        ),
    ],
)
def test_recommend_next_action_uses_only_durable_summary_counts(
    artifact_count: int,
    output_count: int,
    expected: RecommendedAction,
) -> None:
    assert recommend_next_action(artifact_count, output_count) == expected


@pytest.mark.parametrize(("artifact_count", "output_count"), [(-1, 0), (0, -1)])
def test_recommend_next_action_rejects_invalid_counts(
    artifact_count: int,
    output_count: int,
) -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        recommend_next_action(artifact_count, output_count)
