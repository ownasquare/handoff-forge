"""Embedding providers with an offline deterministic baseline."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from importlib import import_module
from typing import Any, Protocol, runtime_checkable

from handoff_forge.errors import CapabilityError

_TOKEN = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", flags=re.UNICODE)


@runtime_checkable
class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int
    modality: str

    @property
    def fingerprint(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class DeterministicHashEmbedding:
    """Credential-free signed feature hashing for repeatable local retrieval."""

    provider_name = "local"
    model_name = "sha256-feature-hash-v1"
    modality = "text"
    schema_version = "handoff-forge-embedding-v1"

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 8:
            raise ValueError("embedding dimensions must be at least 8")
        self.dimensions = dimensions

    @property
    def fingerprint(self) -> str:
        return embedding_fingerprint(self)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    # Familiar aliases for callers that do not distinguish query intent.
    def embed(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    def _embed(self, text: str) -> list[float]:
        normalized = " ".join(text.casefold().split())
        tokens = _TOKEN.findall(normalized)
        features: Counter[str] = Counter(tokens)
        compact = f" {normalized} "
        features.update(
            f"#3:{compact[index : index + 3]}" for index in range(max(0, len(compact) - 2))
        )
        if not features:
            features["<empty>"] = 1

        vector = [0.0] * self.dimensions
        for feature, count in sorted(features.items()):
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            # Sublinear frequency prevents repeated boilerplate from dominating.
            vector[bucket] += sign * (1.0 + math.log(float(count)))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:  # pragma: no cover - signed collision defense
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]


class VoyageEmbedding:
    """Opt-in Voyage adapter with explicit limits and no silent truncation."""

    provider_name = "voyage"
    modality = "text"
    schema_version = "handoff-forge-embedding-v1"

    def __init__(
        self,
        *,
        api_key: str | None,
        model_name: str = "voyage-3.5",
        dimensions: int = 1024,
        max_batch_size: int = 128,
        max_batch_characters: int = 1_000_000,
    ) -> None:
        if not api_key:
            raise CapabilityError("Voyage embeddings require an explicitly supplied API key")
        if dimensions < 1 or max_batch_size < 1 or max_batch_characters < 1:
            raise ValueError("Voyage embedding limits must be positive")
        self.model_name = model_name
        self.dimensions = dimensions
        self.max_batch_size = max_batch_size
        self.max_batch_characters = max_batch_characters
        try:
            voyageai = import_module("voyageai")
        except ImportError as exc:
            raise CapabilityError(
                "install the providers extra to enable Voyage embeddings"
            ) from exc
        self._client: Any = voyageai.Client(api_key=api_key)

    @property
    def fingerprint(self) -> str:
        return embedding_fingerprint(self)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, input_type="document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], input_type="query")[0]

    def _embed(self, texts: Sequence[str], *, input_type: str) -> list[list[float]]:
        values = [str(text) for text in texts]
        if not values:
            return []
        if len(values) > self.max_batch_size:
            raise CapabilityError(
                f"Voyage batch has {len(values)} texts; configured limit is {self.max_batch_size}"
            )
        total_characters = sum(len(text) for text in values)
        if total_characters > self.max_batch_characters:
            raise CapabilityError(
                "Voyage batch exceeds the configured character limit; split it before upload"
            )
        try:
            response = self._client.embed(
                values,
                model=self.model_name,
                input_type=input_type,
                truncation=False,
                output_dimension=self.dimensions,
            )
        except Exception as exc:
            raise CapabilityError("Voyage embedding request failed") from exc
        embeddings = [list(map(float, embedding)) for embedding in response.embeddings]
        if len(embeddings) != len(values) or any(
            len(embedding) != self.dimensions for embedding in embeddings
        ):
            raise CapabilityError("Voyage returned an unexpected embedding shape")
        return embeddings


def embedding_fingerprint(provider: EmbeddingProvider) -> str:
    """Fingerprint the exact schema/provider/model/dimension/modality contract."""

    payload = {
        "schema_version": getattr(provider, "schema_version", "unknown"),
        "provider": provider.provider_name,
        "model": provider.model_name,
        "dimensions": provider.dimensions,
        "modality": provider.modality,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
