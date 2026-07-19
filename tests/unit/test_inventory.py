from __future__ import annotations

from handoff_forge.handoffs.inventory import (
    inventory_scan_evidence,
    inventory_scan_marker,
    scan_section_ten_inventory,
)
from handoff_forge.models import BlockKind, ContentBlock


def _block(block_id: str, text: str, order: int) -> ContentBlock:
    return ContentBlock(
        id=block_id,
        project_id="project-1",
        artifact_id=f"artifact-{block_id}",
        artifact_sha256=(block_id[-1] * 64)[:64],
        kind=BlockKind.TEXT,
        text=text,
        order=order,
        extraction_method="fixture",
    )


def test_inventory_scan_builds_full_deduplicated_conservative_items() -> None:
    evidence = [
        _block(
            "block-1",
            "- [P1] Finish the deterministic merge workflow.\n"
            "- Completed the parser refactor yesterday.",
            2,
        ),
        _block(
            "block-2",
            "TODO: Finish the deterministic merge workflow.\nInvestigate the remaining retry race.",
            1,
        ),
    ]

    result = scan_section_ten_inventory(evidence)

    assert len(result.items) == 2
    merge = next(item for item in result.items if "merge workflow" in item.what)
    retry = next(item for item in result.items if "retry race" in item.what)
    assert merge.priority == "P1"
    assert len(merge.source_refs) == 2
    assert retry.priority == "P2"
    assert "No explicit severity" in retry.priority_rationale
    for item in result.items:
        assert item.who
        assert item.where
        assert item.when
        assert item.description
        assert item.acceptance_criteria
        assert item.definition_of_done
        assert item.root_cause
        assert item.regression_prevention
        assert item.testing
        assert item.audit_policies
        assert item.adjacent_considerations
        assert item.source_refs


def test_inventory_scan_is_bounded_and_marker_round_trips() -> None:
    evidence = [
        _block(f"block-{index}", f"Fix pending issue {index}.", index) for index in range(25)
    ]

    result = scan_section_ten_inventory(evidence, max_items=5)
    marker = inventory_scan_marker(result.evidence_scanned)

    assert len(result.items) == 5
    assert inventory_scan_evidence(["unrelated", marker]) == result.evidence_scanned


def test_empty_real_scan_retains_truthful_evidence_declaration() -> None:
    result = scan_section_ten_inventory([])

    assert result.items == ()
    assert result.evidence_scanned == ("Section 10 selected evidence set (0 blocks supplied)",)


def test_inventory_form_uses_what_without_promoting_nested_checklist_lines() -> None:
    evidence = [
        _block(
            "block-1",
            "### inventory-existing: Fix the primary backlog item\n\n"
            "- **What:** Fix the primary backlog item.\n"
            "- **Acceptance criteria:**\n"
            "  - Implement a nested verification helper.\n"
            "- **Testing:**\n"
            "  - Run the focused suite.",
            1,
        )
    ]

    result = scan_section_ten_inventory(evidence)

    assert [item.what for item in result.items] == ["Fix the primary backlog item"]
