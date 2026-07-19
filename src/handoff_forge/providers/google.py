"""Lazy Google Gen AI adapter."""

from __future__ import annotations

from typing import Any

from handoff_forge.models import GenerationRequest, GenerationResult, ProviderCapabilities
from handoff_forge.providers.base import (
    RemoteProviderBase,
    evidence_prompt,
    get_value,
    load_selected_images,
)


class GoogleProvider(RemoteProviderBase):
    name = "google"
    module_name = "google.genai"
    credential_names = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
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
            "image/webp",
        ),
        max_bytes=50 * 1024 * 1024,
        max_pages=1_000,
        stability="stable",
    )

    def _build_client(self) -> object:
        module = self._import_module()
        client_type = module.Client
        http_options = module.types.HttpOptions(
            api_version="v1",
            timeout=self.timeout_seconds * 1_000,
        )
        # Select the stable API surface rather than the SDK's default beta endpoint.
        return client_type(http_options=http_options)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self._guard(request)
        images = load_selected_images(
            self.name,
            self.capabilities,
            request,
            managed_root=self.managed_root,
        )
        contents: list[Any] = [evidence_prompt(request)]
        contents.extend(
            {"inline_data": {"mime_type": image.media_type, "data": image.data}} for image in images
        )
        payload = {
            "model": request.route.model,
            "contents": contents,
            "config": {
                "system_instruction": request.system_prompt,
                "temperature": request.route.temperature,
                "max_output_tokens": request.route.max_output_tokens,
            },
        }
        try:
            client = self._get_client()
            models = client.models
            response = self._request_with_retries(lambda: models.generate_content(**payload))
        except Exception as error:
            raise self._execution_error(error) from None

        usage = get_value(response, "usage_metadata")
        candidates = get_value(response, "candidates", [])
        finish_reason = None
        if candidates:
            finish_reason_value = get_value(candidates[0], "finish_reason")
            finish_reason = str(finish_reason_value) if finish_reason_value is not None else None
        return GenerationResult(
            text=str(get_value(response, "text", "")).strip(),
            provider=self.name,
            model=request.route.model,
            request_id=get_value(response, "response_id"),
            input_tokens=get_value(usage, "prompt_token_count"),
            output_tokens=get_value(usage, "candidates_token_count"),
            finish_reason=finish_reason,
        )
