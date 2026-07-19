"""Lazy OpenAI Responses API adapter."""

from __future__ import annotations

from typing import Any

from handoff_forge.models import GenerationRequest, GenerationResult, ProviderCapabilities
from handoff_forge.providers.base import (
    RemoteProviderBase,
    evidence_prompt,
    get_value,
    load_selected_images,
)


class OpenAIProvider(RemoteProviderBase):
    name = "openai"
    module_name = "openai"
    credential_names = ("OPENAI_API_KEY",)
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
        max_bytes=50 * 1024 * 1024,
        stability="stable",
    )

    def _build_client(self) -> object:
        module = self._import_module()
        client_type = module.OpenAI
        # Retries are owned by the provider-neutral boundary below.
        return client_type(timeout=self.timeout_seconds, max_retries=0)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self._guard(request)
        images = load_selected_images(
            self.name,
            self.capabilities,
            request,
            managed_root=self.managed_root,
        )
        user_content: list[dict[str, Any]] = [
            {"type": "input_text", "text": evidence_prompt(request)}
        ]
        user_content.extend(
            {
                "type": "input_image",
                "image_url": image.data_url,
                "detail": "auto",
            }
            for image in images
        )
        payload = {
            "model": request.route.model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": request.system_prompt}],
                },
                {"role": "user", "content": user_content},
            ],
            "temperature": request.route.temperature,
            "max_output_tokens": request.route.max_output_tokens,
        }
        try:
            client = self._get_client()
            responses = client.responses
            response = self._request_with_retries(lambda: responses.create(**payload))
        except Exception as error:
            raise self._execution_error(error) from None

        usage = get_value(response, "usage")
        return GenerationResult(
            text=str(get_value(response, "output_text", "")).strip(),
            provider=self.name,
            model=request.route.model,
            request_id=get_value(response, "id"),
            input_tokens=get_value(usage, "input_tokens"),
            output_tokens=get_value(usage, "output_tokens"),
            finish_reason=str(get_value(response, "status", "completed")),
        )
