from __future__ import annotations

from pathlib import Path

from handoff_forge.handoffs.validator import validate_handoff
from handoff_forge.merge.engine import MergeEngine
from handoff_forge.merge.planner import render_merged_handoff
from handoff_forge.models import TemplateProfile

ROOT = Path(__file__).parents[2]


def test_example_handoffs_merge_into_a_valid_launchable_plan() -> None:
    merged = MergeEngine().merge_files(
        [
            ROOT / "examples/handoffs/project-alpha.mdc",
            ROOT / "examples/handoffs/project-beta.mdc",
        ],
        target_profile=TemplateProfile.GOAL_V1,
    )
    rendered = render_merged_handoff(merged)
    handoff_text, unified_plan = rendered.split("\n## Unified Execution Plan", 1)

    assert validate_handoff(handoff_text, TemplateProfile.GOAL_V1).valid
    assert "### Immediate task" in unified_plan
    assert "### Validation gates" in unified_plan
    assert "project-alpha.mdc" in unified_plan
    assert "project-beta.mdc" in unified_plan
    assert len(merged.source_hashes) == 2
    assert {source.display_name for source in merged.package.sources} == {
        "project-alpha.mdc",
        "project-beta.mdc",
    }
    assert all(source.metadata["role"] == "merge-input" for source in merged.package.sources)
