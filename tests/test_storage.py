import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

from market_data import StockQuote
from storage import QuoteSnapshotStore, StorageError, psycopg

SECRET_URL = "postgresql://postgres:secret-password@localhost:5432/trading_agent"


def make_quote(stock_code="005930", stock_name="삼성전자", current_price=70000, raw=None):
    return StockQuote(
        stock_code=stock_code,
        stock_name=stock_name,
        current_price=current_price,
        change_price=1000,
        change_rate=1.45,
        volume=12345678,
        raw=raw or {"stk_cd": stock_code, "stk_nm": stock_name, "cur_prc": str(current_price)},
    )


@pytest.fixture
def store():
    database_url = os.getenv("TEST_DATABASE_URL")
    if psycopg is None:
        pytest.skip("psycopg is not installed; skipping PostgreSQL integration tests")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set; skipping PostgreSQL integration tests")
    store = QuoteSnapshotStore(database_url)
    store.initialize()
    with store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE price_snapshots RESTART IDENTITY")
    yield store
    with store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE price_snapshots RESTART IDENTITY")



def test_initialize_creates_price_snapshots_table(store):
    with store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.price_snapshots')")
            assert cur.fetchone()[0] == "price_snapshots"


def test_save_quote_returns_row_id_and_latest_quote(store):
    observed_at = datetime(2026, 6, 24, 1, 2, 3, tzinfo=timezone.utc)

    row_id = store.save_quote(make_quote(), observed_at=observed_at)
    latest = store.get_latest_quote("005930")

    assert row_id == 1
    assert latest is not None
    assert latest.id == row_id
    assert latest.stock_code == "005930"
    assert latest.stock_name == "삼성전자"
    assert latest.current_price == 70000
    assert latest.change_price == 1000
    assert latest.change_rate == 1.45
    assert latest.volume == 12345678
    assert latest.observed_at == observed_at


def test_get_latest_quote_returns_most_recent_observed_at(store):
    old_time = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    new_time = old_time + timedelta(minutes=5)
    store.save_quote(make_quote(current_price=70000), observed_at=old_time)
    latest_id = store.save_quote(make_quote(current_price=71000), observed_at=new_time)

    latest = store.get_latest_quote("005930")

    assert latest is not None
    assert latest.id == latest_id
    assert latest.current_price == 71000
    assert latest.observed_at == new_time


def test_save_quotes_returns_row_ids_for_multiple_stocks(store):
    row_ids = store.save_quotes([
        make_quote("005930", "삼성전자", 70000),
        make_quote("000660", "SK하이닉스", 200000),
    ])

    assert row_ids == [1, 2]
    assert store.get_latest_quote("005930").stock_name == "삼성전자"
    assert store.get_latest_quote("000660").stock_name == "SK하이닉스"


def test_get_latest_quote_returns_none_for_unknown_stock(store):
    assert store.get_latest_quote("999999") is None


def test_raw_jsonb_is_restored_as_dict_and_korean_stock_name_is_preserved(store):
    raw = {"stk_cd": "005930", "stk_nm": "삼성전자", "nested": {"시장": "코스피"}}
    store.save_quote(make_quote(raw=raw))

    latest = store.get_latest_quote("005930")

    assert latest.raw == raw
    assert isinstance(latest.raw, dict)
    assert latest.stock_name == "삼성전자"


def test_database_url_is_not_exposed_in_storage_error(monkeypatch):
    if psycopg is None:
        pytest.skip("psycopg is not installed; skipping psycopg error sanitization test")
    store = QuoteSnapshotStore(SECRET_URL)

    def raise_error():
        raise psycopg.OperationalError(SECRET_URL)

    monkeypatch.setattr(store, "_connect", raise_error)

    with pytest.raises(StorageError) as exc_info:
        store.initialize()

    assert SECRET_URL not in str(exc_info.value)
    assert "secret-password" not in str(exc_info.value)


@pytest.mark.parametrize("database_url", [None, "", "   "])
def test_empty_database_url_raises_safe_error(database_url):
    with pytest.raises(StorageError) as exc_info:
        QuoteSnapshotStore(database_url)

    assert "database URL is required" in str(exc_info.value)
    assert "postgresql://" not in str(exc_info.value)


def test_get_recent_snapshots_returns_observed_at_ascending(store):
    t1 = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(minutes=1)
    t3 = t1 + timedelta(minutes=2)
    store.save_quote(make_quote(current_price=101), observed_at=t2)
    store.save_quote(make_quote(current_price=100), observed_at=t1)
    store.save_quote(make_quote(current_price=102), observed_at=t3)

    recent = store.get_recent_snapshots("005930")

    assert [snapshot.current_price for snapshot in recent] == [100, 101, 102]
    assert [snapshot.observed_at for snapshot in recent] == [t1, t2, t3]


def test_get_recent_snapshots_filters_since(store):
    t1 = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(minutes=1)
    t3 = t1 + timedelta(minutes=2)
    store.save_quote(make_quote(current_price=100), observed_at=t1)
    store.save_quote(make_quote(current_price=101), observed_at=t2)
    store.save_quote(make_quote(current_price=102), observed_at=t3)

    recent = store.get_recent_snapshots("005930", since=t2)

    assert [snapshot.current_price for snapshot in recent] == [101, 102]


def test_get_recent_snapshots_applies_limit(store):
    base = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    for idx in range(3):
        store.save_quote(make_quote(current_price=100 + idx), observed_at=base + timedelta(minutes=idx))

    recent = store.get_recent_snapshots("005930", limit=2)

    assert [snapshot.current_price for snapshot in recent] == [101, 102]


def test_get_recent_snapshots_limit_must_be_positive(store):
    with pytest.raises(ValueError):
        store.get_recent_snapshots("005930", limit=0)


def test_get_recent_snapshots_returns_empty_for_unknown_stock(store):
    assert store.get_recent_snapshots("999999") == []


def test_get_latest_quotes_returns_latest_snapshots_by_stock_code(store):
    base = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    store.save_quote(make_quote("005930", "삼성전자", 70000), observed_at=base)
    store.save_quote(make_quote("005930", "삼성전자", 71000), observed_at=base + timedelta(minutes=1))
    store.save_quote(make_quote("000660", "SK하이닉스", 200000), observed_at=base)
    store.save_quote(make_quote("000660", "SK하이닉스", 201000), observed_at=base + timedelta(minutes=1))

    latest = store.get_latest_quotes(["005930", "000660"])

    assert set(latest) == {"005930", "000660"}
    assert latest["005930"].current_price == 71000
    assert latest["000660"].current_price == 201000


def test_get_latest_quotes_excludes_stocks_without_history(store):
    store.save_quote(make_quote("005930", "삼성전자", 70000))

    latest = store.get_latest_quotes(["005930", "999999"])

    assert set(latest) == {"005930"}


def test_get_latest_quotes_empty_input_returns_empty_dict(store):
    assert store.get_latest_quotes([]) == {}

def test_get_snapshots_between_returns_observed_at_ascending(store):
    t1 = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(minutes=1)
    t3 = t1 + timedelta(minutes=2)
    store.save_quote(make_quote(current_price=101), observed_at=t2)
    store.save_quote(make_quote(current_price=100), observed_at=t1)
    store.save_quote(make_quote(current_price=102), observed_at=t3)

    snapshots = store.get_snapshots_between("005930")

    assert [snapshot.current_price for snapshot in snapshots] == [100, 101, 102]


def test_get_snapshots_between_filters_start(store):
    base = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    for idx in range(3):
        store.save_quote(make_quote(current_price=100 + idx), observed_at=base + timedelta(minutes=idx))

    snapshots = store.get_snapshots_between("005930", start=base + timedelta(minutes=1))

    assert [snapshot.current_price for snapshot in snapshots] == [101, 102]


def test_get_snapshots_between_filters_end(store):
    base = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    for idx in range(3):
        store.save_quote(make_quote(current_price=100 + idx), observed_at=base + timedelta(minutes=idx))

    snapshots = store.get_snapshots_between("005930", end=base + timedelta(minutes=1))

    assert [snapshot.current_price for snapshot in snapshots] == [100, 101]


def test_get_snapshots_between_filters_start_and_end(store):
    base = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    for idx in range(4):
        store.save_quote(make_quote(current_price=100 + idx), observed_at=base + timedelta(minutes=idx))

    snapshots = store.get_snapshots_between("005930", start=base + timedelta(minutes=1), end=base + timedelta(minutes=2))

    assert [snapshot.current_price for snapshot in snapshots] == [101, 102]


def test_get_snapshots_between_applies_limit(store):
    base = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    for idx in range(3):
        store.save_quote(make_quote(current_price=100 + idx), observed_at=base + timedelta(minutes=idx))

    snapshots = store.get_snapshots_between("005930", limit=2)

    assert [snapshot.current_price for snapshot in snapshots] == [100, 101]


def test_get_snapshots_between_limit_must_be_positive(store):
    with pytest.raises(ValueError):
        store.get_snapshots_between("005930", limit=0)


def test_get_snapshots_between_returns_empty_for_unknown_stock(store):
    assert store.get_snapshots_between("999999") == []


def test_get_snapshots_between_treats_naive_datetime_as_utc(store):
    aware = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)
    store.save_quote(make_quote(current_price=100), observed_at=aware)

    snapshots = store.get_snapshots_between("005930", start=datetime(2026, 6, 24, 9, 0), end=datetime(2026, 6, 24, 9, 0))

    assert len(snapshots) == 1
    assert snapshots[0].observed_at == aware


def test_get_snapshots_between_converts_timezone_aware_datetime_to_utc(store):
    plus_nine = timezone(timedelta(hours=9))
    aware = datetime(2026, 6, 24, 0, 0, tzinfo=timezone.utc)
    store.save_quote(make_quote(current_price=100), observed_at=aware)

    snapshots = store.get_snapshots_between(
        "005930",
        start=datetime(2026, 6, 24, 9, 0, tzinfo=plus_nine),
        end=datetime(2026, 6, 24, 9, 0, tzinfo=plus_nine),
    )

    assert len(snapshots) == 1
    assert snapshots[0].observed_at == aware

from ohlcv import OhlcvBar


def make_bar(day=1, close=1000, stock_name="삼성전자", raw=None):
    return OhlcvBar(
        stock_code="005930",
        stock_name=stock_name,
        interval="1d",
        bar_time=datetime(2026, 1, day, tzinfo=timezone.utc),
        open_price=close - 10,
        high_price=close + 10,
        low_price=close - 20,
        close_price=close,
        volume=123,
        trading_value=456,
        raw=raw or {"day": day, "시장": "코스피"},
    )


@pytest.fixture
def ohlcv_store(store):
    store.initialize_ohlcv()
    with store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE ohlcv_bars RESTART IDENTITY")
    return store


def test_initialize_ohlcv_creates_table_and_index(ohlcv_store):
    with ohlcv_store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.ohlcv_bars')")
            assert cur.fetchone()[0] == "ohlcv_bars"
            cur.execute("SELECT to_regclass('public.idx_ohlcv_bars_stock_interval_bar_time')")
            assert cur.fetchone()[0] == "idx_ohlcv_bars_stock_interval_bar_time"


def test_save_ohlcv_bar_inserts_and_duplicate_save_upserts(ohlcv_store):
    first = ohlcv_store.save_ohlcv_bar(make_bar(close=1000))
    second = ohlcv_store.save_ohlcv_bar(make_bar(close=2000, raw={"updated": True}))
    bars = ohlcv_store.get_ohlcv_bars("005930")
    assert first == second
    assert len(bars) == 1
    assert bars[0].close_price == 2000
    assert bars[0].raw == {"updated": True}


def test_save_ohlcv_bars_returns_ordered_ids_and_get_returns_ascending(ohlcv_store):
    ids = ohlcv_store.save_ohlcv_bars([make_bar(3, 3000), make_bar(1, 1000), make_bar(2, 2000)])
    bars = ohlcv_store.get_ohlcv_bars("005930")
    assert len(ids) == 3
    assert [b.close_price for b in bars] == [1000, 2000, 3000]


def test_get_ohlcv_bars_start_end_filter_and_limit_validation(ohlcv_store):
    ohlcv_store.save_ohlcv_bars([make_bar(1, 1000), make_bar(2, 2000), make_bar(3, 3000)])
    bars = ohlcv_store.get_ohlcv_bars("005930", start=datetime(2026, 1, 2), end=datetime(2026, 1, 3, tzinfo=timezone.utc))
    assert [b.close_price for b in bars] == [2000, 3000]
    with pytest.raises(ValueError):
        ohlcv_store.get_ohlcv_bars("005930", limit=0)


def test_ohlcv_korean_stock_name_and_raw_jsonb_roundtrip(ohlcv_store):
    raw = {"stk_nm": "삼성전자", "nested": {"시장": "코스피"}}
    ohlcv_store.save_ohlcv_bar(make_bar(stock_name="삼성전자", raw=raw))
    saved = ohlcv_store.get_ohlcv_bars("005930")[0]
    assert saved.stock_name == "삼성전자"
    assert saved.raw == raw
    assert isinstance(saved.raw, dict)
