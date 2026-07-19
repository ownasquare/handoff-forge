"""Application configuration with privacy-preserving defaults."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_path
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_root() -> Path:
    return user_data_path("handoff-forge", ensure_exists=False)


class HandoffSettings(BaseSettings):
    """Runtime settings shared by CLI and Streamlit."""

    model_config = SettingsConfigDict(
        env_prefix="HANDOFF_FORGE_",
        case_sensitive=False,
        extra="ignore",
    )

    data_root: Path = Field(
        default_factory=_default_data_root,
        validation_alias=AliasChoices(
            "data_root",
            "HANDOFF_FORGE_DATA_ROOT",
            "HANDOFF_FORGE_DATA_DIR",
        ),
    )
    offline: bool = True
    allow_network: bool = False
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 250
    max_markdown_characters: int = 5_000_000
    pdf_render_scale: float = 1.5
    ocr_native_text_threshold: int = 40
    ocr_timeout_seconds: int = 30
    ocr_language: str = "eng"
    embedding_dimensions: int = 384
    chunk_size: int = 512
    chunk_overlap: int = 64
    generation_context_characters: int = 24_000
    provider_timeout_seconds: int = 90
    provider_max_retries: int = 2

    @field_validator("data_root", mode="before")
    @classmethod
    def _expand_data_root(cls, value: object) -> Path:
        return Path(str(value)).expanduser().resolve()

    @field_validator(
        "max_upload_bytes",
        "max_pdf_pages",
        "max_markdown_characters",
        "ocr_timeout_seconds",
        "embedding_dimensions",
        "chunk_size",
        "generation_context_characters",
        "provider_timeout_seconds",
    )
    @classmethod
    def _require_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("value must be positive")
        return value

    @field_validator("chunk_overlap")
    @classmethod
    def _validate_overlap(cls, value: int, info: object) -> int:
        if value < 0:
            raise ValueError("chunk_overlap must be non-negative")
        return value

    @property
    def network_enabled(self) -> bool:
        """Return true only for an explicitly enabled non-offline run."""

        return self.allow_network and not self.offline
