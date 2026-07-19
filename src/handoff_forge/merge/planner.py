"""Unified executable backlog derived from every handoff source."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from handoff_forge.handoffs.profiles import render_handoff
from handoff_forge.models import InventoryItem, MergedPlan, PlanTask

_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}


def derive_plan_tasks(
    section_ten_items: Iterable[tuple[str, str]],
    inventory_items: Iterable[tuple[InventoryItem, str]],
) -> list[PlanTask]:
    candidates: list[PlanTask] = []
    for text, source_ref in section_ten_items:
        cleaned = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", text).strip()
        if not cleaned:
            continue
        match = re.search(r"\b(P[0-4])\b", cleaned, re.IGNORECASE)
        priority = match.group(1).upper() if match else "P2"
        title = re.sub(r"\[?P[0-4]\]?\s*[:—-]?\s*", "", cleaned, flags=re.IGNORECASE)
        title = title.strip() or "Review imported next step"
        candidates.append(
            PlanTask(
                id=_task_id(title),
                title=title,
                priority=priority,
                status="blocked" if "blocked" in title.casefold() else "pending",
                description=cleaned,
                acceptance_criteria=[f"Verify and close: {title}"],
                source_refs=[source_ref],
            )
        )
    for item, source_ref in inventory_items:
        candidates.append(
            PlanTask(
                id=_task_id(item.what),
                title=item.what,
                priority=item.priority,
                status="blocked" if "block" in item.when.casefold() else "pending",
                description=item.description,
                acceptance_criteria=item.acceptance_criteria or ["Needs re-validation"],
                source_refs=sorted(set(item.source_refs) | {source_ref}),
            )
        )
    by_title: dict[str, PlanTask] = {}
    for task in candidates:
        key = re.sub(r"[^a-z0-9]+", " ", task.title.casefold()).strip()
        existing = by_title.get(key)
        if existing is None:
            by_title[key] = task
            continue
        priority = min((existing.priority, task.priority), key=_PRIORITY_ORDER.__getitem__)
        by_title[key] = existing.model_copy(
            update={
                "priority": priority,
                "source_refs": sorted(set(existing.source_refs) | set(task.source_refs)),
                "acceptance_criteria": list(
                    dict.fromkeys(existing.acceptance_criteria + task.acceptance_criteria)
                ),
            }
        )
    return sorted(by_title.values(), key=lambda task: (_PRIORITY_ORDER[task.priority], task.id))


def render_unified_execution_plan(plan: MergedPlan) -> str:
    lines = ["## Unified Execution Plan", "", "### Immediate task", ""]
    if plan.tasks:
        immediate = plan.tasks[0]
        lines.extend(
            [
                f"- **{immediate.priority}: {immediate.title}**",
                f"  - {immediate.description}",
                f"  - Sources: {', '.join(immediate.source_refs) or 'Unknown'}",
            ]
        )
    else:
        lines.append("- Needs re-validation; no executable task was recovered.")
    lines.extend(["", "### Prioritized backlog", ""])
    lines.extend(
        f"- [{task.priority}] {task.title} ({task.status}) — {', '.join(task.source_refs)}"
        for task in plan.tasks
    )
    if not plan.tasks:
        lines.append("- None known.")
    lines.extend(["", "### Dependencies", ""])
    dependencies = [
        f"- {task.title}: {', '.join(task.dependencies)}"
        for task in plan.tasks
        if task.dependencies
    ]
    lines.extend(dependencies or ["- None known."])
    lines.extend(["", "### Blockers", ""])
    blockers = [
        f"- {conflict.id}: {conflict.summary}"
        for conflict in plan.conflicts
        if conflict.status == "review_required"
    ]
    blockers.extend(f"- {task.title}" for task in plan.tasks if task.status == "blocked")
    lines.extend(blockers or ["- None known."])
    lines.extend(["", "### Validation gates", ""])
    gates = _content_lines(plan.package.sections[4].content)
    lines.extend(f"- {gate}" for gate in gates)
    if not gates:
        lines.append("- Validate tests, profile schema, and runtime proof before closure.")
    lines.extend(["", "### Source handoffs", ""])
    lines.extend(f"- S{index}: `{digest}`" for index, digest in enumerate(plan.source_hashes, 1))
    if plan.package.sources:
        lines.extend(["", "### Source artifacts", ""])
        lines.extend(
            f"- `{source.display_name}` — `{source.sha256}`" for source in plan.package.sources
        )
    lines.extend(["", "### Conflict decisions", ""])
    if plan.conflicts:
        for conflict in plan.conflicts:
            lines.append(f"- **{conflict.id} ({conflict.status}):** {conflict.resolution}")
    else:
        lines.append("- No contradictions detected by the deterministic merge pass.")
    lines.extend(["", "### Execution waves", ""])
    for priority in ("P0", "P1", "P2", "P3", "P4"):
        tasks = [task.title for task in plan.tasks if task.priority == priority]
        if tasks:
            lines.append(f"- **{priority}:** {'; '.join(tasks)}")
    if not plan.tasks:
        lines.append("- Needs re-validation.")
    return "\n".join(lines).rstrip() + "\n"


def render_merged_handoff(plan: MergedPlan) -> str:
    return render_handoff(plan.package).rstrip() + "\n\n" + render_unified_execution_plan(plan)


def _task_id(title: str) -> str:
    return f"task-{hashlib.sha256(title.casefold().encode()).hexdigest()[:12]}"


def _content_lines(content: str) -> list[str]:
    return [
        re.sub(r"^\s*[-*+]\s+", "", line).strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
