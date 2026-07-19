from __future__ import annotations

import json

from handoff_forge.ingestion.nodes import NodeBuilder
from handoff_forge.models import BlockKind, ContentBlock


def _block(*, order: int, text: str) -> ContentBlock:
    return ContentBlock(
        id=f"block-{order}",
        project_id="project-a",
        artifact_id="artifact-a",
        artifact_sha256="a" * 64,
        kind=BlockKind.TEXT,
        text=text,
        order=order,
        page_number=order + 1,
        bbox=(0.0, 0.0, 1.0, 1.0),
        extraction_method="test",
        metadata={"nested": {"safe": True}},
    )


def test_node_ids_are_stable_and_keep_json_safe_provenance(settings) -> None:
    blocks = [
        _block(order=1, text="The validation blocker is provider access."),
        _block(order=0, text="Current architecture uses canonical JSON blocks."),
    ]
    builder = NodeBuilder(settings)

    first = builder.build(blocks)
    second = builder.build(list(reversed(blocks)))

    assert [node.node_id for node in first] == [node.node_id for node in second]
    assert all(node.metadata["artifact_sha256"] == "a" * 64 for node in first)
    assert all(node.metadata["untrusted_evidence"] is True for node in first)
    assert all(json.dumps(node.metadata) for node in first)
