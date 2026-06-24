"""Historical training dataset construction from stored quote snapshots."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .signals import CandidateFeatures
    from .storage import QuoteSnapshot, QuoteSnapshotStore, StorageError
except ImportError:  # pragma: no cover - supports direct script-style imports
    from signals import CandidateFeatures
    from storage import QuoteSnapshot, QuoteSnapshotStore, StorageError


@dataclass(frozen=True)
class TrainingDatasetConfig:
    """Configuration for historical training example generation."""

    lookback_minutes: float = 5.0
    prediction_horizon_minutes: float = 10.0
    target_return_pct: float = 1.0
    min_snapshot_count: int = 2
    max_snapshots_per_window: int = 1000
    max_snapshots_per_stock: int = 10000
    max_future_lag_seconds: float | None = 60.0
    min_current_price: int | None = None
    max_current_price: int | None = None

    def __post_init__(self) -> None:
        if self.lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be greater than 0")
        if self.prediction_horizon_minutes <= 0:
            raise ValueError("prediction_horizon_minutes must be greater than 0")
        if self.min_snapshot_count < 2:
            raise ValueError("min_snapshot_count must be at least 2")
        if self.max_snapshots_per_window <= 0:
            raise ValueError("max_snapshots_per_window must be greater than 0")
        if self.max_snapshots_per_window < self.min_snapshot_count:
            raise ValueError("max_snapshots_per_window must be greater than or equal to min_snapshot_count")
        if self.max_snapshots_per_stock <= 0:
            raise ValueError("max_snapshots_per_stock must be greater than 0")
        if self.max_future_lag_seconds is not None and self.max_future_lag_seconds <= 0:
            raise ValueError("max_future_lag_seconds must be greater than 0")
        if self.min_current_price is not None and self.min_current_price <= 0:
            raise ValueError("min_current_price must be greater than 0")
        if self.max_current_price is not None and self.max_current_price <= 0:
            raise ValueError("max_current_price must be greater than 0")
        if (
            self.min_current_price is not None
            and self.max_current_price is not None
            and self.min_current_price > self.max_current_price
        ):
            raise ValueError("min_current_price must be less than or equal to max_current_price")


@dataclass(frozen=True)
class TrainingExample:
    """One supervised learning example built from historical snapshots."""

    stock_code: str
    stock_name: str | None
    evaluated_at: datetime
    current_price: int
    future_at: datetime
    future_price: int
    future_return_pct: float
    label: int
    features: CandidateFeatures


class TrainingDatasetBuilder:
    """Build leak-free historical training examples from quote snapshots."""

    CSV_COLUMNS = [
        "stock_code",
        "stock_name",
        "evaluated_at",
        "current_price",
        "future_at",
        "future_price",
        "future_return_pct",
        "label",
        "return_pct",
        "price_change",
        "volume_delta",
        "volatility_pct",
        "price_position_pct",
        "snapshot_count",
        "latest_age_seconds",
    ]

    def __init__(self, store: QuoteSnapshotStore, config: TrainingDatasetConfig) -> None:
        self.store = store
        self.config = config

    def build_for_stock(
        self,
        stock_code: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[TrainingExample]:
        start_utc = _ensure_aware_utc(start) if start is not None else None
        end_utc = _ensure_aware_utc(end) if end is not None else None
        snapshots = self.store.get_snapshots_between(
            stock_code,
            start=start_utc,
            end=end_utc,
            limit=self.config.max_snapshots_per_stock,
        )
        snapshots = sorted(snapshots, key=lambda snapshot: (_ensure_aware_utc(snapshot.observed_at), snapshot.id))
        examples: list[TrainingExample] = []
        lookback_delta = timedelta(minutes=self.config.lookback_minutes)
        horizon_delta = timedelta(minutes=self.config.prediction_horizon_minutes)

        for idx, current in enumerate(snapshots):
            evaluated_at = _ensure_aware_utc(current.observed_at)
            current_price = current.current_price
            if current_price <= 0 or not self._current_price_allowed(current_price):
                continue

            window_start = evaluated_at - lookback_delta
            window = [
                snapshot
                for snapshot in snapshots[: idx + 1]
                if window_start <= _ensure_aware_utc(snapshot.observed_at) <= evaluated_at
            ]
            if len(window) < self.config.min_snapshot_count:
                continue
            if len(window) > self.config.max_snapshots_per_window:
                window = window[-self.config.max_snapshots_per_window :]

            target_future_at = evaluated_at + horizon_delta
            future = next(
                (snapshot for snapshot in snapshots[idx + 1 :] if _ensure_aware_utc(snapshot.observed_at) >= target_future_at),
                None,
            )
            if future is None:
                continue
            future_at = _ensure_aware_utc(future.observed_at)
            if self.config.max_future_lag_seconds is not None:
                lag_seconds = (future_at - target_future_at).total_seconds()
                if lag_seconds > self.config.max_future_lag_seconds:
                    continue
            future_price = future.current_price
            if future_price <= 0:
                continue

            future_return_pct = (future_price - current_price) / current_price * 100
            examples.append(
                TrainingExample(
                    stock_code=current.stock_code,
                    stock_name=current.stock_name,
                    evaluated_at=evaluated_at,
                    current_price=current_price,
                    future_at=future_at,
                    future_price=future_price,
                    future_return_pct=future_return_pct,
                    label=1 if future_return_pct >= self.config.target_return_pct else 0,
                    features=_calculate_features(window),
                )
            )
        return examples

    def build(
        self,
        stock_codes: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[TrainingExample]:
        examples: list[TrainingExample] = []
        for stock_code in stock_codes:
            examples.extend(self.build_for_stock(stock_code, start=start, end=end))
        return examples

    def to_records(self, examples: list[TrainingExample]) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for example in examples:
            features = example.features
            records.append(
                {
                    "stock_code": example.stock_code,
                    "stock_name": example.stock_name,
                    "evaluated_at": _ensure_aware_utc(example.evaluated_at).isoformat(),
                    "current_price": example.current_price,
                    "future_at": _ensure_aware_utc(example.future_at).isoformat(),
                    "future_price": example.future_price,
                    "future_return_pct": example.future_return_pct,
                    "label": example.label,
                    "return_pct": features.return_pct,
                    "price_change": features.price_change,
                    "volume_delta": features.volume_delta,
                    "volatility_pct": features.volatility_pct,
                    "price_position_pct": features.price_position_pct,
                    "snapshot_count": features.snapshot_count,
                    "latest_age_seconds": features.latest_age_seconds,
                }
            )
        return records

    def write_csv(self, examples: list[TrainingExample], path: str | Path) -> None:
        with Path(path).open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=self.CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(self.to_records(examples))

    def _current_price_allowed(self, current_price: int) -> bool:
        if self.config.min_current_price is not None and current_price < self.config.min_current_price:
            return False
        if self.config.max_current_price is not None and current_price > self.config.max_current_price:
            return False
        return True


def _calculate_features(window: list[QuoteSnapshot]) -> CandidateFeatures:
    baseline = window[0]
    latest = window[-1]
    prices = [snapshot.current_price for snapshot in window]
    price_change = latest.current_price - baseline.current_price
    return_pct = price_change / baseline.current_price * 100
    volume_delta = None
    if latest.volume is not None and baseline.volume is not None:
        volume_delta = latest.volume - baseline.volume
    volatility_pct = _calculate_volatility_pct(prices)
    price_position_pct = _calculate_price_position_pct(latest.current_price, prices)
    return CandidateFeatures(
        return_pct=return_pct,
        price_change=price_change,
        volume_delta=volume_delta,
        volatility_pct=volatility_pct,
        price_position_pct=price_position_pct,
        snapshot_count=len(window),
        latest_age_seconds=0.0,
    )


def _calculate_volatility_pct(prices: list[int]) -> float | None:
    if not prices:
        return None
    mean = sum(prices) / len(prices)
    if mean <= 0:
        return None
    variance = sum((price - mean) ** 2 for price in prices) / len(prices)
    return math.sqrt(variance) / mean * 100


def _calculate_price_position_pct(latest_price: int, prices: list[int]) -> float | None:
    min_price = min(prices)
    max_price = max(prices)
    if max_price == min_price:
        return None
    return (latest_price - min_price) / (max_price - min_price) * 100


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
