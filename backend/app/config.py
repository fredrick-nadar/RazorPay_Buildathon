"""Application settings with safe local defaults.

Every setting is optional. With no environment variables configured the
application starts in rules-only mode backed by a local SQLite database; no
model API key is ever required merely to start (PRD Phase 0 gate).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "ARGUS CONTROL"
API_VERSION = "v1"
DOMAIN_CONTRACT_VERSION = "domain-contracts-v0"


class Settings(BaseSettings):
    """Runtime settings. Every field has a safe default for local use."""

    model_config = SettingsConfigDict(
        env_prefix="ARGUS_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        frozen=True,
    )

    app_version: str = "0.1.0"
    db_path: Path = Path("argus.local.sqlite3")
    import_staging_root: Path = Path("artifacts/raw/imports")
    model_provider: str | None = None
    model_api_key: SecretStr | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    investigator_tool_budget: int = Field(default=12, ge=1, le=100)
    investigator_max_retries: int = Field(default=2, ge=1, le=10)
    # Total wall time for ONE investigated case, covering every model turn,
    # retry, provider attempt and fallback. Raised from 30s, which was shorter
    # than a single HTTP attempt and therefore preempted every provider.
    investigator_timeout_s: float = Field(default=75.0, gt=0)
    # One model turn inside the case deadline. Groq then fallback providers are
    # walked within this window.
    investigator_turn_timeout_s: float = Field(default=25.0, gt=0)
    # Withheld from every attempt so a failure can be classified and returned
    # before the outer watchdog fires.
    investigator_safety_reserve_s: float = Field(default=0.75, ge=0)
    # Last-resort worker grace over the case deadline, for a broken provider
    # that ignores the deadline entirely.
    investigator_watchdog_grace_s: float = Field(default=5.0, ge=0)
    # Shortest attempt worth starting. Below this the remaining time is spent
    # failing cleanly instead of on a request that cannot complete.
    investigator_min_attempt_s: float = Field(default=1.5, gt=0)
    # Wall time held back inside a turn for the providers AFTER the current
    # one, so a first provider cannot starve its fallback. Defaults to one full
    # attempt cap; clamped so it always leaves a viable first attempt.
    investigator_fallback_reserve_s: float | None = Field(default=None, ge=0)
    # A live model must make at least one allowlisted read-only evidence tool
    # call before its final answer is accepted.
    investigator_require_tool_call: bool = True
    # ONE attempt per provider by default. Two 11-second attempts inside a
    # 25-second turn left a fallback provider ~2 seconds, so the fallback could
    # not realistically answer (REVIEW-006). Raising this stays safe because
    # the fallback reserve above protects the next provider.
    ai_provider_max_attempts: int = Field(default=1, ge=1, le=3)
    workflow_max_attempts: int = Field(default=2, ge=1, le=5)
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
    voice_stt_model: str = "saaras:v3"
    voice_tts_api_key: SecretStr | None = None
    voice_tts_base_url: str = "https://api.sarvam.ai"
    voice_tts_model: str = "bulbul:v3"
    voice_tts_speaker: str = "shubh"
    voice_tts_pace: float = 1.0
    voice_tts_sample_rate: int = 22050

    # AI investigator providers (PRD 10). Chain order for "auto":
    # groq -> gemini -> openai -> sarvam -> ollama (local Llama). With no live
    # provider, rules-only mode remains available; the deterministic fake is
    # selected only when explicitly configured/requested.
    ai_provider: str = Field(
        default="auto",
        pattern="^(auto|groq|gemini|openai|sarvam|ollama|fake|none)$",
    )
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    groq_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ARGUS_GROQ_API_KEY", "GROQ_API_KEY", "groq_api_key"),
    )
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_schema_model: str = "openai/gpt-oss-20b"
    groq_investigator_model: str = Field(
        default="openai/gpt-oss-20b",
        validation_alias=AliasChoices(
            "ARGUS_GROQ_INVESTIGATOR_MODEL",
            "ARGUS_GROQ_MODEL",
            "GROQ_MODEL",
            "groq_investigator_model",
        ),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ARGUS_OPENAI_API_KEY", "OPENAI_API_KEY", "openai_api_key"),
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices(
            "ARGUS_OPENAI_MODEL", "OPENAI_MODEL", "LLM_MODEL", "ARGUS_LLM_MODEL", "openai_model"
        ),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices(
            "ARGUS_OPENAI_BASE_URL",
            "OPENAI_BASE_URL",
            "LLM_BASE_URL",
            "ARGUS_LLM_BASE_URL",
            "openai_base_url",
        ),
    )
    sarvam_model: str = "sarvam-105b"
    sarvam_base_url: str = "https://api.sarvam.ai/v1"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "llama3.1:8b"
    ollama_api_key: str = "ollama"
    ollama_enabled: bool = False  # local Llama joins the auto chain only when enabled
    # Cap for ONE HTTP attempt. Must stay below the turn window; the effective
    # value per attempt is min(this cap, time left before the case deadline).
    ai_timeout_s: float = Field(default=11.0, gt=0)
    sarvam_api_key: SecretStr | None = None
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None

    @field_validator("model_provider", "razorpay_key_id", "elevenlabs_voice_id", mode="before")
    @classmethod
    def _strip_empty_str(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator(
        "model_api_key",
        "groq_api_key",
        "gemini_api_key",
        "openai_api_key",
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

    @model_validator(mode="after")
    def _fallback_unprefixed_sarvam(self) -> Settings:
        if not self.sarvam_api_key and not os.environ.get("PYTEST_CURRENT_TEST"):
            key = os.environ.get("SARVAM_API_KEY") or os.environ.get("ARGUS_SARVAM_API_KEY")
            if not key:
                for fn in (".env.local", ".env"):
                    p = Path(fn)
                    if p.is_file():
                        try:
                            for line in p.read_text(encoding="utf-8").splitlines():
                                if "=" in line and not line.strip().startswith("#"):
                                    k, v = line.split("=", 1)
                                    if (
                                        k.strip() in ("SARVAM_API_KEY", "ARGUS_SARVAM_API_KEY")
                                        and v.strip()
                                    ):
                                        key = v.strip().strip("\"'")
                                        break
                        except Exception:
                            pass
                        if key:
                            break
            if key and key.strip():
                object.__setattr__(self, "sarvam_api_key", SecretStr(key.strip()))
        return self

    @model_validator(mode="after")
    def _cross_populate_universal_llm_key(self) -> Settings:
        """Cross-populate universal LLM_API_KEY / model_api_key to specific provider chains."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return self

        raw_key = self.model_api_key.get_secret_value().strip() if self.model_api_key else ""
        if not raw_key:
            # Check env and .env.local / .env files for LLM_API_KEY directly
            for name in (
                "LLM_API_KEY",
                "ARGUS_LLM_API_KEY",
                "MODEL_API_KEY",
                "ARGUS_MODEL_API_KEY",
            ):
                val = os.environ.get(name, "").strip()
                if val:
                    raw_key = val
                    object.__setattr__(self, "model_api_key", SecretStr(raw_key))
                    break
            if not raw_key:
                for fn in (".env.local", ".env"):
                    p = Path(fn)
                    if p.is_file():
                        try:
                            for line in p.read_text(encoding="utf-8").splitlines():
                                if "=" in line and not line.strip().startswith("#"):
                                    k, v = line.split("=", 1)
                                    if (
                                        k.strip()
                                        in (
                                            "LLM_API_KEY",
                                            "ARGUS_LLM_API_KEY",
                                            "MODEL_API_KEY",
                                            "ARGUS_MODEL_API_KEY",
                                        )
                                        and v.strip()
                                    ):
                                        raw_key = v.strip().strip("\"'")
                                        object.__setattr__(
                                            self, "model_api_key", SecretStr(raw_key)
                                        )
                                        break
                        except Exception:
                            pass
                        if raw_key:
                            break

        if raw_key:
            # Auto-detect Groq keys
            if raw_key.startswith("gsk_"):
                if not self.groq_api_key:
                    object.__setattr__(self, "groq_api_key", SecretStr(raw_key))
                if not self.model_provider:
                    object.__setattr__(self, "model_provider", "groq")

            # Auto-detect Gemini keys
            elif raw_key.startswith("AIza") or raw_key.startswith("AQ."):
                if not self.gemini_api_key:
                    object.__setattr__(self, "gemini_api_key", SecretStr(raw_key))
                if not self.model_provider:
                    object.__setattr__(self, "model_provider", "gemini")

            # Standard OpenAI / OpenAI-compatible keys
            elif raw_key.startswith("sk-") or not self.gemini_api_key:
                if not self.openai_api_key:
                    object.__setattr__(self, "openai_api_key", SecretStr(raw_key))
                if not self.gemini_api_key and str(
                    getattr(self, "model_provider", "") or ""
                ).lower() in ("gemini", ""):
                    object.__setattr__(self, "gemini_api_key", SecretStr(raw_key))
                if not self.model_provider:
                    object.__setattr__(self, "model_provider", "openai")

        return self

    @property
    def rules_only(self) -> bool:
        """True when no usable model is configured; rules-only mode must always start."""
        live_configured = bool(
            self.groq_api_key
            or self.gemini_api_key
            or self.openai_api_key
            or self.sarvam_api_key
            or self.ollama_enabled
            or (self.model_provider and self.model_api_key)
        )
        return not live_configured

    def safe_summary(self) -> dict[str, object]:
        """Loggable configuration snapshot. Never includes the API key value."""
        return {
            "app_version": self.app_version,
            "db_path": str(self.db_path),
            "model_provider": self.ai_provider,
            "model_key_configured": any(
                key is not None
                for key in (
                    self.groq_api_key,
                    self.gemini_api_key,
                    self.openai_api_key,
                    self.sarvam_api_key,
                    self.model_api_key,
                )
            ),
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
