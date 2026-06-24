import csv
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

from datasets import TrainingDatasetBuilder, TrainingDatasetConfig
from storage import QuoteSnapshot, StorageError


BASE = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)


def snap(minutes, price=100, volume=1000, code="005930", name="삼성전자", id=None):
    return QuoteSnapshot(
        id=minutes if id is None else id,
        stock_code=code,
        stock_name=name,
        current_price=price,
        change_price=None,
        change_rate=None,
        volume=volume,
        observed_at=BASE + timedelta(minutes=minutes),
        raw={},
    )


class FakeStore:
    def __init__(self, by_code=None, error=None):
        self.by_code = by_code or {}
        self.error = error
        self.calls = []

    def get_snapshots_between(self, stock_code, start=None, end=None, limit=10000):
        self.calls.append((stock_code, start, end, limit))
        if self.error:
            raise self.error
        return list(self.by_code.get(stock_code, []))[:limit]


def builder(snaps, config=None, code="005930"):
    return TrainingDatasetBuilder(FakeStore({code: snaps}), config or TrainingDatasetConfig())


def test_config_defaults():
    config = TrainingDatasetConfig()
    assert config.lookback_minutes == 5.0
    assert config.prediction_horizon_minutes == 10.0
    assert config.target_return_pct == 1.0


@pytest.mark.parametrize("kwargs", [
    {"lookback_minutes": 0},
    {"prediction_horizon_minutes": 0},
    {"min_snapshot_count": 1},
    {"min_snapshot_count": 3, "max_snapshots_per_window": 2},
    {"max_snapshots_per_stock": 0},
    {"max_future_lag_seconds": 0},
    {"min_current_price": 0},
    {"max_current_price": 0},
    {"min_current_price": 200, "max_current_price": 100},
])
def test_config_validation(kwargs):
    with pytest.raises(ValueError):
        TrainingDatasetConfig(**kwargs)


def test_build_creates_example_and_positive_label_with_future_return():
    examples = builder([snap(0, 100), snap(5, 110), snap(15, 112)]).build_for_stock("005930")
    assert len(examples) == 1
    assert examples[0].evaluated_at == BASE + timedelta(minutes=5)
    assert examples[0].future_return_pct == pytest.approx((112 - 110) / 110 * 100)
    assert examples[0].label == 1


def test_label_zero_when_future_return_below_target():
    examples = builder([snap(0, 100), snap(5, 110), snap(15, 110)]).build_for_stock("005930")
    assert examples[0].label == 0


def test_skips_when_window_is_insufficient():
    examples = builder([snap(5, 110), snap(15, 112)]).build_for_stock("005930")
    assert examples == []


def test_skips_when_future_snapshot_is_missing():
    assert builder([snap(0, 100), snap(5, 110)]).build_for_stock("005930") == []


def test_skips_when_future_snapshot_lag_exceeds_limit():
    config = TrainingDatasetConfig(max_future_lag_seconds=30)
    examples = builder([snap(0, 100), snap(5, 110), snap(16, 112)], config).build_for_stock("005930")
    assert examples == []


@pytest.mark.parametrize("snaps", [
    [snap(0, 100), snap(5, 0), snap(15, 112)],
    [snap(0, 100), snap(5, 110), snap(15, 0)],
])
def test_skips_non_positive_current_or_future_prices(snaps):
    assert builder(snaps).build_for_stock("005930") == []


def test_skips_prices_outside_min_max_filters():
    assert builder([snap(0, 100), snap(5, 110), snap(15, 120)], TrainingDatasetConfig(min_current_price=111)).build_for_stock("005930") == []
    assert builder([snap(0, 100), snap(5, 110), snap(15, 120)], TrainingDatasetConfig(max_current_price=109)).build_for_stock("005930") == []


def test_features_do_not_use_snapshots_after_evaluated_at():
    examples = builder([snap(0, 100), snap(5, 110), snap(6, 1000), snap(15, 112)]).build_for_stock("005930")
    first = examples[0]
    assert first.evaluated_at == BASE + timedelta(minutes=5)
    assert first.features.price_change == 10
    assert first.features.return_pct == pytest.approx(10.0)


def test_volume_delta_and_none_volume():
    ex = builder([snap(0, 100, 10), snap(5, 110, 25), snap(15, 112, 50)]).build_for_stock("005930")[0]
    assert ex.features.volume_delta == 15
    ex_none = builder([snap(0, 100, None), snap(5, 110, 25), snap(15, 112, 50)]).build_for_stock("005930")[0]
    assert ex_none.features.volume_delta is None


def test_volatility_and_price_position():
    ex = builder([snap(0, 100), snap(5, 110), snap(15, 112)]).build_for_stock("005930")[0]
    mean = 105
    expected_volatility = math.sqrt(((100 - mean) ** 2 + (110 - mean) ** 2) / 2) / mean * 100
    assert ex.features.volatility_pct == pytest.approx(expected_volatility)
    assert ex.features.price_position_pct == pytest.approx(100.0)


def test_build_preserves_order_empty_and_duplicates():
    store = FakeStore({
        "A": [snap(0, 100, code="A"), snap(5, 110, code="A"), snap(15, 112, code="A")],
        "B": [snap(0, 200, code="B"), snap(5, 210, code="B"), snap(15, 212, code="B")],
    })
    b = TrainingDatasetBuilder(store, TrainingDatasetConfig())
    assert b.build([]) == []
    assert [e.stock_code for e in b.build(["B", "A", "B"])] == ["B", "A", "B"]


def test_to_records_flattens_and_isoformats_time_fields():
    b = builder([snap(0, 100), snap(5, 110), snap(15, 112)])
    record = b.to_records(b.build_for_stock("005930"))[0]
    assert set(TrainingDatasetBuilder.CSV_COLUMNS).issubset(record)
    assert record["evaluated_at"] == (BASE + timedelta(minutes=5)).isoformat()
    assert record["future_at"] == (BASE + timedelta(minutes=15)).isoformat()


def test_write_csv_creates_file_and_empty_file_has_header(tmp_path):
    b = builder([snap(0, 100), snap(5, 110), snap(15, 112)])
    path = tmp_path / "dataset.csv"
    b.write_csv(b.build_for_stock("005930"), path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 1
    empty = tmp_path / "empty.csv"
    b.write_csv([], empty)
    assert empty.read_text(encoding="utf-8").splitlines()[0].startswith("stock_code,stock_name")


def test_storage_error_propagates_safely():
    error = StorageError("Failed to read quote snapshots")
    b = TrainingDatasetBuilder(FakeStore(error=error), TrainingDatasetConfig())
    with pytest.raises(StorageError) as exc_info:
        b.build_for_stock("005930")
    assert exc_info.value is error
    assert "postgresql://" not in str(exc_info.value)
    assert "password" not in str(exc_info.value).lower()
    assert "access token" not in str(exc_info.value).lower()


def test_start_end_are_normalized_to_utc_before_store_call():
    store = FakeStore({"005930": []})
    b = TrainingDatasetBuilder(store, TrainingDatasetConfig())
    b.build_for_stock("005930", start=datetime(2026, 6, 24, 9), end=datetime(2026, 6, 24, 10))
    assert store.calls[-1][1] == datetime(2026, 6, 24, 9, tzinfo=timezone.utc)
    assert store.calls[-1][2] == datetime(2026, 6, 24, 10, tzinfo=timezone.utc)
    plus_nine = timezone(timedelta(hours=9))
    b.build_for_stock("005930", start=datetime(2026, 6, 24, 18, tzinfo=plus_nine), end=datetime(2026, 6, 24, 19, tzinfo=plus_nine))
    assert store.calls[-1][1] == datetime(2026, 6, 24, 9, tzinfo=timezone.utc)
    assert store.calls[-1][2] == datetime(2026, 6, 24, 10, tzinfo=timezone.utc)
