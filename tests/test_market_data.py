import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

from config import KiwoomConfig
from market_data import KiwoomAPIError, KiwoomMarketData, StockQuote

TOKEN = "sensitive-access-token"


def make_config():
    return KiwoomConfig(
        mode="real",
        base_url="https://api.kiwoom.com",
        app_env="pytest",
        appkey="appkey",
        secretkey="secretkey",
    )


def make_response(status_code=200, payload=None, json_error=None):
    response = Mock()
    response.status_code = status_code
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = payload or {}
    return response


def test_get_stock_quote_returns_normalized_quote(monkeypatch):
    payload = {
        "return_code": 0,
        "stk_cd": "005930",
        "stk_nm": "삼성전자",
        "cur_prc": "-68,900",
        "pred_pre": "-1,100",
        "flu_rt": "-1.57",
        "trde_qty": "12,345,678",
    }
    post = Mock(return_value=make_response(payload=payload))
    monkeypatch.setattr("market_data.requests.post", post)

    quote = KiwoomMarketData(make_config()).get_stock_quote("005930", TOKEN)

    assert quote == StockQuote(
        stock_code="005930",
        stock_name="삼성전자",
        current_price=68900,
        change_price=-1100,
        change_rate=-1.57,
        volume=12345678,
        raw=payload,
    )
    post.assert_called_once_with(
        "https://api.kiwoom.com/api/dostk/stkinfo",
        json={"stk_cd": "005930"},
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {TOKEN}",
            "api-id": "ka10001",
        },
        timeout=10.0,
    )


def test_get_stock_quote_raises_on_http_error_without_token(monkeypatch):
    monkeypatch.setattr("market_data.requests.post", Mock(return_value=make_response(status_code=500)))

    with pytest.raises(KiwoomAPIError) as exc_info:
        KiwoomMarketData(make_config()).get_stock_quote("005930", TOKEN)

    assert "HTTP status 500" in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)


def test_get_stock_quote_raises_on_json_parse_error_without_token(monkeypatch):
    monkeypatch.setattr(
        "market_data.requests.post",
        Mock(return_value=make_response(json_error=ValueError("bad json"))),
    )

    with pytest.raises(KiwoomAPIError) as exc_info:
        KiwoomMarketData(make_config()).get_stock_quote("005930", TOKEN)

    assert "not valid JSON" in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)


def test_get_stock_quote_raises_on_non_zero_return_code_without_token(monkeypatch):
    monkeypatch.setattr(
        "market_data.requests.post",
        Mock(return_value=make_response(payload={"return_code": 1, "return_msg": "invalid symbol"})),
    )

    with pytest.raises(KiwoomAPIError) as exc_info:
        KiwoomMarketData(make_config()).get_stock_quote("005930", TOKEN)

    assert "return_code=1" in str(exc_info.value)
    assert "invalid symbol" in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)


def test_get_stock_quote_raises_on_request_error_without_token(monkeypatch):
    monkeypatch.setattr("market_data.requests.post", Mock(side_effect=TimeoutError("timed out")))

    with pytest.raises(KiwoomAPIError) as exc_info:
        KiwoomMarketData(make_config()).get_stock_quote("005930", TOKEN)

    assert "Stock quote request failed" in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)
