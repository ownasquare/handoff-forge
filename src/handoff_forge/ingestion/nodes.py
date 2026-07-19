"""Stable project-owned LlamaIndex node construction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode

from handoff_forge.config import HandoffSettings
from handoff_forge.models import ContentBlock, ParsedDocument


def _json_safe(value: Any) -> str | int | float | bool | list[Any] | dict[str, Any] | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class NodeBuilder:
    """Build transient TextNodes while keeping canonical blocks authoritative."""

    schema_version = "handoff-forge-node-v1"

    def __init__(self, settings: HandoffSettings | None = None) -> None:
        self.settings = settings or HandoffSettings()
        if self.settings.chunk_overlap >= self.settings.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self._splitter = SentenceSplitter(
            chunk_size=self.settings.chunk_size,
            chunk_overlap=self.settings.chunk_overlap,
        )

    def build(
        self,
        blocks: Iterable[ContentBlock] | ParsedDocument,
    ) -> list[TextNode]:
        canonical_blocks = list(blocks.blocks if isinstance(blocks, ParsedDocument) else blocks)
        canonical_blocks.sort(
            key=lambda block: (
                block.project_id,
                block.artifact_sha256,
                block.order,
                block.page_number or 0,
                block.line_start or 0,
                block.id,
            )
        )
        nodes: list[TextNode] = []
        for block in canonical_blocks:
            chunks = self._splitter.split_text(block.text) or [block.text]
            for chunk_index, chunk in enumerate(chunks):
                text = chunk.strip()
                if not text:
                    continue
                metadata = self._metadata(block, chunk_index=chunk_index, chunk_count=len(chunks))
                node_id = self._node_id(block, chunk_index=chunk_index, text=text)
                nodes.append(TextNode(id_=node_id, text=text, metadata=metadata))
        return nodes

    def build_documents(self, documents: Iterable[ParsedDocument]) -> list[TextNode]:
        blocks = [block for document in documents for block in document.blocks]
        return self.build(blocks)

    def _metadata(
        self,
        block: ContentBlock,
        *,
        chunk_index: int,
        chunk_count: int,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_id": block.project_id,
            "source_id": block.source_id,
            "artifact_id": block.artifact_id,
            "artifact_sha256": block.artifact_sha256,
            "block_id": block.id,
            "block_kind": block.kind.value,
            "block_order": block.order,
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "extraction_method": block.extraction_method,
            "confidence": block.confidence,
            "untrusted_evidence": True,
        }
        optional = {
            "page_number": block.page_number,
            "line_start": block.line_start,
            "line_end": block.line_end,
            "bbox": block.bbox,
            "artifact_path": block.artifact_path,
            "block_metadata": block.metadata,
        }
        metadata.update(
            {
                key: safe_value
                for key, value in optional.items()
                if value is not None and (safe_value := _json_safe(value)) is not None
            }
        )
        return metadata

    def _node_id(self, block: ContentBlock, *, chunk_index: int, text: str) -> str:
        payload = {
            "schema_version": self.schema_version,
            "project_id": block.project_id,
            "artifact_sha256": block.artifact_sha256,
            "block_id": block.id,
            "chunk_index": chunk_index,
            "text": text,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"node_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def build_nodes(
    blocks: Iterable[ContentBlock] | ParsedDocument,
    settings: HandoffSettings | None = None,
) -> list[TextNode]:
    return NodeBuilder(settings).build(blocks)
