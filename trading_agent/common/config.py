"""Configuration helpers for the Kiwoom REST API authentication step."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    database_url: str | None = None
    account_no: str | None = None

    @property
    def base_url_loaded(self) -> bool:
        return bool(self.base_url)

    @property
    def appkey_loaded(self) -> bool:
        return bool(self.appkey)

    @property
    def secretkey_loaded(self) -> bool:
        return bool(self.secretkey)

    @property
    def account_no_loaded(self) -> bool:
        return bool(self.account_no)

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
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)
    return KiwoomConfig(
        mode=os.getenv("KIWOOM_MODE", "real"),
        base_url=os.getenv("KIWOOM_BASE_URL", "https://api.kiwoom.com"),
        app_env=os.getenv("APP_ENV", "codex"),
        appkey=os.getenv("KIWOOM_APPKEY"),
        secretkey=os.getenv("KIWOOM_SECRETKEY"),
        database_url=os.getenv("DATABASE_URL"),
        account_no=os.getenv("KIWOOM_ACCOUNT_NO"),
    )

@dataclass
class BotConfig:
    evaluation_interval: int = int(os.getenv("BOT_EVAL_INTERVAL", "10"))
    target_stocks: list[str] = field(default_factory=lambda: os.getenv("BOT_TARGETS", "005930,000660,035420").split(","))
    buy_threshold: float = float(os.getenv("BOT_BUY_THRESHOLD", "0.6"))
    take_profit_pct: float = float(os.getenv("BOT_TAKE_PROFIT_PCT", "5.0"))
    stop_loss_pct: float = float(os.getenv("BOT_STOP_LOSS_PCT", "-3.0"))
    position_size_pct: float = float(os.getenv("BOT_POSITION_SIZE_PCT", "0.10"))
    discord_webhook_url: str | None = os.getenv("DISCORD_WEBHOOK_URL")

