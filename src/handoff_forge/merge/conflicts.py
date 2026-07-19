"""Statement normalization and conservative contradiction detection."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from itertools import combinations

from handoff_forge.models import ConfidenceLevel, ConflictRecord


@dataclass(frozen=True, slots=True)
class ProvenanceStatement:
    text: str
    section_id: int
    source_ref: str
    confidence: ConfidenceLevel


_STATUS_WORDS = {
    "blocked",
    "broken",
    "complete",
    "completed",
    "deferred",
    "failed",
    "failing",
    "healthy",
    "implemented",
    "incomplete",
    "passing",
    "pending",
    "running",
    "stable",
    "unstable",
    "working",
}
_NEGATIONS = {"never", "no", "not", "without"}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "has",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "was",
}


def split_statements(
    content: str, *, section_id: int, source_ref: str, confidence: ConfidenceLevel
) -> list[ProvenanceStatement]:
    found: list[ProvenanceStatement] = []
    for raw_line in content.splitlines():
        line = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", raw_line).strip()
        if not line or line.startswith("#"):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
            normalized = sentence.strip()
            if normalized:
                found.append(
                    ProvenanceStatement(
                        text=normalized,
                        section_id=section_id,
                        source_ref=source_ref,
                        confidence=confidence,
                    )
                )
    return found


def statement_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def detect_conflicts(statements: list[ProvenanceStatement]) -> list[ConflictRecord]:
    records: list[ConflictRecord] = []
    seen: set[tuple[str, str]] = set()
    for left, right in combinations(statements, 2):
        if left.source_ref == right.source_ref or left.section_id != right.section_id:
            continue
        if not _contradict(left.text, right.text):
            continue
        keys = sorted((statement_key(left.text), statement_key(right.text)))
        pair = (keys[0], keys[1])
        if pair in seen:
            continue
        seen.add(pair)
        ranked = sorted(
            (left, right),
            key=lambda item: (-_confidence_rank(item.confidence), item.source_ref, item.text),
        )
        if left.confidence is right.confidence:
            status = "review_required"
            resolution = "Equal-confidence variants retained; review is required before execution."
        else:
            status = "resolved"
            winner = ranked[0]
            resolution = (
                f"Prefer {winner.source_ref} because it has fresher confidence; "
                "retain both variants."
            )
        digest = hashlib.sha256("|".join(pair).encode()).hexdigest()[:12]
        records.append(
            ConflictRecord(
                id=f"conflict-{digest}",
                section_id=left.section_id,
                summary=f"Conflicting Section {left.section_id} statements",
                variants=[
                    f"{item.text} [{item.source_ref}]"
                    for item in sorted((left, right), key=lambda item: item.source_ref)
                ],
                source_refs=sorted({left.source_ref, right.source_ref}),
                resolution=resolution,
                status=status,
            )
        )
    return sorted(records, key=lambda item: (item.section_id, item.id))


def _contradict(left: str, right: str) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    left_subject = _subject_tokens(left_tokens)
    right_subject = _subject_tokens(right_tokens)
    if not left_subject or not right_subject:
        return False
    overlap = len(left_subject & right_subject) / len(left_subject | right_subject)
    if overlap < 0.5:
        return False
    left_status = left_tokens & _STATUS_WORDS
    right_status = right_tokens & _STATUS_WORDS
    if left_status and right_status and left_status != right_status:
        return True
    left_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", left))
    right_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", right))
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return True
    return bool(left_tokens & _NEGATIONS) != bool(right_tokens & _NEGATIONS)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _subject_tokens(tokens: set[str]) -> set[str]:
    return {
        token
        for token in tokens
        if token not in _STOPWORDS
        and token not in _STATUS_WORDS
        and token not in _NEGATIONS
        and not token.isdigit()
    }


def _confidence_rank(value: ConfidenceLevel) -> int:
    return {
        ConfidenceLevel.LOW: 0,
        ConfidenceLevel.MEDIUM: 1,
        ConfidenceLevel.HIGH: 2,
    }[value]
