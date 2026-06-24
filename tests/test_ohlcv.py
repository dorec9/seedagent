import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

from ohlcv import (
    KiwoomHistoricalMarketData,
    OhlcvBackfillConfig,
    OhlcvBackfillRunner,
    OhlcvBar,
    OhlcvTrainingDatasetBuilder,
    OhlcvTrainingDatasetConfig,
)
from market_data import KiwoomAPIError
from config import KiwoomConfig


def bar(i, close=None, volume=1000, code="005930"):
    p = close if close is not None else 100 + i
    return OhlcvBar(code, "삼성전자", "1d", datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i), max(p, 0), max(p + 1, 0), max(p - 1, 0), p, volume, volume * p, {"i": i})


def test_ohlcv_bar_validation_and_datetime_normalization():
    b = OhlcvBar("005930", "삼성전자", "1d", datetime(2026, 1, 1), 1, 2, 1, 2, 3, 4, {"시장": "코스피"})
    assert b.bar_time.tzinfo == timezone.utc
    with pytest.raises(ValueError):
        OhlcvBar("", None, "1d", datetime.now(), 1, 1, 1, 1, None, None, {})
    with pytest.raises(ValueError):
        OhlcvBar("005930", None, "2d", datetime.now(), 1, 1, 1, 1, None, None, {})


class FakeStore:
    def __init__(self, bars): self.bars = bars
    def get_ohlcv_bars(self, stock_code, interval="1d", start=None, end=None, limit=10000):
        return [b for b in self.bars if b.stock_code == stock_code][:limit]


def test_dataset_builder_prevents_future_leakage_and_labels_correctly():
    bars = [bar(i, 100) for i in range(20)] + [bar(20, 120)]
    cfg = OhlcvTrainingDatasetConfig(lookback_bars=20, prediction_horizon_bars=1, min_bars=20, target_return_pct=10)
    examples = OhlcvTrainingDatasetBuilder(FakeStore(bars), cfg).build_for_stock("005930")
    assert len(examples) == 1
    ex = examples[0]
    assert ex.evaluated_at == bars[19].bar_time
    assert ex.current_close == 100
    assert ex.future_close == 120
    assert ex.future_return_pct == 20
    assert ex.label == 1
    assert ex.return_1 == 0


def test_dataset_builder_skips_zero_or_negative_price_windows():
    bars = [bar(i, 100) for i in range(21)]
    bars[5] = bar(5, 0)
    cfg = OhlcvTrainingDatasetConfig(lookback_bars=20, prediction_horizon_bars=1, min_bars=20)
    assert OhlcvTrainingDatasetBuilder(FakeStore(bars), cfg).build_for_stock("005930") == []


def test_csv_writing_includes_header_for_empty_and_non_empty(tmp_path):
    builder = OhlcvTrainingDatasetBuilder(FakeStore([]), OhlcvTrainingDatasetConfig())
    path = tmp_path / "empty.csv"
    builder.write_csv([], path)
    assert path.read_text(encoding="utf-8").startswith("stock_code,evaluated_at")

    bars = [bar(i, 100 + i) for i in range(31)]
    examples = OhlcvTrainingDatasetBuilder(FakeStore(bars), OhlcvTrainingDatasetConfig(prediction_horizon_bars=1)).build_for_stock("005930")
    path2 = tmp_path / "data.csv"
    builder.write_csv(examples, path2)
    assert "future_return_pct,label" in path2.read_text(encoding="utf-8").splitlines()[0]


def test_backfill_error_does_not_expose_access_token():
    class BadMarket:
        def get_daily_ohlcv(self, stock_code, access_token, start_date, end_date):
            raise KiwoomAPIError(f"boom {access_token}")
    class Store:
        def save_ohlcv_bars(self, bars): return []
    token = "secret-token"
    runner = OhlcvBackfillRunner(BadMarket(), Store(), OhlcvBackfillConfig(("005930",), per_request_delay_seconds=0))
    result = runner.run(token)[0]
    assert result.error is not None
    assert token not in result.error


def test_kiwoom_daily_ohlcv_parses_rows_and_filters_dates(monkeypatch):
    class Resp:
        status_code = 200
        def json(self):
            return {"return_code": 0, "stk_dt_pole_chart_qry": [
                {"dt": "20260101", "open_pric": "+1,000", "high_pric": "1,100", "low_pric": "900", "cur_prc": "1,050", "trde_qty": "10", "stk_nm": "삼성전자"},
                {"dt": "20260102", "open_pric": "2", "high_pric": "3", "low_pric": "1", "cur_prc": "2", "trde_qty": "20"},
            ]}
    monkeypatch.setattr("ohlcv.requests.post", lambda *a, **k: Resp())
    client = KiwoomHistoricalMarketData(KiwoomConfig(mode="real", base_url="https://example.test", app_env="test", appkey=None, secretkey=None))
    bars = client.get_daily_ohlcv("005930", "token", start_date=date(2026, 1, 2))
    assert len(bars) == 1
    assert bars[0].close_price == 2


def test_kiwoom_error_does_not_expose_access_token(monkeypatch):
    class Resp:
        status_code = 500
        def json(self): return {}
    monkeypatch.setattr("ohlcv.requests.post", lambda *a, **k: Resp())
    client = KiwoomHistoricalMarketData(KiwoomConfig(mode="real", base_url="https://example.test", app_env="test", appkey=None, secretkey=None))
    with pytest.raises(KiwoomAPIError) as exc:
        client.get_daily_ohlcv("005930", "secret-token")
    assert "secret-token" not in str(exc.value)
