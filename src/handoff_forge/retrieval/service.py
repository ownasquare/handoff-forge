"""Application-facing retrieval orchestration over canonical documents."""

from __future__ import annotations

from collections.abc import Iterable

from handoff_forge.ingestion.nodes import NodeBuilder
from handoff_forge.models import ContentBlock, ParsedDocument
from handoff_forge.retrieval.index import ChromaIndex, RetrievalHit
from handoff_forge.storage import ContentAddressedStore


class RetrievalService:
    """Keep LlamaIndex nodes transient and Chroma safely rebuildable."""

    def __init__(
        self,
        index: ChromaIndex,
        *,
        node_builder: NodeBuilder | None = None,
        store: ContentAddressedStore | None = None,
    ) -> None:
        self.index = index
        self.node_builder = node_builder or NodeBuilder()
        self.store = store

    def index_document(self, document: ParsedDocument) -> int:
        nodes = self.node_builder.build(document)
        self.index.upsert(nodes)
        return len(nodes)

    def index_documents(self, documents: Iterable[ParsedDocument]) -> int:
        nodes = self.node_builder.build_documents(documents)
        self.index.upsert(nodes)
        return len(nodes)

    def index_blocks(self, blocks: Iterable[ContentBlock]) -> int:
        nodes = self.node_builder.build(blocks)
        self.index.upsert(nodes)
        return len(nodes)

    def search(self, project_id: str, query: str, *, limit: int = 5) -> list[RetrievalHit]:
        return self.index.search(project_id=project_id, query=query, limit=limit)

    def rebuild(
        self,
        project_id: str,
        documents: Iterable[ParsedDocument] | None = None,
    ) -> int:
        if documents is None:
            if self.store is None:
                raise ValueError("documents or a canonical store are required for rebuild")
            project = self.store.load_project(project_id)
            documents = [
                self.store.load_parsed_document(project_id, artifact_id)
                for artifact_id in project.artifact_ids
            ]
        document_list = list(documents)
        if any(document.artifact.project_id != project_id for document in document_list):
            raise ValueError("all rebuild documents must belong to the requested project")
        nodes = self.node_builder.build_documents(document_list)
        self.index.rebuild(project_id=project_id, nodes=nodes)
        return len(nodes)

    def delete_project(self, project_id: str, *, include_canonical_sources: bool = True) -> None:
        self.index.delete_project(project_id)
        if include_canonical_sources and self.store is not None:
            self.store.delete_project(project_id)

    def delete_artifact(self, project_id: str, artifact_id: str) -> None:
        self.index.delete_artifact(project_id, artifact_id)
