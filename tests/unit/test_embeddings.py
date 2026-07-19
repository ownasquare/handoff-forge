from __future__ import annotations

import math

import pytest

from handoff_forge.errors import CapabilityError
from handoff_forge.retrieval.embeddings import DeterministicHashEmbedding, VoyageEmbedding


def test_hash_embeddings_are_deterministic_normalized_and_versioned() -> None:
    embedding = DeterministicHashEmbedding(dimensions=64)

    first = embedding.embed_query("Validation blocker")
    second = embedding.embed_documents(["Validation blocker"])[0]

    assert first == second
    assert len(first) == 64
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)
    assert embedding.fingerprint == DeterministicHashEmbedding(dimensions=64).fingerprint
    assert embedding.fingerprint != DeterministicHashEmbedding(dimensions=32).fingerprint


def test_voyage_requires_explicit_credentials_before_import_or_network() -> None:
    with pytest.raises(CapabilityError, match="explicitly supplied"):
        VoyageEmbedding(api_key=None)
