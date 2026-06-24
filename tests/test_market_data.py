import sys
from pathlib import Path
from unittest.mock import Mock, call

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


def make_quote(stock_code):
    return StockQuote(
        stock_code=stock_code,
        stock_name=f"name-{stock_code}",
        current_price=1000,
        change_price=10,
        change_rate=1.0,
        volume=100,
        raw={"return_code": 0, "stk_cd": stock_code},
    )


def test_get_stock_quotes_returns_empty_list_for_empty_input():
    market_data = KiwoomMarketData(make_config())

    assert market_data.get_stock_quotes([], TOKEN) == []


def test_get_stock_quotes_calls_get_stock_quote_in_input_order(monkeypatch):
    calls = []

    def fake_get_stock_quote(self, stk_cd, access_token):
        calls.append((stk_cd, access_token))
        return make_quote(stk_cd)

    monkeypatch.setattr(KiwoomMarketData, "get_stock_quote", fake_get_stock_quote)

    quotes = KiwoomMarketData(make_config()).get_stock_quotes(["005930", "000660", "005930"], TOKEN)

    assert calls == [("005930", TOKEN), ("000660", TOKEN), ("005930", TOKEN)]
    assert [quote.stock_code for quote in quotes] == ["005930", "000660", "005930"]


def test_get_stock_quotes_returns_stock_quote_list(monkeypatch):
    monkeypatch.setattr(
        KiwoomMarketData,
        "get_stock_quote",
        lambda self, stk_cd, access_token: make_quote(stk_cd),
    )

    quotes = KiwoomMarketData(make_config()).get_stock_quotes(["005930", "000660"], TOKEN)

    assert isinstance(quotes, list)
    assert all(isinstance(quote, StockQuote) for quote in quotes)


def test_get_stock_quotes_fail_fast_on_kiwoom_api_error_without_token(monkeypatch):
    calls = []

    def fake_get_stock_quote(self, stk_cd, access_token):
        calls.append(stk_cd)
        if stk_cd == "000660":
            raise KiwoomAPIError("quote lookup failed for stock_code=000660")
        return make_quote(stk_cd)

    monkeypatch.setattr(KiwoomMarketData, "get_stock_quote", fake_get_stock_quote)

    with pytest.raises(KiwoomAPIError) as exc_info:
        KiwoomMarketData(make_config()).get_stock_quotes(["005930", "000660", "035420"], TOKEN)

    assert calls == ["005930", "000660"]
    assert TOKEN not in str(exc_info.value)


def test_get_stock_quotes_raises_value_error_for_negative_delay_seconds(monkeypatch):
    get_stock_quote = Mock(return_value=make_quote("005930"))
    monkeypatch.setattr(KiwoomMarketData, "get_stock_quote", get_stock_quote)

    with pytest.raises(ValueError):
        KiwoomMarketData(make_config()).get_stock_quotes(["005930"], TOKEN, delay_seconds=-0.1)

    get_stock_quote.assert_not_called()


def test_get_stock_quotes_sleeps_between_requests_when_delay_is_positive(monkeypatch):
    monkeypatch.setattr(
        KiwoomMarketData,
        "get_stock_quote",
        lambda self, stk_cd, access_token: make_quote(stk_cd),
    )
    sleep = Mock()
    monkeypatch.setattr("market_data.time.sleep", sleep)

    KiwoomMarketData(make_config()).get_stock_quotes(["005930", "000660", "035420"], TOKEN, delay_seconds=0.25)

    assert sleep.call_args_list == [call(0.25), call(0.25)]
