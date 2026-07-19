"""Versioned, project-scoped Chroma derived-state index."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from filelock import FileLock
from llama_index.core.schema import TextNode

from handoff_forge.errors import StorageError
from handoff_forge.retrieval.embeddings import EmbeddingProvider
from handoff_forge.security import FILE_MODE, ensure_directory


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    node_id: str
    project_id: str
    text: str
    score: float
    metadata: dict[str, Any]

    @property
    def source_id(self) -> str | None:
        value = self.metadata.get("source_id") or self.metadata.get("artifact_id")
        return str(value) if value is not None else None


class ChromaIndex:
    """A rebuildable index fingerprinted by the exact embedding contract."""

    schema_version = "handoff-forge-chroma-v1"
    batch_size = 200

    def __init__(self, path: Path, embedding: EmbeddingProvider) -> None:
        self.path = ensure_directory(Path(path).expanduser().resolve())
        self.embedding = embedding
        self.fingerprint = embedding.fingerprint
        self.collection_name = f"hf_{self.fingerprint[:40]}"
        self._lock = FileLock(str(self.path / ".index.lock"), mode=FILE_MODE)
        self._client: Any = chromadb.PersistentClient(
            path=str(self.path),
            settings=ChromaSettings(
                anonymized_telemetry=False,
                chroma_product_telemetry_impl=(
                    "handoff_forge.retrieval.telemetry.DisabledProductTelemetry"
                ),
            ),
        )
        self._collection: Any = self._get_or_create_collection()

    def upsert(self, nodes: list[TextNode]) -> None:
        if not nodes:
            return
        ids_seen: set[str] = set()
        for node in nodes:
            if node.node_id in ids_seen:
                raise StorageError(f"duplicate node ID in upsert batch: {node.node_id}")
            ids_seen.add(node.node_id)
            if not node.metadata.get("project_id"):
                raise StorageError(f"node is missing project_id metadata: {node.node_id}")
        texts = [node.text for node in nodes]
        vectors = self.embedding.embed_documents(texts)
        if len(vectors) != len(nodes) or any(
            len(vector) != self.embedding.dimensions for vector in vectors
        ):
            raise StorageError("embedding provider returned an incompatible shape")

        with self._lock:
            for offset in range(0, len(nodes), self.batch_size):
                batch_nodes = nodes[offset : offset + self.batch_size]
                batch_vectors = vectors[offset : offset + self.batch_size]
                self._collection.upsert(
                    ids=[node.node_id for node in batch_nodes],
                    documents=[node.text for node in batch_nodes],
                    embeddings=batch_vectors,
                    metadatas=[self._chroma_metadata(node.metadata) for node in batch_nodes],
                )

    def search(self, *, project_id: str, query: str, limit: int = 5) -> list[RetrievalHit]:
        if limit < 1:
            raise ValueError("search limit must be positive")
        if not query.strip():
            return []
        scoped_count = self.count(project_id=project_id)
        if scoped_count == 0:
            return []
        vector = self.embedding.embed_query(query)
        if len(vector) != self.embedding.dimensions:
            raise StorageError("query embedding has an incompatible dimension")
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=min(limit, scoped_count),
            where={"project_id": project_id},
            include=["documents", "metadatas", "distances"],
        )
        ids = self._first_row(result.get("ids"))
        documents = self._first_row(result.get("documents"))
        metadatas = self._first_row(result.get("metadatas"))
        distances = self._first_row(result.get("distances"))
        hits: list[RetrievalHit] = []
        for node_id, document, metadata, distance in zip(
            ids,
            documents,
            metadatas,
            distances,
            strict=False,
        ):
            decoded = self._decode_metadata(metadata or {})
            hit_project = str(decoded.get("project_id", ""))
            if hit_project != project_id:
                raise StorageError("Chroma returned a node outside the requested project scope")
            hits.append(
                RetrievalHit(
                    node_id=str(node_id),
                    project_id=hit_project,
                    text=str(document or ""),
                    score=max(-1.0, min(1.0, 1.0 - float(distance))),
                    metadata=decoded,
                )
            )
        return hits

    def count(self, *, project_id: str | None = None) -> int:
        if project_id is None:
            return int(self._collection.count())
        result = self._collection.get(where={"project_id": project_id}, include=["metadatas"])
        return len(result.get("ids") or [])

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            if self.count(project_id=project_id):
                self._collection.delete(where={"project_id": project_id})
            if self.count(project_id=project_id) != 0:
                raise StorageError(f"vector deletion readback failed for project {project_id}")

    def delete_artifact(self, project_id: str, artifact_id: str) -> None:
        """Delete one artifact's derived nodes without disturbing sibling artifacts."""

        where = {
            "$and": [
                {"project_id": {"$eq": project_id}},
                {"artifact_id": {"$eq": artifact_id}},
            ]
        }
        with self._lock:
            matches = self._collection.get(where=where, include=["metadatas"])
            ids = list(matches.get("ids") or [])
            if ids:
                self._collection.delete(ids=ids)
            remaining = self._collection.get(where=where, include=["metadatas"])
            if remaining.get("ids"):
                raise StorageError(f"vector deletion readback failed for artifact {artifact_id}")

    def rebuild(self, *, project_id: str, nodes: list[TextNode]) -> None:
        for node in nodes:
            if node.metadata.get("project_id") != project_id:
                raise StorageError("rebuild nodes must all belong to the requested project")
        with self._lock:
            if self.count(project_id=project_id):
                self._collection.delete(where={"project_id": project_id})
            # Keep the lock as one writer while using a private non-locking path.
            self._upsert_locked(nodes)
            expected = len({node.node_id for node in nodes})
            actual = self.count(project_id=project_id)
            if actual != expected:
                raise StorageError(
                    f"vector rebuild readback failed for {project_id}: "
                    f"expected {expected}, found {actual}"
                )

    def collection_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for collection in self._client.list_collections():
            names.append(collection if isinstance(collection, str) else collection.name)
        return tuple(sorted(names))

    def _upsert_locked(self, nodes: list[TextNode]) -> None:
        if not nodes:
            return
        ids = [node.node_id for node in nodes]
        if len(ids) != len(set(ids)):
            raise StorageError("rebuild contains duplicate node IDs")
        vectors = self.embedding.embed_documents([node.text for node in nodes])
        if len(vectors) != len(nodes) or any(
            len(vector) != self.embedding.dimensions for vector in vectors
        ):
            raise StorageError("embedding provider returned an incompatible shape")
        for offset in range(0, len(nodes), self.batch_size):
            batch_nodes = nodes[offset : offset + self.batch_size]
            self._collection.upsert(
                ids=[node.node_id for node in batch_nodes],
                documents=[node.text for node in batch_nodes],
                embeddings=vectors[offset : offset + self.batch_size],
                metadatas=[self._chroma_metadata(node.metadata) for node in batch_nodes],
            )

    def _get_or_create_collection(self) -> Any:
        metadata: dict[str, str | int] = {
            "hnsw:space": "cosine",
            "schema_version": self.schema_version,
            "embedding_fingerprint": self.fingerprint,
            "embedding_provider": self.embedding.provider_name,
            "embedding_model": self.embedding.model_name,
            "embedding_dimensions": self.embedding.dimensions,
            "modality": self.embedding.modality,
        }
        collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata=metadata,
        )
        existing = collection.metadata or {}
        if existing.get("embedding_fingerprint") != self.fingerprint:
            raise StorageError("existing Chroma collection has an incompatible fingerprint")
        return collection

    @staticmethod
    def _chroma_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
        result: dict[str, str | int | float | bool] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                result[key] = value
            else:
                result[key] = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
        result["provenance_json"] = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return result

    @staticmethod
    def _decode_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        raw = metadata.get("provenance_json")
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                return decoded
        return {key: value for key, value in metadata.items() if key != "provenance_json"}

    @staticmethod
    def _first_row(value: Any) -> list[Any]:
        if not value:
            return []
        first = value[0]
        return list(first) if first is not None else []


VersionedChromaIndex = ChromaIndex
