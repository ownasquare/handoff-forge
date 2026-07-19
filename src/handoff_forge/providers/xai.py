"""Lazy native xAI SDK adapter for selected text and image evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from handoff_forge.models import GenerationRequest, GenerationResult, ProviderCapabilities
from handoff_forge.providers.base import (
    RemoteProviderBase,
    evidence_prompt,
    get_value,
    load_selected_images,
)


@dataclass(frozen=True)
class XAIMessageBuilders:
    system: Callable[[str], object]
    user: Callable[..., object]
    image: Callable[[str], object]


class XAIProvider(RemoteProviderBase):
    name = "xai"
    module_name = "xai_sdk"
    credential_names = ("XAI_API_KEY",)
    capabilities = ProviderCapabilities(
        text=True,
        image_input=True,
        native_pdf=False,
        document_search=False,
        structured_output=False,
        streaming=False,
        supported_mime_types=("image/jpeg", "image/png"),
        max_bytes=20 * 1024 * 1024,
        stability="stable",
    )

    def __init__(
        self,
        *,
        message_builders: XAIMessageBuilders | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._message_builders = message_builders

    def _build_client(self) -> object:
        module = self._import_module()
        client_type = module.Client
        # Disable SDK-level retries; the shared boundary applies the configured limit.
        return client_type(
            timeout=self.timeout_seconds,
            channel_options=[("grpc.enable_retries", 0)],
        )

    def _builders(self) -> XAIMessageBuilders:
        if self._message_builders is None:
            module = self._import_module("xai_sdk.chat")
            self._message_builders = XAIMessageBuilders(
                system=module.system,
                user=module.user,
                image=module.image,
            )
        return self._message_builders

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self._guard(request)
        images = load_selected_images(
            self.name,
            self.capabilities,
            request,
            managed_root=self.managed_root,
        )
        try:
            client = self._get_client()
            builders = self._builders()
            chat_api = client.chat
            chat = chat_api.create(model=request.route.model)
            chat.append(builders.system(request.system_prompt))
            parts: list[object] = [evidence_prompt(request)]
            parts.extend(builders.image(item.data_url) for item in images)
            chat.append(builders.user(*parts))
            response = self._request_with_retries(chat.sample)
        except Exception as error:
            raise self._execution_error(error) from None

        usage = get_value(response, "usage")
        input_tokens = get_value(usage, "prompt_tokens")
        if input_tokens is None:
            input_tokens = get_value(usage, "input_tokens")
        output_tokens = get_value(usage, "completion_tokens")
        if output_tokens is None:
            output_tokens = get_value(usage, "output_tokens")
        request_id = get_value(response, "id")
        if request_id is None:
            request_id = get_value(response, "response_id")
        return GenerationResult(
            text=str(get_value(response, "content", "")).strip(),
            provider=self.name,
            model=request.route.model,
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=get_value(response, "finish_reason"),
        )
