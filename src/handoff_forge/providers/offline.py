"""Deterministic local extractive provider."""

from __future__ import annotations

import hashlib
import json

from handoff_forge.models import (
    GenerationRequest,
    GenerationResult,
    ProviderCapabilities,
)
from handoff_forge.providers.base import ProviderStatus, validate_capabilities


class OfflineProvider:
    """Create a cited extract without loading a model or using a network."""

    name = "offline"
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
        ordered = sorted(
            request.evidence,
            key=lambda block: (
                block.order,
                block.page_number or 0,
                block.id,
            ),
        )
        if ordered:
            lines: list[str] = []
            for block in ordered:
                page = f" page={block.page_number}" if block.page_number is not None else ""
                lines.append(
                    f"- {block.text} [source={block.artifact_sha256[:12]} block={block.id}{page}]"
                )
            text = "\n".join(lines)
        else:
            text = "Unknown — no verified evidence was supplied."

        character_limit = max(256, request.route.max_output_tokens * 4)
        if len(text) > character_limit:
            text = text[: character_limit - 1].rstrip() + "…"

        serialized = json.dumps(request.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        request_id = f"offline-{hashlib.sha256(serialized).hexdigest()[:20]}"
        input_text = (
            request.system_prompt + request.user_prompt + "".join(block.text for block in ordered)
        )
        return GenerationResult(
            text=text,
            provider=self.name,
            model=request.route.model,
            request_id=request_id,
            input_tokens=max(1, len(input_text) // 4),
            output_tokens=max(1, len(text) // 4),
            finish_reason="stop",
        )
