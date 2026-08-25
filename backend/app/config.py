"""Application settings with safe local defaults.

Every setting is optional. With no environment variables configured the
application starts in rules-only mode backed by a local SQLite database; no
model API key is ever required merely to start (PRD Phase 0 gate).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "ARGUS CONTROL"
API_VERSION = "v1"
DOMAIN_CONTRACT_VERSION = "domain-contracts-v0"


class Settings(BaseSettings):
    """Runtime settings. Every field has a safe default for local use."""

    model_config = SettingsConfigDict(
        env_prefix="ARGUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_version: str = "0.1.0"
    db_path: Path = Path("argus.local.sqlite3")
    model_provider: str | None = None
    model_api_key: SecretStr | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    investigator_tool_budget: int = Field(default=12, ge=1, le=100)
    investigator_max_retries: int = Field(default=2, ge=1, le=10)
    investigator_timeout_s: float = Field(default=30.0, gt=0)
    razorpay_key_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ARGUS_RAZORPAY_KEY_ID", "RAZORPAY_KEY_ID", "razorpay_key_id"
        ),
    )
    razorpay_key_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ARGUS_RAZORPAY_KEY_SECRET", "RAZORPAY_KEY_SECRET", "razorpay_key_secret"
        ),
    )
    razorpay_webhook_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ARGUS_RAZORPAY_WEBHOOK_SECRET", "RAZORPAY_WEBHOOK_SECRET", "razorpay_webhook_secret"
        ),
    )

    # Optional voice speech providers (PRD 13.5.3). Keys are NEVER required:
    # with them unset, /voice/transcribe and /voice/tts return 501 with a
    # machine-readable fallback and the copilot uses on-device browser
    # speech engines. Any OpenAI-compatible / Sarvam-shaped gateway works
    # through the configurable base URLs.
    voice_stt_api_key: SecretStr | None = None
    voice_stt_base_url: str = "https://api.sarvam.ai"
    voice_stt_model: str = "saarika:v2.5"
    voice_tts_api_key: SecretStr | None = None
    voice_tts_base_url: str = "https://api.sarvam.ai"
    voice_tts_model: str = "bulbul:v2"
    voice_tts_speaker: str = "anushka"
    sarvam_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ARGUS_SARVAM_API_KEY", "SARVAM_API_KEY"),
    )
    elevenlabs_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ARGUS_ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"),
    )
    elevenlabs_voice_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ARGUS_ELEVENLABS_VOICE_ID", "ELEVENLABS_VOICE_ID"),
    )

    @field_validator("model_provider", "razorpay_key_id", "elevenlabs_voice_id", mode="before")
    @classmethod
    def _strip_empty_str(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator(
        "model_api_key",
        "razorpay_key_secret",
        "razorpay_webhook_secret",
        "sarvam_api_key",
        "elevenlabs_api_key",
        mode="before",
    )
    @classmethod
    def _strip_empty_secret(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        if isinstance(v, SecretStr) and not v.get_secret_value().strip():
            return None
        return v

    @property
    def rules_only(self) -> bool:
        """True when no usable model is configured; rules-only mode must always start."""
        if not self.model_provider or not self.model_api_key:
            return True
        return not bool(
            self.model_provider.strip() and self.model_api_key.get_secret_value().strip()
        )

    def safe_summary(self) -> dict[str, object]:
        """Loggable configuration snapshot. Never includes the API key value."""
        return {
            "app_version": self.app_version,
            "db_path": str(self.db_path),
            "model_provider": self.model_provider,
            "model_key_configured": self.model_api_key is not None,
            "rules_only": self.rules_only,
            "host": self.host,
            "port": self.port,
            "log_level": self.log_level,
            "razorpay_test_mode_configured": self.razorpay_key_id is not None
            and self.razorpay_key_secret is not None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton; override in tests by constructing Settings()."""
    return Settings()
