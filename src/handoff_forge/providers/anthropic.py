"""Lazy Anthropic Messages API adapter."""

from __future__ import annotations

from typing import Any

from handoff_forge.models import GenerationRequest, GenerationResult, ProviderCapabilities
from handoff_forge.providers.base import (
    RemoteProviderBase,
    evidence_prompt,
    get_value,
    load_selected_images,
)


class AnthropicProvider(RemoteProviderBase):
    name = "anthropic"
    module_name = "anthropic"
    credential_names = ("ANTHROPIC_API_KEY",)
    capabilities = ProviderCapabilities(
        text=True,
        image_input=True,
        native_pdf=False,
        document_search=False,
        structured_output=False,
        streaming=False,
        supported_mime_types=(
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
        ),
        max_bytes=32 * 1024 * 1024,
        max_pages=100,
        stability="stable",
    )

    def _build_client(self) -> object:
        module = self._import_module()
        client_type = module.Anthropic
        return client_type(timeout=self.timeout_seconds, max_retries=0)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self._guard(request)
        images = load_selected_images(
            self.name,
            self.capabilities,
            request,
            managed_root=self.managed_root,
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": evidence_prompt(request)}]
        content.extend(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": image.base64_data,
                },
            }
            for image in images
        )
        payload = {
            "model": request.route.model,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": content}],
            "temperature": request.route.temperature,
            "max_tokens": request.route.max_output_tokens,
        }
        try:
            client = self._get_client()
            messages = client.messages
            response = self._request_with_retries(lambda: messages.create(**payload))
        except Exception as error:
            raise self._execution_error(error) from None

        text_parts = [
            str(get_value(item, "text", ""))
            for item in get_value(response, "content", [])
            if get_value(item, "type") == "text"
        ]
        usage = get_value(response, "usage")
        return GenerationResult(
            text="\n".join(part for part in text_parts if part).strip(),
            provider=self.name,
            model=request.route.model,
            request_id=get_value(response, "id"),
            input_tokens=get_value(usage, "input_tokens"),
            output_tokens=get_value(usage, "output_tokens"),
            finish_reason=get_value(response, "stop_reason"),
        )
