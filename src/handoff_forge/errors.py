"""Typed application errors suitable for CLI and UI presentation."""


class HandoffForgeError(RuntimeError):
    """Base exception for expected application failures."""


class UnsafeUploadError(HandoffForgeError):
    """An upload failed type, signature, size, or path validation."""


class StorageError(HandoffForgeError):
    """Durable state could not be read or written safely."""


class ParseError(HandoffForgeError):
    """A document could not be parsed safely."""


class HandoffValidationError(HandoffForgeError):
    """A handoff violates its selected schema profile."""


class MergeError(HandoffForgeError):
    """Handoffs could not be merged without losing contract integrity."""


class CapabilityError(HandoffForgeError):
    """A provider or harness lacks a required capability."""


class ExternalActionError(HandoffForgeError):
    """A safe external action could not be completed."""
