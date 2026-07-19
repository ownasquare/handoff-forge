from __future__ import annotations

from types import SimpleNamespace

import pytest

from handoff_forge.errors import CapabilityError
from handoff_forge.models import (
    BlockKind,
    ContentBlock,
    GenerationRequest,
    ModelRoute,
)
from handoff_forge.providers.anthropic import AnthropicProvider
from handoff_forge.providers.base import ProviderExecutionError
from handoff_forge.providers.google import GoogleProvider
from handoff_forge.providers.offline import OfflineProvider
from handoff_forge.providers.openai import OpenAIProvider
from handoff_forge.providers.xai import XAIMessageBuilders, XAIProvider


def _request(
    provider: str,
    model: str,
    *,
    consent: bool = False,
    include_visual_evidence: bool = False,
) -> GenerationRequest:
    evidence = ContentBlock(
        id="block-1",
        project_id="project",
        artifact_id="artifact",
        artifact_sha256="b" * 64,
        kind=BlockKind.TEXT,
        text="Validation passed for the local workflow.",
        order=1,
        page_number=2,
        extraction_method="fixture",
    )
    return GenerationRequest(
        section_id=3,
        system_prompt="Use only supplied evidence.",
        user_prompt="Summarize the current state.",
        evidence=[evidence],
        route=ModelRoute(
            provider=provider,
            model=model,
            allow_cloud_upload=consent,
            include_visual_evidence=include_visual_evidence,
            max_output_tokens=128,
        ),
    )


def test_offline_provider_is_deterministic_and_cites_evidence() -> None:
    provider = OfflineProvider()
    request = _request("offline", "extractive-v1")

    first = provider.generate(request)
    second = provider.generate(request)

    assert first == second
    assert "Validation passed" in first.text
    assert "block-1" in first.text
    assert first.request_id and first.request_id.startswith("offline-")


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses = self

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text="OpenAI result",
            id="resp-openai",
            usage=SimpleNamespace(input_tokens=11, output_tokens=4),
            status="completed",
        )


def test_openai_adapter_uses_responses_api_with_injected_client() -> None:
    client = FakeOpenAIClient()
    provider = OpenAIProvider(
        network_enabled=True,
        configured=True,
        client=client,
        timeout_seconds=5,
        max_retries=0,
    )

    result = provider.generate(_request("openai", "gpt-test", consent=True))

    assert result.text == "OpenAI result"
    assert result.request_id == "resp-openai"
    assert client.calls[0]["model"] == "gpt-test"
    assert "input" in client.calls[0]


def test_visual_block_defaults_to_text_only_without_reading_image_bytes(tmp_path) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    missing_image = managed_root / "missing.png"
    visual = ContentBlock(
        id="block-visual-text-only",
        project_id="project",
        artifact_id="artifact",
        artifact_sha256="c" * 64,
        kind=BlockKind.PAGE_RENDER,
        text="Visual context says the release gate is blocked.",
        order=1,
        artifact_path=missing_image,
        extraction_method="fixture",
    )
    request = _request("openai", "gpt-test", consent=True).model_copy(
        update={"evidence": [visual], "image_paths": [missing_image]}
    )
    client = FakeOpenAIClient()
    provider = OpenAIProvider(
        network_enabled=True,
        configured=True,
        client=client,
        managed_root=managed_root,
    )

    provider.generate(request)

    user_content = client.calls[0]["input"][1]["content"]
    assert "Visual context says the release gate is blocked." in str(user_content)
    assert "input_image" not in str(user_content)


def test_operator_confirmed_visual_route_sends_selected_managed_image(tmp_path) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    selected_image = managed_root / "selected.png"
    selected_image.write_bytes(b"selected-image-bytes")
    visual = ContentBlock(
        id="block-visual-selected",
        project_id="project",
        artifact_id="artifact",
        artifact_sha256="d" * 64,
        kind=BlockKind.IMAGE,
        text="Selected architecture diagram.",
        order=1,
        artifact_path=selected_image,
        extraction_method="fixture",
    )
    request = _request(
        "openai",
        "gpt-test",
        consent=True,
        include_visual_evidence=True,
    ).model_copy(update={"evidence": [visual]})
    client = FakeOpenAIClient()
    provider = OpenAIProvider(
        network_enabled=True,
        configured=True,
        client=client,
        managed_root=managed_root,
    )

    provider.generate(request)

    user_content = client.calls[0]["input"][1]["content"]
    assert "input_image" in str(user_content)
    assert "data:image/png;base64," in str(user_content)


def test_visual_attestation_rejects_incapable_adapter_before_generation(tmp_path) -> None:
    selected_image = tmp_path / "selected.png"
    selected_image.write_bytes(b"selected-image-bytes")
    visual = ContentBlock(
        id="block-visual-unsupported",
        project_id="project",
        artifact_id="artifact",
        artifact_sha256="e" * 64,
        kind=BlockKind.IMAGE,
        text="Selected visual context remains text evidence.",
        order=1,
        artifact_path=selected_image,
        extraction_method="fixture",
    )
    request = _request(
        "offline",
        "extractive-v1",
        include_visual_evidence=True,
    ).model_copy(update={"evidence": [visual]})

    with pytest.raises(CapabilityError, match="image_input"):
        OfflineProvider().generate(request)


def test_remote_visual_upload_is_confined_to_the_managed_root(tmp_path) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside-image-canary")
    visual = ContentBlock(
        id="block-visual",
        project_id="project",
        artifact_id="artifact",
        artifact_sha256="c" * 64,
        kind=BlockKind.IMAGE,
        text="A selected page visual.",
        order=1,
        artifact_path=outside,
        extraction_method="fixture",
    )
    request = _request(
        "openai",
        "gpt-test",
        consent=True,
        include_visual_evidence=True,
    ).model_copy(update={"evidence": [visual], "image_paths": [outside]})
    client = FakeOpenAIClient()
    provider = OpenAIProvider(
        network_enabled=True,
        configured=True,
        client=client,
        managed_root=managed_root,
    )

    with pytest.raises(CapabilityError, match="managed data root"):
        provider.generate(request)

    assert client.calls == []


class FakeAnthropicClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.messages = self

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Anthropic result")],
            id="msg-anthropic",
            usage=SimpleNamespace(input_tokens=12, output_tokens=5),
            stop_reason="end_turn",
        )


def test_anthropic_adapter_uses_messages_api_with_injected_client() -> None:
    client = FakeAnthropicClient()
    provider = AnthropicProvider(
        network_enabled=True,
        configured=True,
        client=client,
        timeout_seconds=5,
        max_retries=0,
    )

    result = provider.generate(_request("anthropic", "claude-test", consent=True))

    assert result.text == "Anthropic result"
    assert result.finish_reason == "end_turn"
    assert client.calls[0]["model"] == "claude-test"
    assert "messages" in client.calls[0]


class FakeGoogleModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            text="Google result",
            response_id="google-response",
            usage_metadata=SimpleNamespace(prompt_token_count=13, candidates_token_count=6),
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )


def test_google_adapter_uses_google_genai_with_injected_client() -> None:
    client = SimpleNamespace(models=FakeGoogleModels())
    provider = GoogleProvider(
        network_enabled=True,
        configured=True,
        client=client,
        timeout_seconds=5,
        max_retries=0,
    )

    result = provider.generate(_request("google", "gemini-test", consent=True))

    assert result.text == "Google result"
    assert result.request_id == "google-response"
    assert client.models.calls[0]["model"] == "gemini-test"


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def append(self, message: object) -> None:
        self.messages.append(message)

    def sample(self) -> object:
        return SimpleNamespace(
            content="xAI result",
            id="xai-response",
            usage=SimpleNamespace(prompt_tokens=14, completion_tokens=7),
            finish_reason="stop",
        )


class FakeXAIClient:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.chat = self
        self.last_chat = FakeChat()

    def create(self, **kwargs: object) -> FakeChat:
        self.created.append(kwargs)
        return self.last_chat


def test_xai_adapter_uses_native_chat_sdk_with_injected_builders() -> None:
    client = FakeXAIClient()
    builders = XAIMessageBuilders(
        system=lambda text: ("system", text),
        user=lambda *parts: ("user", parts),
        image=lambda url: ("image", url),
    )
    provider = XAIProvider(
        network_enabled=True,
        configured=True,
        client=client,
        message_builders=builders,
        timeout_seconds=5,
        max_retries=0,
    )

    result = provider.generate(_request("xai", "grok-test", consent=True))

    assert result.text == "xAI result"
    assert client.created == [{"model": "grok-test"}]
    assert client.last_chat.messages[0][0] == "system"
    assert client.last_chat.messages[1][0] == "user"


class ExplodingResponses:
    def create(self, **_: object) -> object:
        token_canary = "sk-" + "live-value-that-must-not-escape"
        raise RuntimeError(token_canary)


def test_provider_errors_withhold_sdk_exception_details() -> None:
    client = SimpleNamespace(responses=ExplodingResponses())
    provider = OpenAIProvider(network_enabled=True, configured=True, client=client)

    with pytest.raises(ProviderExecutionError) as error:
        provider.generate(_request("openai", "gpt-test", consent=True))

    assert "sk-live" not in str(error.value)
    assert "RuntimeError" in str(error.value)
