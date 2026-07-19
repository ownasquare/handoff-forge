"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from handoff_forge.config import HandoffSettings


@pytest.fixture
def settings(tmp_path: Path) -> HandoffSettings:
    return HandoffSettings(data_root=tmp_path / "handoff-data", offline=True, allow_network=False)
