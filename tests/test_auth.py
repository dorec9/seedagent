import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

import auth as auth_module
from auth import KiwoomAuth
from config import KiwoomConfig

APPKEY = "sensitive-appkey"
SECRETKEY = "sensitive-secretkey"
TOKEN = "sensitive-token"


def make_config(*, appkey=APPKEY, secretkey=SECRETKEY):
    return KiwoomConfig(
        mode="real",
        base_url="https://api.kiwoom.com",
        app_env="pytest",
        appkey=appkey,
        secretkey=secretkey,
    )


def make_response(status_code=200, payload=None, json_error=None):
    response = Mock()
    response.status_code = status_code
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = payload or {}
    return response


def assert_no_sensitive_values(result, *, include_token=True):
    result_text = repr(result)
    assert APPKEY not in result_text
    assert SECRETKEY not in result_text
    if include_token:
        assert TOKEN not in result_text


def assert_error_is_sanitized(result):
    assert APPKEY not in result.errors
    assert SECRETKEY not in result.errors
    assert TOKEN not in result.errors
    assert_no_sensitive_values(result)


def test_missing_appkey_returns_error_without_token_request(monkeypatch):
    post = Mock()
    monkeypatch.setattr("auth.requests.post", post)
    auth = KiwoomAuth(make_config(appkey=None))

    result = auth.issue_access_token()

    post.assert_not_called()
    assert result["system"] == "ERROR"
    assert result["token_issued"] is False
    assert result["access_token_exists"] is False
    assert result.access_token is None
    assert_error_is_sanitized(result)


def test_missing_secretkey_returns_error_without_token_request(monkeypatch):
    post = Mock()
    monkeypatch.setattr("auth.requests.post", post)
    auth = KiwoomAuth(make_config(secretkey=None))

    result = auth.issue_access_token()

    post.assert_not_called()
    assert result["system"] == "ERROR"
    assert result["token_issued"] is False
    assert result["access_token_exists"] is False
    assert result.access_token is None
    assert_error_is_sanitized(result)


def test_non_200_status_returns_error(monkeypatch):
    monkeypatch.setattr("auth.requests.post", Mock(return_value=make_response(status_code=401)))
    auth = KiwoomAuth(make_config())

    result = auth.issue_access_token()

    assert result["system"] == "ERROR"
    assert result["token_issued"] is False
    assert result["access_token_exists"] is False
    assert result.access_token is None
    assert result["errors"] == ["Token request failed with status 401"]
    assert_error_is_sanitized(result)


def test_network_error_returns_sanitized_error(monkeypatch):
    raw_error = f"failed with {APPKEY} {SECRETKEY} {TOKEN}"
    request_exception = auth_module.requests.RequestException(raw_error)
    monkeypatch.setattr("auth.requests.post", Mock(side_effect=request_exception))
    auth = KiwoomAuth(make_config())

    result = auth.issue_access_token()

    assert result.system == "ERROR"
    assert result.access_token is None
    assert result.errors == ["Token request failed"]
    assert_error_is_sanitized(result)
    assert APPKEY not in auth.last_error
    assert SECRETKEY not in auth.last_error
    assert TOKEN not in auth.last_error


def test_invalid_json_response_returns_error(monkeypatch):
    monkeypatch.setattr(
        "auth.requests.post",
        Mock(return_value=make_response(status_code=200, json_error=ValueError(f"bad json {TOKEN}"))),
    )
    auth = KiwoomAuth(make_config())

    result = auth.issue_access_token()

    assert result["system"] == "ERROR"
    assert result["token_issued"] is False
    assert result["access_token_exists"] is False
    assert result.access_token is None
    assert result["errors"] == ["Token response was not valid JSON"]
    assert_error_is_sanitized(result)


def test_token_field_returns_access_token_on_success(monkeypatch):
    monkeypatch.setattr("auth.requests.post", Mock(return_value=make_response(payload={"token": TOKEN})))
    auth = KiwoomAuth(make_config())

    result = auth.issue_access_token()

    assert result["system"] == "OK"
    assert result["token_issued"] is True
    assert result["access_token_exists"] is True
    assert result.access_token == TOKEN
    assert result["access_token"] == TOKEN
    public_result = result.to_public_dict()
    assert "access_token" not in public_result
    assert_no_sensitive_values(public_result)


def test_access_token_field_returns_access_token_for_compatibility(monkeypatch):
    monkeypatch.setattr("auth.requests.post", Mock(return_value=make_response(payload={"access_token": TOKEN})))
    auth = KiwoomAuth(make_config())

    result = auth.issue_access_token()

    assert result["system"] == "OK"
    assert result["token_issued"] is True
    assert result["access_token_exists"] is True
    assert result.access_token == TOKEN
    public_result = result.to_public_dict()
    assert "access_token" not in public_result
    assert_no_sensitive_values(public_result)


def test_missing_token_fields_returns_error(monkeypatch):
    monkeypatch.setattr("auth.requests.post", Mock(return_value=make_response(payload={"expires_in": 3600})))
    auth = KiwoomAuth(make_config())

    result = auth.issue_access_token()

    assert result["system"] == "ERROR"
    assert result["token_issued"] is False
    assert result["access_token_exists"] is False
    assert result.access_token is None
    assert result["errors"] == ["Token response did not include token or access_token"]
    assert_error_is_sanitized(result)
