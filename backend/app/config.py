"""Application settings with safe local defaults.

Every setting is optional. With no environment variables configured the
application starts in rules-only mode backed by a local SQLite database; no
model API key is ever required merely to start (PRD Phase 0 gate).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
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

    @property
    def rules_only(self) -> bool:
        """True when no usable model is configured; rules-only mode must always start."""
        return self.model_provider is None or self.model_api_key is None

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
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton; override in tests by constructing Settings()."""
    return Settings()
