from datetime import datetime, timezone
from unittest.mock import Mock, call

import pytest

from collector import QuoteCollectionResult, QuotePollingCollector, QuotePollingConfig
from market_data import KiwoomAPIError, StockQuote
from storage import StorageError

TOKEN = "sensitive-access-token"


def make_quote(stock_code="005930"):
    return StockQuote(
        stock_code=stock_code,
        stock_name="삼성전자",
        current_price=70000,
        change_price=1000,
        change_rate=1.45,
        volume=12345678,
        raw={"stk_cd": stock_code, "stk_nm": "삼성전자", "cur_prc": "70000"},
    )


def make_collector(config=None, quotes=None, snapshot_ids=None):
    market_data = Mock()
    market_data.get_stock_quotes.return_value = quotes if quotes is not None else [make_quote()]
    store = Mock()
    store.save_quotes.return_value = snapshot_ids if snapshot_ids is not None else [101]
    collector = QuotePollingCollector(
        market_data=market_data,
        store=store,
        config=config or QuotePollingConfig(stock_codes=["005930"]),
    )
    return collector, market_data, store


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stock_codes": []},
        {"stock_codes": ["005930"], "interval_seconds": 0},
        {"stock_codes": ["005930"], "interval_seconds": -1},
        {"stock_codes": ["005930"], "per_request_delay_seconds": -0.1},
        {"stock_codes": ["005930"], "max_iterations": 0},
        {"stock_codes": ["005930"], "max_iterations": -1},
    ],
)
def test_quote_polling_config_validates_invalid_values(kwargs):
    with pytest.raises(ValueError):
        QuotePollingConfig(**kwargs)


def test_quote_polling_config_preserves_order_and_duplicates():
    config = QuotePollingConfig(stock_codes=["005930", "000660", "005930"])

    assert config.stock_codes == ["005930", "000660", "005930"]


def test_collect_once_fetches_and_saves_quotes():
    quotes = [make_quote("005930"), make_quote("000660")]
    config = QuotePollingConfig(
        stock_codes=["005930", "000660"],
        per_request_delay_seconds=0.2,
    )
    collector, market_data, store = make_collector(config=config, quotes=quotes, snapshot_ids=[11, 12])

    result = collector.collect_once(TOKEN, iteration=7)

    market_data.get_stock_quotes.assert_called_once_with(
        ["005930", "000660"],
        TOKEN,
        delay_seconds=0.2,
    )
    store.save_quotes.assert_called_once_with(quotes, observed_at=result.observed_at)
    assert isinstance(result, QuoteCollectionResult)
    assert result.iteration == 7
    assert result.requested_count == 2
    assert result.saved_count == 2
    assert result.snapshot_ids == [11, 12]
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset() == timezone.utc.utcoffset(result.observed_at)


def test_run_uses_max_iterations_and_sleeps_between_iterations_only(monkeypatch):
    config = QuotePollingConfig(stock_codes=["005930"], interval_seconds=5.0, max_iterations=3)
    collector, _, _ = make_collector(config=config)
    collect_once = Mock(side_effect=lambda access_token, iteration: QuoteCollectionResult(
        iteration=iteration,
        requested_count=1,
        saved_count=1,
        snapshot_ids=[iteration],
        observed_at=datetime.now(timezone.utc),
    ))
    collector.collect_once = collect_once
    sleep = Mock()
    monkeypatch.setattr("collector.time.sleep", sleep)

    results = collector.run(TOKEN)

    assert [result.iteration for result in results] == [1, 2, 3]
    assert collect_once.call_args_list == [call(TOKEN, iteration=1), call(TOKEN, iteration=2), call(TOKEN, iteration=3)]
    assert sleep.call_args_list == [call(5.0), call(5.0)]


def test_run_propagates_kiwoom_api_error_when_fail_fast_true():
    collector, market_data, _ = make_collector()
    market_data.get_stock_quotes.side_effect = KiwoomAPIError("quote lookup failed")

    with pytest.raises(KiwoomAPIError) as exc_info:
        collector.run(TOKEN)

    assert TOKEN not in str(exc_info.value)


def test_run_propagates_storage_error_when_fail_fast_true():
    collector, _, store = make_collector()
    store.save_quotes.side_effect = StorageError("failed to save quote snapshot")

    with pytest.raises(StorageError) as exc_info:
        collector.run(TOKEN)

    assert TOKEN not in str(exc_info.value)
