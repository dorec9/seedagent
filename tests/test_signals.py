import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

from signals import CandidateFilter, CandidateFilterConfig
from storage import QuoteSnapshot, StorageError


NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)


def snapshot(code="005930", price=1000, volume=10000, observed_at=None, row_id=1, name="삼성전자"):
    return QuoteSnapshot(
        id=row_id,
        stock_code=code,
        stock_name=name,
        current_price=price,
        change_price=None,
        change_rate=None,
        volume=volume,
        observed_at=observed_at or NOW,
        raw={"stock_code": code},
    )


class FakeStore:
    def __init__(self, snapshots_by_code=None, error=None):
        self.snapshots_by_code = snapshots_by_code or {}
        self.error = error
        self.calls = []

    def get_recent_snapshots(self, stock_code, since=None, limit=100):
        self.calls.append((stock_code, since, limit))
        if self.error is not None:
            raise self.error
        return list(self.snapshots_by_code.get(stock_code, []))


def make_filter(snaps, config=None):
    return CandidateFilter(FakeStore({"005930": snaps}), config or CandidateFilterConfig(min_volume_delta=100))


def passing_snapshots(code="005930"):
    return [
        snapshot(code=code, price=1000, volume=10000, observed_at=NOW - timedelta(minutes=4), row_id=1),
        snapshot(code=code, price=1020, volume=10200, observed_at=NOW - timedelta(seconds=10), row_id=2),
    ]


def test_candidate_filter_config_defaults():
    config = CandidateFilterConfig()
    assert config.lookback_minutes == 5.0
    assert config.min_price_change_pct == 1.0
    assert config.max_price_change_pct == 8.0
    assert config.min_volume_delta == 10000
    assert config.min_current_price == 1000
    assert config.max_current_price == 500000
    assert config.min_snapshot_count == 2
    assert config.max_snapshots_per_window == 1000
    assert config.max_latest_age_seconds == 30.0


@pytest.mark.parametrize("kwargs", [
    {"lookback_minutes": 0},
    {"lookback_minutes": -1},
    {"min_snapshot_count": 1},
    {"min_volume_delta": -1},
    {"min_current_price": 0},
    {"max_current_price": 0},
    {"min_current_price": 2000, "max_current_price": 1000},
    {"min_price_change_pct": 2.0, "max_price_change_pct": 1.0},
    {"max_latest_age_seconds": 0},
    {"max_snapshots_per_window": 0},
    {"max_snapshots_per_window": -1},
    {"min_snapshot_count": 5, "max_snapshots_per_window": 3},
])
def test_candidate_filter_config_validation(kwargs):
    with pytest.raises(ValueError):
        CandidateFilterConfig(**kwargs)


def test_evaluate_stock_calculates_features_from_recent_snapshots():
    filt = make_filter([
        snapshot(price=1000, volume=10000, observed_at=NOW - timedelta(minutes=4), row_id=1),
        snapshot(price=1010, volume=10100, observed_at=NOW - timedelta(minutes=2), row_id=2),
        snapshot(price=1020, volume=10300, observed_at=NOW - timedelta(seconds=10), row_id=3),
    ], CandidateFilterConfig(min_volume_delta=100, max_latest_age_seconds=30))

    signal = filt.evaluate_stock("005930", now=NOW)

    assert signal is not None
    assert signal.features.return_pct == pytest.approx(2.0)
    assert signal.features.price_change == 20
    assert signal.features.volume_delta == 300
    assert signal.features.volatility_pct == pytest.approx(math.sqrt(((1000-1010)**2+(1010-1010)**2+(1020-1010)**2)/3)/1010*100)
    assert signal.features.price_position_pct == pytest.approx(100.0)
    assert signal.features.snapshot_count == 3
    assert signal.passed is True
    assert signal.model_score is None
    assert 0.0 <= signal.rule_score <= 1.0


def test_return_pct_below_minimum_fails():
    filt = make_filter([snapshot(price=1000, volume=10000, observed_at=NOW - timedelta(minutes=1)), snapshot(price=1005, volume=10200, observed_at=NOW)])
    signal = filt.evaluate_stock("005930", now=NOW)
    assert signal.passed is False
    assert "return_pct below minimum" in signal.reasons


def test_return_pct_above_maximum_fails():
    filt = make_filter([snapshot(price=1000, volume=10000, observed_at=NOW - timedelta(minutes=1)), snapshot(price=1200, volume=10200, observed_at=NOW)])
    signal = filt.evaluate_stock("005930", now=NOW)
    assert signal.passed is False
    assert "return_pct above maximum" in signal.reasons


def test_volume_delta_missing_fails():
    filt = make_filter([snapshot(price=1000, volume=None, observed_at=NOW - timedelta(minutes=1)), snapshot(price=1020, volume=10200, observed_at=NOW)])
    signal = filt.evaluate_stock("005930", now=NOW)
    assert signal.passed is False
    assert "volume_delta missing" in signal.reasons


def test_volume_delta_below_minimum_fails():
    filt = make_filter([snapshot(price=1000, volume=10000, observed_at=NOW - timedelta(minutes=1)), snapshot(price=1020, volume=10050, observed_at=NOW)])
    signal = filt.evaluate_stock("005930", now=NOW)
    assert signal.passed is False
    assert "volume_delta below minimum" in signal.reasons


def test_current_price_below_minimum_fails():
    filt = make_filter([snapshot(price=900, volume=10000, observed_at=NOW - timedelta(minutes=1)), snapshot(price=920, volume=10200, observed_at=NOW)])
    signal = filt.evaluate_stock("005930", now=NOW)
    assert signal.passed is False
    assert "current_price below minimum" in signal.reasons


def test_current_price_above_maximum_fails():
    filt = make_filter([snapshot(price=500000, volume=10000, observed_at=NOW - timedelta(minutes=1)), snapshot(price=510000, volume=10200, observed_at=NOW)])
    signal = filt.evaluate_stock("005930", now=NOW)
    assert signal.passed is False
    assert "current_price above maximum" in signal.reasons


def test_latest_snapshot_stale_fails():
    filt = make_filter([snapshot(price=1000, volume=10000, observed_at=NOW - timedelta(minutes=2)), snapshot(price=1020, volume=10200, observed_at=NOW - timedelta(seconds=31))])
    signal = filt.evaluate_stock("005930", now=NOW)
    assert signal.passed is False
    assert "latest snapshot is stale" in signal.reasons


def test_insufficient_snapshots_returns_none():
    assert make_filter([snapshot()]).evaluate_stock("005930", now=NOW) is None


def test_baseline_price_zero_or_less_returns_none():
    assert make_filter([snapshot(price=0), snapshot(price=100)]).evaluate_stock("005930", now=NOW) is None


def test_evaluate_preserves_input_order_and_duplicates():
    store = FakeStore({
        "A": passing_snapshots("A"),
        "B": passing_snapshots("B"),
    })
    signals = CandidateFilter(store, CandidateFilterConfig(min_volume_delta=100)).evaluate(["B", "A", "B"], now=NOW)
    assert [s.stock_code for s in signals] == ["B", "A", "B"]
    assert [call[0] for call in store.calls] == ["B", "A", "B"]


def test_get_candidates_returns_only_passed_results():
    store = FakeStore({
        "PASS": passing_snapshots("PASS"),
        "FAIL": [snapshot(code="FAIL", price=1000, volume=10000, observed_at=NOW - timedelta(minutes=1)), snapshot(code="FAIL", price=1001, volume=10200, observed_at=NOW)],
    })
    candidates = CandidateFilter(store, CandidateFilterConfig(min_volume_delta=100)).get_candidates(["PASS", "FAIL"], now=NOW)
    assert [candidate.stock_code for candidate in candidates] == ["PASS"]


def test_now_timezone_aware_utc_is_used_for_window_start():
    store = FakeStore({"005930": passing_snapshots()})
    CandidateFilter(store, CandidateFilterConfig(min_volume_delta=100)).evaluate_stock("005930", now=NOW)
    assert store.calls[0][1] == NOW - timedelta(minutes=5)


def test_naive_now_is_treated_as_utc():
    store = FakeStore({"005930": passing_snapshots()})
    naive_now = datetime(2026, 6, 24, 12, 0)
    signal = CandidateFilter(store, CandidateFilterConfig(min_volume_delta=100)).evaluate_stock("005930", now=naive_now)
    assert store.calls[0][1] == NOW - timedelta(minutes=5)
    assert signal.features.latest_age_seconds == 10.0


def test_storage_error_is_propagated_and_not_rewritten_with_secrets():
    safe_error = StorageError("Failed to read quote snapshots")
    filt = CandidateFilter(FakeStore(error=safe_error), CandidateFilterConfig())
    with pytest.raises(StorageError) as exc_info:
        filt.evaluate_stock("005930", now=NOW)
    assert exc_info.value is safe_error
    assert "DATABASE_URL" not in str(exc_info.value)
    assert "access_token" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value).lower()


def test_empty_evaluate_and_get_candidates_return_empty_lists():
    filt = CandidateFilter(FakeStore(), CandidateFilterConfig())
    assert filt.evaluate([], now=NOW) == []
    assert filt.get_candidates([], now=NOW) == []


def test_evaluate_stock_passes_configured_limit_to_store():
    store = FakeStore({"005930": passing_snapshots()})
    config = CandidateFilterConfig(min_volume_delta=100, max_snapshots_per_window=500)
    CandidateFilter(store, config).evaluate_stock("005930", now=NOW)
    assert store.calls[0][2] == 500


def test_evaluate_uses_same_window_start_for_all_stocks():
    store = FakeStore({
        "A": passing_snapshots("A"),
        "B": passing_snapshots("B"),
    })
    signals = CandidateFilter(store, CandidateFilterConfig(min_volume_delta=100)).evaluate(["A", "B"], now=NOW)
    assert len(signals) == 2
    assert signals[0].window_start_at == signals[1].window_start_at
