"""A deterministic provider that summarizes only the evidence supplied by Handoff Forge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from handoff_forge.config import HandoffSettings
from handoff_forge.extensions import (
    GenerationRequest,
    GenerationResult,
    ProviderCapabilities,
    ProviderStatus,
    validate_capabilities,
)


class LocalNotesProvider:
    """Small, credential-free example of the supported provider protocol."""

    name = "local-notes"
    is_remote = False
    capabilities = ProviderCapabilities(
        text=True,
        image_input=False,
        native_pdf=False,
        document_search=False,
        structured_output=False,
        streaming=False,
        supported_mime_types=("text/plain", "text/markdown"),
        stability="local",
    )

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            installed=True,
            configured=True,
            enabled=True,
            state="ready",
            capabilities=self.capabilities,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        validate_capabilities(self.name, self.capabilities, request)
        evidence = sorted(
            request.evidence,
            key=lambda block: (block.order, block.page_number or 0, block.id),
        )
        if evidence:
            notes = [
                (f"- {block.text} [source={block.artifact_sha256[:12]} block={block.id}]")
                for block in evidence
            ]
            text = "Local notes summary:\n" + "\n".join(notes)
        else:
            text = "No verified notes were supplied."
        character_limit = max(256, request.route.max_output_tokens * 4)
        if len(text) > character_limit:
            text = text[: character_limit - 1].rstrip() + "…"
        serialized = json.dumps(request.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        request_id = f"local-notes-{hashlib.sha256(serialized).hexdigest()[:20]}"
        return GenerationResult(
            text=text,
            provider=self.name,
            model=request.route.model,
            request_id=request_id,
            input_tokens=max(1, sum(len(block.text) for block in evidence) // 4),
            output_tokens=max(1, len(text) // 4),
            finish_reason="stop",
        )


def create_provider(
    *,
    settings: HandoffSettings,
    managed_root: Path,
) -> LocalNotesProvider:
    """Build the provider from the exact safe context supplied to every extension."""

    del settings, managed_root
    return LocalNotesProvider()
