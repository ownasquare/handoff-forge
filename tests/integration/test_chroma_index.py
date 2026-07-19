from __future__ import annotations

from pathlib import Path

from handoff_forge.ingestion.nodes import NodeBuilder
from handoff_forge.models import BlockKind, ContentBlock
from handoff_forge.retrieval.embeddings import DeterministicHashEmbedding
from handoff_forge.retrieval.index import ChromaIndex


def _block(project_id: str, order: int, text: str) -> ContentBlock:
    return ContentBlock(
        id=f"{project_id}-block-{order}",
        project_id=project_id,
        artifact_id=f"{project_id}-artifact",
        artifact_sha256=("a" if project_id == "project-a" else "b") * 64,
        kind=BlockKind.TEXT,
        text=text,
        order=order,
        extraction_method="test",
    )


def test_index_is_scoped_restart_safe_rebuildable_and_deletable(
    tmp_path: Path,
    settings,
) -> None:
    embedding = DeterministicHashEmbedding(dimensions=64)
    builder = NodeBuilder(settings)
    project_a_nodes = builder.build(
        [
            _block("project-a", 0, "Validation blocker and regression test evidence."),
            _block("project-a", 1, "Architecture decision record."),
        ]
    )
    project_b_nodes = builder.build([_block("project-b", 0, "Unrelated deployment notes.")])
    index_path = tmp_path / "chroma"
    index = ChromaIndex(index_path, embedding)
    index.upsert(project_a_nodes + project_b_nodes)

    hits = index.search(project_id="project-a", query="validation blocker", limit=5)
    assert hits and {hit.project_id for hit in hits} == {"project-a"}
    assert index.count(project_id="project-a") == len(project_a_nodes)
    assert index.count(project_id="project-b") == len(project_b_nodes)

    index.delete_artifact("project-a", "project-a-artifact")
    assert index.count(project_id="project-a") == 0
    assert index.count(project_id="project-b") == len(project_b_nodes)
    index.upsert(project_a_nodes)

    restarted = ChromaIndex(index_path, embedding)
    assert restarted.count(project_id="project-a") == len(project_a_nodes)
    restarted.rebuild(project_id="project-a", nodes=project_a_nodes[:1])
    assert restarted.count(project_id="project-a") == 1
    assert restarted.count(project_id="project-b") == len(project_b_nodes)

    restarted.delete_project("project-a")
    assert restarted.count(project_id="project-a") == 0
    assert restarted.search(project_id="project-a", query="validation", limit=5) == []

    incompatible = ChromaIndex(index_path, DeterministicHashEmbedding(dimensions=32))
    assert incompatible.collection_name != restarted.collection_name
    assert set(incompatible.collection_names()) == {
        incompatible.collection_name,
        restarted.collection_name,
    }
