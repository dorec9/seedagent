"""Configuration helpers for the Kiwoom REST API authentication step."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class KiwoomConfig:
    """Runtime configuration loaded from environment variables."""

    mode: str
    base_url: str
    app_env: str
    appkey: str | None
    secretkey: str | None

    @property
    def base_url_loaded(self) -> bool:
        return bool(self.base_url)

    @property
    def appkey_loaded(self) -> bool:
        return bool(self.appkey)

    @property
    def secretkey_loaded(self) -> bool:
        return bool(self.secretkey)

    def validation_errors(self) -> list[str]:
        """Return clear validation errors without exposing secret values."""
        errors: list[str] = []
        if not self.appkey_loaded:
            errors.append("KIWOOM_APPKEY is missing")
        if not self.secretkey_loaded:
            errors.append("KIWOOM_SECRETKEY is missing")
        return errors


def load_config() -> KiwoomConfig:
    """Load Kiwoom configuration from environment variables using os.getenv()."""
    return KiwoomConfig(
        mode=os.getenv("KIWOOM_MODE", "real"),
        base_url=os.getenv("KIWOOM_BASE_URL", "https://api.kiwoom.com"),
        app_env=os.getenv("APP_ENV", "codex"),
        appkey=os.getenv("KIWOOM_APPKEY"),
        secretkey=os.getenv("KIWOOM_SECRETKEY"),
    )
