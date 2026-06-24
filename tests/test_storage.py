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


def test_storage_integration_tests_skip_without_test_database_url(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL is not set; skipping PostgreSQL integration tests")


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
