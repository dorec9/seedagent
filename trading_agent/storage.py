"""PostgreSQL-backed storage for market data snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - exercised when optional dependency is absent
    psycopg = None
    dict_row = None
    Jsonb = None

try:
    from .market_data import StockQuote
except ImportError:  # pragma: no cover - supports direct script-style imports
    from market_data import StockQuote


class StorageError(Exception):
    """Raised when quote snapshot storage fails without exposing secrets."""


@dataclass(frozen=True)
class QuoteSnapshot:
    """A persisted stock quote snapshot."""

    id: int
    stock_code: str
    stock_name: str | None
    current_price: int
    change_price: int | None
    change_rate: float | None
    volume: int | None
    observed_at: datetime
    raw: dict[str, Any]


class QuoteSnapshotStore:
    """Store and retrieve quote snapshots using PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        if not database_url or not str(database_url).strip():
            raise StorageError("PostgreSQL database URL is required for quote snapshot storage")
        self._database_url = database_url

    def initialize(self) -> None:
        """Create the quote snapshot table and lookup index if needed."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS price_snapshots (
                            id BIGSERIAL PRIMARY KEY,
                            stock_code TEXT NOT NULL,
                            stock_name TEXT,
                            current_price BIGINT NOT NULL,
                            change_price BIGINT,
                            change_rate DOUBLE PRECISION,
                            volume BIGINT,
                            observed_at TIMESTAMPTZ NOT NULL,
                            raw JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_price_snapshots_stock_observed_at
                        ON price_snapshots (stock_code, observed_at DESC)
                        """
                    )
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to initialize quote snapshot storage") from exc
            raise

    def save_quote(self, quote: StockQuote, observed_at: datetime | None = None) -> int:
        """Persist one quote snapshot and return its generated row id."""
        saved_at = _ensure_aware_utc(observed_at)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO price_snapshots (
                            stock_code, stock_name, current_price, change_price,
                            change_rate, volume, observed_at, raw
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            quote.stock_code,
                            quote.stock_name,
                            quote.current_price,
                            quote.change_price,
                            quote.change_rate,
                            quote.volume,
                            saved_at,
                            Jsonb(quote.raw),
                        ),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise StorageError("Failed to save quote snapshot")
                    return int(row[0])
        except StorageError:
            raise
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to save quote snapshot") from None
            raise

    def save_quotes(self, quotes: list[StockQuote], observed_at: datetime | None = None) -> list[int]:
        """Persist multiple quote snapshots and return generated row ids."""
        saved_at = _ensure_aware_utc(observed_at)
        return [self.save_quote(quote, saved_at) for quote in quotes]

    def get_latest_quote(self, stock_code: str) -> QuoteSnapshot | None:
        """Return the latest snapshot for a stock code, or None when absent."""
        try:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT id, stock_code, stock_name, current_price, change_price,
                               change_rate, volume, observed_at, raw
                        FROM price_snapshots
                        WHERE stock_code = %s
                        ORDER BY observed_at DESC, id DESC
                        LIMIT 1
                        """,
                        (stock_code,),
                    )
                    row = cur.fetchone()
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to read quote snapshot") from exc
            raise

        if row is None:
            return None
        return _row_to_snapshot(row)

    def _connect(self):
        if psycopg is None:
            raise StorageError("psycopg is required for PostgreSQL quote snapshot storage")
        return psycopg.connect(self._database_url)


def _ensure_aware_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _row_to_snapshot(row: dict[str, Any]) -> QuoteSnapshot:
    raw = row["raw"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    return QuoteSnapshot(
        id=row["id"],
        stock_code=row["stock_code"],
        stock_name=row["stock_name"],
        current_price=row["current_price"],
        change_price=row["change_price"],
        change_rate=row["change_rate"],
        volume=row["volume"],
        observed_at=_ensure_aware_utc(row["observed_at"]),
        raw=raw,
    )


def _is_database_error(exc: Exception) -> bool:
    return psycopg is not None and isinstance(exc, psycopg.Error)
