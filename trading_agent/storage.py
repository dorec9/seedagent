"""PostgreSQL-backed storage for market data snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

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
    from .ohlcv import OhlcvBar, ensure_aware_utc
except ImportError:  # pragma: no cover - supports direct script-style imports
    from market_data import StockQuote
    from ohlcv import OhlcvBar, ensure_aware_utc


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

    def initialize_ohlcv(self) -> None:
        """Create the OHLCV bar table and lookup indexes if needed."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS ohlcv_bars (
                            id BIGSERIAL PRIMARY KEY,
                            stock_code TEXT NOT NULL,
                            stock_name TEXT,
                            interval TEXT NOT NULL,
                            bar_time TIMESTAMPTZ NOT NULL,
                            open_price BIGINT NOT NULL,
                            high_price BIGINT NOT NULL,
                            low_price BIGINT NOT NULL,
                            close_price BIGINT NOT NULL,
                            volume BIGINT,
                            trading_value BIGINT,
                            raw JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            UNIQUE (stock_code, interval, bar_time)
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_ohlcv_bars_stock_interval_bar_time
                        ON ohlcv_bars (stock_code, interval, bar_time DESC)
                        """
                    )
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to initialize OHLCV storage") from exc
            raise

    def save_ohlcv_bar(self, bar: OhlcvBar) -> int:
        """Persist one OHLCV bar with upsert semantics and return its row id."""
        return self.save_ohlcv_bars([bar])[0]

    def save_ohlcv_bars(self, bars: Sequence[OhlcvBar]) -> list[int]:
        """Persist OHLCV bars and return their row ids."""
        ids: list[int] = []
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for bar in bars:
                        cur.execute(
                            """
                            INSERT INTO ohlcv_bars (
                                stock_code, stock_name, interval, bar_time, open_price,
                                high_price, low_price, close_price, volume, trading_value, raw
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (stock_code, interval, bar_time) DO UPDATE SET
                                stock_name = EXCLUDED.stock_name,
                                open_price = EXCLUDED.open_price,
                                high_price = EXCLUDED.high_price,
                                low_price = EXCLUDED.low_price,
                                close_price = EXCLUDED.close_price,
                                volume = EXCLUDED.volume,
                                trading_value = EXCLUDED.trading_value,
                                raw = EXCLUDED.raw
                            RETURNING id
                            """,
                            (
                                bar.stock_code,
                                bar.stock_name,
                                bar.interval,
                                ensure_aware_utc(bar.bar_time),
                                bar.open_price,
                                bar.high_price,
                                bar.low_price,
                                bar.close_price,
                                bar.volume,
                                bar.trading_value,
                                Jsonb(bar.raw),
                            ),
                        )
                        row = cur.fetchone()
                        if row is None:
                            raise StorageError("Failed to save OHLCV bar")
                        ids.append(int(row[0]))
                    return ids
        except StorageError:
            raise
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to save OHLCV bar") from None
            raise

    def get_ohlcv_bars(
        self,
        stock_code: str,
        interval: str = "1d",
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10000,
    ) -> list[OhlcvBar]:
        """Return OHLCV bars ordered oldest to newest."""
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        start_utc = _ensure_aware_utc(start) if start is not None else None
        end_utc = _ensure_aware_utc(end) if end is not None else None
        try:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT stock_code, stock_name, interval, bar_time, open_price,
                               high_price, low_price, close_price, volume, trading_value, raw
                        FROM ohlcv_bars
                        WHERE stock_code = %s
                          AND interval = %s
                          AND (%s::timestamptz IS NULL OR bar_time >= %s)
                          AND (%s::timestamptz IS NULL OR bar_time <= %s)
                        ORDER BY bar_time ASC, id ASC
                        LIMIT %s
                        """,
                        (stock_code, interval, start_utc, start_utc, end_utc, end_utc, limit),
                    )
                    rows = cur.fetchall()
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to read OHLCV bars") from None
            raise
        return [_row_to_ohlcv_bar(row) for row in rows]

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
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    ids: list[int] = []
                    for quote in quotes:
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
                        ids.append(int(row[0]))
                    return ids
        except StorageError:
            raise
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to save quote snapshot") from None
            raise

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
                raise StorageError("Failed to read quote snapshot") from None
            raise

        if row is None:
            return None
        return _row_to_snapshot(row)


    def get_recent_snapshots(
        self,
        stock_code: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[QuoteSnapshot]:
        """Return recent snapshots for one stock ordered oldest to newest."""
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        since_utc = _ensure_aware_utc(since) if since is not None else None
        try:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    if since_utc is None:
                        cur.execute(
                            """
                            SELECT id, stock_code, stock_name, current_price, change_price,
                                   change_rate, volume, observed_at, raw
                            FROM (
                                SELECT id, stock_code, stock_name, current_price, change_price,
                                       change_rate, volume, observed_at, raw
                                FROM price_snapshots
                                WHERE stock_code = %s
                                ORDER BY observed_at DESC, id DESC
                                LIMIT %s
                            ) recent
                            ORDER BY observed_at ASC, id ASC
                            """,
                            (stock_code, limit),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT id, stock_code, stock_name, current_price, change_price,
                                   change_rate, volume, observed_at, raw
                            FROM (
                                SELECT id, stock_code, stock_name, current_price, change_price,
                                       change_rate, volume, observed_at, raw
                                FROM price_snapshots
                                WHERE stock_code = %s AND observed_at >= %s
                                ORDER BY observed_at DESC, id DESC
                                LIMIT %s
                            ) recent
                            ORDER BY observed_at ASC, id ASC
                            """,
                            (stock_code, since_utc, limit),
                        )
                    rows = cur.fetchall()
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to read quote snapshots") from None
            raise

        return [_row_to_snapshot(row) for row in rows]


    def get_snapshots_between(
        self,
        stock_code: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10000,
    ) -> list[QuoteSnapshot]:
        """Return snapshots for one stock in a time range ordered oldest to newest."""
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        start_utc = _ensure_aware_utc(start) if start is not None else None
        end_utc = _ensure_aware_utc(end) if end is not None else None

        try:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT id, stock_code, stock_name, current_price, change_price,
                               change_rate, volume, observed_at, raw
                        FROM price_snapshots
                        WHERE stock_code = %s
                          AND (%s::timestamptz IS NULL OR observed_at >= %s)
                          AND (%s::timestamptz IS NULL OR observed_at <= %s)
                        ORDER BY observed_at ASC, id ASC
                        LIMIT %s
                        """,
                        (stock_code, start_utc, start_utc, end_utc, end_utc, limit),
                    )
                    rows = cur.fetchall()
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to read quote snapshots") from None
            raise

        return [_row_to_snapshot(row) for row in rows]

    def get_latest_quotes(self, stock_codes: list[str]) -> dict[str, QuoteSnapshot]:
        """Return latest snapshots for multiple stock codes keyed by stock code."""
        unique_codes = list(dict.fromkeys(stock_codes))
        if not unique_codes:
            return {}
        try:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (stock_code)
                               id, stock_code, stock_name, current_price, change_price,
                               change_rate, volume, observed_at, raw
                        FROM price_snapshots
                        WHERE stock_code = ANY(%s)
                        ORDER BY stock_code, observed_at DESC, id DESC
                        """,
                        (unique_codes,),
                    )
                    rows = cur.fetchall()
        except Exception as exc:
            if _is_database_error(exc):
                raise StorageError("Failed to read quote snapshots") from None
            raise

        return {row["stock_code"]: _row_to_snapshot(row) for row in rows}

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


def _row_to_ohlcv_bar(row: dict[str, Any]) -> OhlcvBar:
    raw = row["raw"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    return OhlcvBar(
        stock_code=row["stock_code"],
        stock_name=row["stock_name"],
        interval=row["interval"],
        bar_time=_ensure_aware_utc(row["bar_time"]),
        open_price=row["open_price"],
        high_price=row["high_price"],
        low_price=row["low_price"],
        close_price=row["close_price"],
        volume=row["volume"],
        trading_value=row["trading_value"],
        raw=raw,
    )


def _is_database_error(exc: Exception) -> bool:
    return psycopg is not None and isinstance(exc, psycopg.Error)
