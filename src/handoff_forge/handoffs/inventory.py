"""Deterministic, conservative inventory discovery for Section 10 evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from handoff_forge.models import ContentBlock, InventoryItem, SourceArtifact

MAX_INVENTORY_ITEMS = 20
INVENTORY_SCAN_MARKER_PREFIX = "inventory-scan-v1:"

_PRIORITY_RE = re.compile(r"(?:^|[\s\[(])P([0-4])(?:$|[\s\]):.\-])", re.IGNORECASE)
_MARKDOWN_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|\[[ xX]\]\s+)+")
_LABEL_PREFIX_RE = re.compile(
    r"^(?:todo|next(?:\s+task|\s+step)?|action(?:\s+item)?|backlog|pending|"
    r"remaining|follow[- ]?up|blocker)\s*[:\-]\s*",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"^(?:(?:we|the next session|the next agent)\s+(?:must|should|needs?\s+to)\s+|"
    r"(?:must|should|needs?\s+to)\s+)?"
    r"(?:add|build|complete|configure|create|deploy|document|ensure|finish|fix|harden|"
    r"implement|integrate|investigate|migrate|publish|release|remove|replace|resolve|"
    r"review|run|test|update|validate|verify)\b",
    re.IGNORECASE,
)
_INVENTORY_FIELD_LINE_RE = re.compile(r"^\s*-\s+\*\*(.+?):\*\*\s*(.*)$")
_NON_ACTION_VALUES = {
    "none",
    "none known",
    "no new items",
    "no new items found",
    "no next steps",
    "not applicable",
    "unknown",
    "needs re-validation",
    "needs revalidation",
}
_FIELD_LABELS = {
    "acceptance criteria",
    "audit policies",
    "definition of done",
    "detailed description",
    "how discovered",
    "other considerations",
    "priority",
    "priority rationale",
    "regression prevention",
    "root cause",
    "source references",
    "testing",
    "what",
    "when",
    "where",
    "who",
}


@dataclass(frozen=True, slots=True)
class InventoryScanResult:
    """Full inventory records plus an auditable declaration of what was scanned."""

    items: tuple[InventoryItem, ...]
    evidence_scanned: tuple[str, ...]


def scan_section_ten_inventory(
    evidence: Sequence[ContentBlock],
    *,
    sources: Sequence[SourceArtifact] = (),
    existing: Sequence[InventoryItem] = (),
    max_items: int = MAX_INVENTORY_ITEMS,
    selection_notes: Sequence[str] = (),
) -> InventoryScanResult:
    """Extract bounded actionable lines without inventing severity or completion.

    Evidence is expected to have already passed the Section 10 character/block selector.
    This function applies a second item-count bound and stable normalized de-duplication.
    """

    if max_items < 1:
        raise ValueError("max_items must be positive")
    ordered = sorted(evidence, key=lambda block: (block.order, block.artifact_id, block.id))
    display_names = {source.id: source.display_name for source in sources}
    scanned = _evidence_scan_labels(ordered, display_names)
    scanned.extend(_one_line(note) for note in selection_notes if _one_line(note))
    if not scanned:
        scanned.append("Section 10 selected evidence set (0 blocks supplied)")

    items_by_key: dict[str, InventoryItem] = {}
    order: list[str] = []
    for item in existing:
        key = _deduplication_key(item.what)
        if not key:
            continue
        if key in items_by_key:
            items_by_key[key] = _merge_source_refs(items_by_key[key], item.source_refs)
            continue
        if len(order) >= max_items:
            break
        items_by_key[key] = item.model_copy(deep=True)
        order.append(key)
        scanned.append(f"Existing inventory item {item.id}")

    for block in ordered:
        source_ref = _source_ref(block, display_names)
        for raw_line in _candidate_lines(block.text):
            candidate = _actionable_candidate(raw_line)
            if candidate is None:
                continue
            what, priority, explicit_priority = candidate
            key = _deduplication_key(what)
            if not key:
                continue
            if key in items_by_key:
                merged = _merge_source_refs(items_by_key[key], [source_ref])
                if explicit_priority and merged.priority_rationale.startswith(
                    "No explicit severity was verified"
                ):
                    merged = merged.model_copy(
                        update={
                            "priority": priority,
                            "priority_rationale": _priority_rationale(
                                priority,
                                explicit=True,
                            ),
                        },
                        deep=True,
                    )
                items_by_key[key] = merged
                continue
            if len(order) >= max_items:
                break
            item = _inventory_item(
                what=what,
                priority=priority,
                explicit_priority=explicit_priority,
                source_ref=source_ref,
            )
            items_by_key[key] = item
            order.append(key)
        if len(order) >= max_items:
            break

    return InventoryScanResult(
        items=tuple(items_by_key[key] for key in order),
        evidence_scanned=tuple(_deduplicate(scanned)),
    )


def _candidate_lines(text: str) -> Iterable[str]:
    skip_nested_values = False
    for raw_line in text.splitlines():
        field = _INVENTORY_FIELD_LINE_RE.match(raw_line)
        if field:
            label = field.group(1).strip().casefold()
            value = field.group(2).strip()
            skip_nested_values = label in _FIELD_LABELS and not value
            if label == "what" and value:
                yield value
            continue
        stripped = raw_line.lstrip()
        is_nested_bullet = (
            len(raw_line) > len(stripped) and re.match(r"[-*+]\s+", stripped) is not None
        )
        if skip_nested_values and is_nested_bullet:
            continue
        if raw_line and len(raw_line) == len(stripped):
            skip_nested_values = False
        yield raw_line


def inventory_scan_marker(evidence_scanned: Sequence[str]) -> str:
    """Serialize scan evidence into an internal package boundary marker."""

    normalized = _deduplicate(_one_line(item) for item in evidence_scanned if _one_line(item))
    if not normalized:
        raise ValueError("inventory scan evidence cannot be empty")
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return f"{INVENTORY_SCAN_MARKER_PREFIX}{payload}"


def inventory_scan_evidence(boundaries: Iterable[str]) -> tuple[str, ...]:
    """Read the first valid internal inventory-scan marker from package boundaries."""

    for boundary in boundaries:
        if not boundary.startswith(INVENTORY_SCAN_MARKER_PREFIX):
            continue
        try:
            decoded = json.loads(boundary.removeprefix(INVENTORY_SCAN_MARKER_PREFIX))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(decoded, list):
            continue
        values = tuple(
            _one_line(value) for value in decoded if isinstance(value, str) and _one_line(value)
        )
        if values:
            return values
    return ()


def _actionable_candidate(
    raw_line: str,
) -> tuple[str, Literal["P0", "P1", "P2", "P3", "P4"], bool] | None:
    line = _MARKDOWN_PREFIX_RE.sub("", raw_line).strip()
    line = line.strip("*_ ")
    if not line:
        return None
    priority_match = _PRIORITY_RE.search(line)
    explicit_priority = priority_match is not None
    priority_value = f"P{priority_match.group(1)}" if priority_match else "P2"
    priority = cast(Literal["P0", "P1", "P2", "P3", "P4"], priority_value.upper())
    line_without_priority = _PRIORITY_RE.sub(" ", line, count=1).strip(" :-")
    label_match = _LABEL_PREFIX_RE.match(line_without_priority)
    has_label = label_match is not None
    if label_match:
        line_without_priority = line_without_priority[label_match.end() :].strip()
    normalized = _one_line(line_without_priority).rstrip(".")
    if not normalized or normalized.casefold() in _NON_ACTION_VALUES:
        return None
    field_label = normalized.split(":", 1)[0].strip("* ").casefold()
    if field_label in _FIELD_LABELS:
        return None
    if not explicit_priority and not has_label and _ACTION_RE.match(normalized) is None:
        return None
    if normalized.casefold().startswith("verified generated content for section"):
        return None
    return normalized[:240].rstrip(), priority, explicit_priority


def _inventory_item(
    *,
    what: str,
    priority: Literal["P0", "P1", "P2", "P3", "P4"],
    explicit_priority: bool,
    source_ref: str,
) -> InventoryItem:
    digest = hashlib.sha256(_deduplication_key(what).encode("utf-8")).hexdigest()[:12]
    priority_rationale = _priority_rationale(priority, explicit=explicit_priority)
    return InventoryItem(
        id=f"inventory-{digest}",
        who="Next continuation-session owner",
        what=what,
        how_discovered=f"Deterministic scan of actionable Section 10 evidence at {source_ref}.",
        where=(
            f"The project surface referenced by {source_ref}; resolve the exact change location "
            "against the source before editing."
        ),
        when="Next continuation session, before dependent follow-up work.",
        description=(
            f"Complete the actionable backlog outcome: {what}. Preserve the cited evidence, "
            "confirm scope, and record validation without promoting unsupported completion claims."
        ),
        acceptance_criteria=[
            f"Implement the requested outcome: {what}.",
            "Confirm the result against the cited source reference.",
        ],
        definition_of_done=[
            f"The requested outcome is implemented and read back: {what}.",
            "Focused tests and applicable audits pass with evidence recorded.",
        ],
        root_cause="The cited Section 10 backlog records this outcome as actionable pending work.",
        priority=priority,
        priority_rationale=priority_rationale,
        regression_prevention=[
            "Add or update a focused regression check for the changed behavior."
        ],
        testing=["Run the smallest relevant automated suite and verify the affected behavior."],
        audit_policies=[
            "Preserve source references and do not promote unverified completion claims."
        ],
        adjacent_considerations=[
            "Confirm dependencies, ownership, and the exact change location before implementation."
        ],
        source_refs=[source_ref],
    )


def _priority_rationale(
    priority: Literal["P0", "P1", "P2", "P3", "P4"],
    *,
    explicit: bool,
) -> str:
    if explicit:
        return (
            f"The cited Section 10 evidence explicitly labels this item {priority}; "
            "the scan did not infer a higher severity."
        )
    return (
        "No explicit severity was verified in the cited evidence; P2 is the conservative "
        "default for actionable pending work."
    )


def _source_ref(block: ContentBlock, display_names: dict[str, str]) -> str:
    display_name = _one_line(display_names.get(block.artifact_id, block.artifact_id))
    return f"{display_name} [{block.artifact_id}#{block.id}]"


def _evidence_scan_labels(
    evidence: Sequence[ContentBlock],
    display_names: dict[str, str],
) -> list[str]:
    return [_source_ref(block, display_names) for block in evidence]


def _merge_source_refs(item: InventoryItem, refs: Sequence[str]) -> InventoryItem:
    merged = _deduplicate([*item.source_refs, *refs])
    return item.model_copy(update={"source_refs": merged}, deep=True)


def _deduplication_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _one_line(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _one_line(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


__all__ = [
    "INVENTORY_SCAN_MARKER_PREFIX",
    "MAX_INVENTORY_ITEMS",
    "InventoryScanResult",
    "inventory_scan_evidence",
    "inventory_scan_marker",
    "scan_section_ten_inventory",
]
