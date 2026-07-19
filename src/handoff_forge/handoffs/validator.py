"""Actionable validators for versioned handoff profiles."""

from __future__ import annotations

import re
from collections import Counter

from handoff_forge.errors import HandoffValidationError
from handoff_forge.handoffs.catalog import (
    EXPECTED_SECTION_IDS,
    SECTION_BY_ID,
    normalize_heading_title,
)
from handoff_forge.handoffs.parser import parse_confidence_lines, parse_handoff
from handoff_forge.models import ConfidenceLevel, HandoffValidationReport, TemplateProfile

_INVENTORY_ITEM_HEADING_RE = re.compile(r"^###\s+([^:\n]+):\s*(.+?)\s*$", re.MULTILINE)
_INVENTORY_FIELD_RE = re.compile(r"^- \*\*(.+?):\*\*(?:\s*(.*))?$")
_INVENTORY_NESTED_VALUE_RE = re.compile(r"^\s{2,}-\s+(.+)$")
_NO_NEW_ITEMS_RE = re.compile(
    r"^\s*-\s+(?:\*\*)?No new items found\.(?:\*\*)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_EVIDENCE_SCANNED_RE = re.compile(
    r"^\s*-\s+(?:\*\*)?Evidence scanned(?::)?(?:\*\*)?\s*:?\s*(.*?)\s*$",
    re.IGNORECASE,
)
_PLACEHOLDER_VALUES = {
    "",
    "n/a",
    "na",
    "needs re-validation",
    "needs revalidation",
    "none",
    "none known",
    "none recorded",
    "not provided",
    "not supplied",
    "tbd",
    "todo",
    "unknown",
}
_INVENTORY_SCALARS = {
    "who",
    "what",
    "how discovered",
    "where",
    "when",
    "detailed description",
    "root cause",
    "priority",
    "priority rationale",
}
_INVENTORY_LISTS = {
    "acceptance criteria",
    "definition of done",
    "regression prevention",
    "testing",
    "audit policies",
    "other considerations",
    "source references",
}
_HIGH_NEGATION_PATTERNS = (
    re.compile(r"\b(?:assumed|unknown|unconfirmed|unverified)\b", re.IGNORECASE),
    re.compile(r"\b(?:needs?|requires?)\s+re[- ]?validation\b", re.IGNORECASE),
    re.compile(
        r"\b(?:cannot|can't|hasn't|haven't|isn't|no|not|never|wasn't|without)\b"
        r"[^.\n]{0,48}"
        r"\b(?:confirmed|current[- ]session|validated|verified)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:confirmed|current[- ]session|validated|verified)\b[^.\n]{0,32}"
        r"\b(?:not|never)\b",
        re.IGNORECASE,
    ),
)
_AFFIRMATIVE_COMPLETION_PATTERNS = (
    re.compile(
        r"\b(?:the\s+)?(?:project|task|work|objective|implementation)\s+"
        r"(?:is|are|was|were|has been|have been)\s+(?:fully\s+)?"
        r"(?:complete|completed|done)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:all|every)\s+(?:the\s+)?(?:work|tasks?|objectives?|requirements?)\s+"
        r"(?:(?:is|are|has been|have been)\s+)?(?:complete|completed|done)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:fully|successfully)\s+completed\s+(?:the\s+)?"
        r"(?:project|task|work|objective|implementation)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcompletion\s+(?:is\s+)?(?:achieved|confirmed|proven)\b", re.IGNORECASE),
    re.compile(r"\b100\s*%\s+complete\b", re.IGNORECASE),
    re.compile(r"\bready\s+for\s+(?:deployment|production|release)\b", re.IGNORECASE),
)


def validate_handoff(text: str, profile: TemplateProfile) -> HandoffValidationReport:
    parsed = parse_handoff(text)
    ids = [section.id for section in parsed.sections]
    counts = Counter(ids)
    duplicates = sorted(section_id for section_id, count in counts.items() if count > 1)
    if duplicates:
        raise HandoffValidationError(f"duplicate section {duplicates[0]}")
    missing = [section_id for section_id in EXPECTED_SECTION_IDS if section_id not in counts]
    if missing:
        raise HandoffValidationError(f"missing section {missing[0]}")
    if tuple(ids) != EXPECTED_SECTION_IDS:
        raise HandoffValidationError(f"section order must be 1 through 12; received {tuple(ids)}")
    for section in parsed.sections:
        expected = normalize_heading_title(SECTION_BY_ID[section.id].title)
        actual = normalize_heading_title(section.title)
        if actual != expected:
            raise HandoffValidationError(
                f"section {section.id} title must be '{SECTION_BY_ID[section.id].title}'"
            )

    _validate_profile_wrapper(parsed, profile)
    _validate_confidence(parsed, profile)
    warnings: list[str] = []
    if parsed.unified_execution_plan is not None:
        warnings.append("Unified Execution Plan is supplemental to the twelve-section schema.")
    return HandoffValidationReport(
        profile=profile,
        section_ids=EXPECTED_SECTION_IDS,
        warnings=warnings,
    )


def _validate_profile_wrapper(parsed: object, profile: TemplateProfile) -> None:
    # ParsedHandoff is deliberately structural; avoid importing a second runtime protocol.
    frontmatter = parsed.frontmatter  # type: ignore[attr-defined]
    raw_text = parsed.raw_text  # type: ignore[attr-defined]
    inventory_text = parsed.inventory_text  # type: ignore[attr-defined]
    if profile is TemplateProfile.CODEX_POST_CHAT_V1:
        if not frontmatter:
            raise HandoffValidationError("post-chat profile requires MDC frontmatter")
        if not str(frontmatter.get("description", "")).strip():
            raise HandoffValidationError("post-chat frontmatter requires description")
        if frontmatter.get("alwaysApply") is not False:
            raise HandoffValidationError("post-chat frontmatter requires alwaysApply: false")
        if inventory_text is None:
            raise HandoffValidationError("post-chat profile requires INVENTORY NEXT ITEMS")
        _validate_inventory(inventory_text)
    elif profile is TemplateProfile.CODEX_PRECOMPACT_V1:
        lowered = raw_text.casefold()
        if "in-progress context snapshot" not in lowered:
            raise HandoffValidationError(
                "precompact profile must identify an in-progress context snapshot"
            )
        if "not a completion claim" not in lowered:
            raise HandoffValidationError(
                "precompact profile must state that it is not a completion claim"
            )
        if _has_affirmative_completion_claim(raw_text):
            raise HandoffValidationError(
                "precompact profile cannot contain an affirmative completion claim"
            )


def _validate_confidence(parsed: object, profile: TemplateProfile) -> None:
    assessments = parse_confidence_lines(parsed)  # type: ignore[arg-type]
    ids = [item.section_id for item in assessments]
    if 12 in ids:
        raise HandoffValidationError("Section 12 must not assess itself")
    counts = Counter(ids)
    duplicates = [section_id for section_id, count in counts.items() if count > 1]
    if duplicates:
        raise HandoffValidationError(f"duplicate confidence assessment for Section {duplicates[0]}")
    missing = [section_id for section_id in range(1, 12) if section_id not in counts]
    if missing:
        raise HandoffValidationError(f"confidence assessment missing for Section {missing[0]}")
    for item in assessments:
        if item.confidence is ConfidenceLevel.HIGH:
            basis = item.basis.casefold()
            if any(pattern.search(item.basis) for pattern in _HIGH_NEGATION_PATTERNS):
                raise HandoffValidationError(
                    f"Section {item.section_id} High cannot use negated or unverified evidence"
                )
            if "current session" not in basis and "current-session" not in basis:
                raise HandoffValidationError(
                    f"Section {item.section_id} High requires current-session evidence"
                )
            if re.search(r"\bverified\b", basis) is None:
                raise HandoffValidationError(
                    f"Section {item.section_id} High requires explicitly verified evidence"
                )
        if profile is TemplateProfile.CODEX_POST_CHAT_V1:
            expected = {
                ConfidenceLevel.HIGH: "✅",
                ConfidenceLevel.MEDIUM: "⚠️",
                ConfidenceLevel.LOW: "❓",
            }[item.confidence]
            if item.emoji != expected:
                raise HandoffValidationError(
                    f"post-chat confidence for Section {item.section_id} requires {expected}"
                )
        elif item.emoji is not None:
            raise HandoffValidationError(f"{profile.value} uses non-emoji confidence labels")


def _validate_inventory(inventory_text: str) -> None:
    text = inventory_text.strip()
    if not text:
        raise HandoffValidationError("INVENTORY NEXT ITEMS cannot be empty")
    headings = list(_INVENTORY_ITEM_HEADING_RE.finditer(text))
    no_new = _NO_NEW_ITEMS_RE.search(text)
    if no_new is not None:
        if headings:
            raise HandoffValidationError(
                "No new items found declaration cannot be combined with inventory items"
            )
        evidence = _inventory_scan_values(text)
        if not evidence or any(_is_placeholder(value) for value in evidence):
            raise HandoffValidationError(
                "No new items found requires a complete Evidence scanned declaration"
            )
        return
    if not headings:
        raise HandoffValidationError(
            "INVENTORY NEXT ITEMS requires full item fields or a complete no-new-items scan"
        )
    seen_ids: set[str] = set()
    for index, heading in enumerate(headings):
        item_id = heading.group(1).strip()
        heading_what = heading.group(2).strip()
        if _is_placeholder(item_id) or _is_placeholder(heading_what):
            raise HandoffValidationError("inventory item heading cannot be a placeholder")
        normalized_id = item_id.casefold()
        if normalized_id in seen_ids:
            raise HandoffValidationError(f"duplicate inventory item {item_id}")
        seen_ids.add(normalized_id)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        _validate_inventory_item(item_id, text[heading.end() : end])


def _validate_inventory_item(item_id: str, body: str) -> None:
    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    active_list: str | None = None
    seen_fields: set[str] = set()
    for line in body.splitlines():
        field = _INVENTORY_FIELD_RE.match(line.strip())
        if field:
            label = field.group(1).strip().casefold()
            value = (field.group(2) or "").strip()
            if label in seen_fields:
                raise HandoffValidationError(
                    f"inventory item {item_id} repeats required field {label}"
                )
            seen_fields.add(label)
            if label in _INVENTORY_LISTS:
                lists[label] = [value] if value else []
                active_list = label
            else:
                scalars[label] = value
                active_list = None
            continue
        nested = _INVENTORY_NESTED_VALUE_RE.match(line)
        if nested and active_list is not None:
            lists[active_list].append(nested.group(1).strip())

    missing_scalars = sorted(_INVENTORY_SCALARS - scalars.keys())
    missing_lists = sorted(_INVENTORY_LISTS - lists.keys())
    if missing_scalars or missing_lists:
        missing = [*missing_scalars, *missing_lists]
        raise HandoffValidationError(
            f"inventory item {item_id} missing required field {missing[0]}"
        )
    for label in sorted(_INVENTORY_SCALARS):
        if _is_placeholder(scalars[label]):
            raise HandoffValidationError(f"inventory item {item_id} has incomplete field {label}")
    if scalars["priority"].upper() not in {"P0", "P1", "P2", "P3", "P4"}:
        raise HandoffValidationError(f"inventory item {item_id} has invalid priority")
    for label in sorted(_INVENTORY_LISTS):
        values = lists[label]
        if not values or any(_is_placeholder(value) for value in values):
            raise HandoffValidationError(f"inventory item {item_id} has incomplete field {label}")


def _inventory_scan_values(text: str) -> list[str]:
    lines = text.splitlines()
    values: list[str] = []
    for index, line in enumerate(lines):
        match = _EVIDENCE_SCANNED_RE.match(line)
        if match is None:
            continue
        inline = match.group(1).strip()
        if inline:
            values.append(inline.rstrip("."))
        for nested_line in lines[index + 1 :]:
            nested = _INVENTORY_NESTED_VALUE_RE.match(nested_line)
            if nested is None:
                break
            values.append(nested.group(1).strip().rstrip("."))
        break
    return values


def _is_placeholder(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value).strip().strip(".*_`").casefold()
    if normalized in _PLACEHOLDER_VALUES:
        return True
    return normalized.startswith(
        (
            "needs re-validation",
            "needs revalidation",
            "no evidence",
            "no source inventory",
            "none recorded",
            "not scanned",
            "scan incomplete",
            "tbd ",
            "todo ",
            "unknown ",
        )
    )


def _has_affirmative_completion_claim(raw_text: str) -> bool:
    without_required_disclaimer = re.sub(
        r"not a completion claim",
        "",
        raw_text,
        flags=re.IGNORECASE,
    )
    for pattern in _AFFIRMATIVE_COMPLETION_PATTERNS:
        for match in pattern.finditer(without_required_disclaimer):
            sentence_prefix = re.split(
                r"[.!?;\n]",
                without_required_disclaimer[: match.start()],
            )[-1]
            if re.search(
                r"\b(?:cannot|can't|don't|doesn't|false|isn't|never|no|none|not|"
                r"wasn't|weren't|without)\b",
                sentence_prefix,
                re.IGNORECASE,
            ):
                continue
            return True
    return False


__all__ = ["HandoffValidationError", "validate_handoff"]
