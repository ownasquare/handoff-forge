"""A deliberate no-op Chroma product telemetry component."""

from __future__ import annotations

from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class DisabledProductTelemetry(ProductTelemetryClient):
    """Keep the local index from creating identifiers or emitting product events."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        del event


__all__ = ["DisabledProductTelemetry"]
