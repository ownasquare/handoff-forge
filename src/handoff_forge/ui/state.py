"""Small Streamlit state helpers; durable product state remains in storage."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

DEFAULT_STATE: dict[str, Any] = {
    "active_view": "home",
    "active_project_id": None,
    "selected_artifact_id": None,
    "selected_output_paths": [],
    "flash_message": None,
    "flash_tone": "info",
}

_PROJECT_STATE_PREFIX = "_project_state:"


def initialize_state(state: MutableMapping[str, Any]) -> None:
    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = list(value) if isinstance(value, list) else value


def set_flash(state: MutableMapping[str, Any], message: str, *, tone: str = "info") -> None:
    state["flash_message"] = message
    state["flash_tone"] = tone


def pop_flash(state: MutableMapping[str, Any]) -> tuple[str, str] | None:
    message = state.get("flash_message")
    if not message:
        return None
    tone = str(state.get("flash_tone") or "info")
    state["flash_message"] = None
    return str(message), tone


def project_state_key(project_id: str, name: str) -> str:
    """Return a stable key for transient UI state owned by one workspace."""

    normalized_project_id = project_id.strip()
    normalized_name = name.strip()
    if not normalized_project_id:
        raise ValueError("project_id cannot be empty")
    if not normalized_name:
        raise ValueError("project state name cannot be empty")
    return f"{_PROJECT_STATE_PREFIX}{normalized_project_id}:{normalized_name}"


def get_project_state(
    state: MutableMapping[str, Any],
    project_id: str,
    name: str,
    default: Any = None,
) -> Any:
    """Read one workspace's transient value without leaking across workspaces."""

    return state.get(project_state_key(project_id, name), default)


def set_project_state(
    state: MutableMapping[str, Any],
    project_id: str,
    name: str,
    value: Any,
) -> None:
    """Store one workspace's transient value."""

    state[project_state_key(project_id, name)] = value


def pop_project_state(
    state: MutableMapping[str, Any],
    project_id: str,
    name: str,
    default: Any = None,
) -> Any:
    """Remove and return one workspace's transient value."""

    return state.pop(project_state_key(project_id, name), default)


def clear_project_state(state: MutableMapping[str, Any], project_id: str) -> None:
    """Remove only transient state owned by the selected workspace."""

    prefix = f"{_PROJECT_STATE_PREFIX}{project_id.strip()}:"
    if prefix == f"{_PROJECT_STATE_PREFIX}:":
        raise ValueError("project_id cannot be empty")
    for key in tuple(state):
        if isinstance(key, str) and key.startswith(prefix):
            state.pop(key, None)


__all__ = [
    "clear_project_state",
    "get_project_state",
    "initialize_state",
    "pop_flash",
    "pop_project_state",
    "project_state_key",
    "set_flash",
    "set_project_state",
]
