"""Privacy-first configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from handoff_forge.config import HandoffSettings


def test_network_requires_both_explicit_flags(tmp_path) -> None:
    assert not HandoffSettings(data_root=tmp_path, offline=True, allow_network=True).network_enabled
    assert not HandoffSettings(
        data_root=tmp_path,
        offline=False,
        allow_network=False,
    ).network_enabled
    assert HandoffSettings(data_root=tmp_path, offline=False, allow_network=True).network_enabled


def test_limits_must_be_positive(tmp_path) -> None:
    with pytest.raises(ValidationError, match="positive"):
        HandoffSettings(data_root=tmp_path, max_pdf_pages=0)


def test_documented_data_dir_environment_alias_is_supported(tmp_path, monkeypatch) -> None:
    configured = tmp_path / "documented-data-dir"
    monkeypatch.setenv("HANDOFF_FORGE_DATA_DIR", str(configured))

    assert HandoffSettings().data_root == Path(configured).resolve()
