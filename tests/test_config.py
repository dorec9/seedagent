import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

from config import load_config


def test_load_config_uses_defaults_when_optional_env_missing(monkeypatch):
    monkeypatch.delenv("KIWOOM_MODE", raising=False)
    monkeypatch.delenv("KIWOOM_BASE_URL", raising=False)
    monkeypatch.setenv("KIWOOM_APPKEY", "test-appkey")
    monkeypatch.setenv("KIWOOM_SECRETKEY", "test-secretkey")

    config = load_config()

    assert config.mode == "real"
    assert config.base_url == "https://api.kiwoom.com"
    assert config.appkey_loaded is True
    assert config.secretkey_loaded is True


def test_load_config_reads_appkey_and_secretkey_from_environment(monkeypatch):
    monkeypatch.setenv("KIWOOM_MODE", "mock")
    monkeypatch.setenv("KIWOOM_BASE_URL", "https://example.test")
    monkeypatch.setenv("KIWOOM_APPKEY", "test-appkey")
    monkeypatch.setenv("KIWOOM_SECRETKEY", "test-secretkey")

    config = load_config()

    assert config.mode == "mock"
    assert config.base_url == "https://example.test"
    assert config.appkey == "test-appkey"
    assert config.secretkey == "test-secretkey"


def test_load_config_allows_missing_appkey_or_secretkey(monkeypatch):
    monkeypatch.delenv("KIWOOM_APPKEY", raising=False)
    monkeypatch.delenv("KIWOOM_SECRETKEY", raising=False)

    config = load_config()

    assert config.appkey is None
    assert config.secretkey is None
    assert config.appkey_loaded is False
    assert config.secretkey_loaded is False
    assert config.validation_errors() == [
        "KIWOOM_APPKEY is missing",
        "KIWOOM_SECRETKEY is missing",
    ]
