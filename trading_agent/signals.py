"""Candidate feature calculation and rule-based signal filtering."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

try:
    from .storage import QuoteSnapshotStore
except ImportError:  # pragma: no cover - supports direct script-style imports
    from storage import QuoteSnapshotStore


@dataclass(frozen=True)
class CandidateFilterConfig:
    """Configuration for rule-based candidate filtering."""

    lookback_minutes: float = 5.0
    min_price_change_pct: float = 1.0
    max_price_change_pct: float | None = 8.0
    min_volume_delta: int = 10000
    min_current_price: int | None = 1000
    max_current_price: int | None = 500000
    min_snapshot_count: int = 2
    max_snapshots_per_window: int = 1000
    max_latest_age_seconds: float | None = 30.0

    def __post_init__(self) -> None:
        if self.lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be greater than 0")
        if self.min_snapshot_count < 2:
            raise ValueError("min_snapshot_count must be at least 2")
        if self.min_volume_delta < 0:
            raise ValueError("min_volume_delta must be greater than or equal to 0")
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
        if self.max_price_change_pct is not None and self.max_price_change_pct < self.min_price_change_pct:
            raise ValueError("max_price_change_pct must be greater than or equal to min_price_change_pct")
        if self.max_latest_age_seconds is not None and self.max_latest_age_seconds <= 0:
            raise ValueError("max_latest_age_seconds must be greater than 0")
        if self.max_snapshots_per_window <= 0:
            raise ValueError("max_snapshots_per_window must be greater than 0")
        if self.max_snapshots_per_window < self.min_snapshot_count:
            raise ValueError("max_snapshots_per_window must be greater than or equal to min_snapshot_count")


@dataclass(frozen=True)
class CandidateFeatures:
    """Computed modeling features for a candidate stock."""

    return_pct: float
    price_change: int
    volume_delta: int | None
    volatility_pct: float | None
    price_position_pct: float | None
    snapshot_count: int
    latest_age_seconds: float


@dataclass(frozen=True)
class CandidateSignal:
    """Rule evaluation result for one stock."""

    stock_code: str
    stock_name: str | None
    current_price: int
    latest_volume: int | None
    baseline_price: int
    baseline_volume: int | None
    observed_at: datetime
    window_start_at: datetime
    features: CandidateFeatures
    passed: bool
    reasons: list[str]
    rule_score: float | None = None
    model_score: float | None = None


class CandidateFilter:
    """Calculate quote features and select rule-based buy candidates."""

    def __init__(self, store: QuoteSnapshotStore, config: CandidateFilterConfig) -> None:
        self.store = store
        self.config = config

    def evaluate_stock(self, stock_code: str, now: datetime | None = None) -> CandidateSignal | None:
        evaluated_at = _ensure_aware_utc(now)
        window_start_at = evaluated_at - timedelta(minutes=self.config.lookback_minutes)

        snapshots = self.store.get_recent_snapshots(
            stock_code, since=window_start_at, limit=self.config.max_snapshots_per_window
        )
        if len(snapshots) < self.config.min_snapshot_count:
            return None

        baseline = snapshots[0]
        latest = snapshots[-1]
        if baseline.current_price <= 0:
            return None

        prices = [snapshot.current_price for snapshot in snapshots]
        price_change = latest.current_price - baseline.current_price
        return_pct = price_change / baseline.current_price * 100
        volume_delta = None
        if latest.volume is not None and baseline.volume is not None:
            volume_delta = latest.volume - baseline.volume
        volatility_pct = _calculate_volatility_pct(prices)
        price_position_pct = _calculate_price_position_pct(latest.current_price, prices)
        latest_observed_at = _ensure_aware_utc(latest.observed_at)
        latest_age_seconds = max((evaluated_at - latest_observed_at).total_seconds(), 0.0)

        features = CandidateFeatures(
            return_pct=return_pct,
            price_change=price_change,
            volume_delta=volume_delta,
            volatility_pct=volatility_pct,
            price_position_pct=price_position_pct,
            snapshot_count=len(snapshots),
            latest_age_seconds=latest_age_seconds,
        )
        passed, reasons, rule_score = self._evaluate_rules(latest.current_price, features)
        return CandidateSignal(
            stock_code=latest.stock_code,
            stock_name=latest.stock_name,
            current_price=latest.current_price,
            latest_volume=latest.volume,
            baseline_price=baseline.current_price,
            baseline_volume=baseline.volume,
            observed_at=latest_observed_at,
            window_start_at=window_start_at,
            features=features,
            passed=passed,
            reasons=reasons,
            rule_score=rule_score,
            model_score=None,
        )

    def evaluate(self, stock_codes: list[str], now: datetime | None = None) -> list[CandidateSignal]:
        evaluated_at = _ensure_aware_utc(now) if now is not None else datetime.now(timezone.utc)
        signals: list[CandidateSignal] = []
        for stock_code in stock_codes:
            signal = self.evaluate_stock(stock_code, now=evaluated_at)
            if signal is not None:
                signals.append(signal)
        return signals

    def get_candidates(self, stock_codes: list[str], now: datetime | None = None) -> list[CandidateSignal]:
        return [signal for signal in self.evaluate(stock_codes, now=now) if signal.passed]

    def _evaluate_rules(self, current_price: int, features: CandidateFeatures) -> tuple[bool, list[str], float]:
        reasons: list[str] = []
        score = 0.0

        price_min_ok = features.return_pct >= self.config.min_price_change_pct
        if price_min_ok:
            reasons.append(f"return_pct >= {self.config.min_price_change_pct}")
        else:
            reasons.append("return_pct below minimum")

        price_max_ok = True
        if self.config.max_price_change_pct is not None:
            price_max_ok = features.return_pct <= self.config.max_price_change_pct
            if price_max_ok:
                reasons.append(f"return_pct <= {self.config.max_price_change_pct}")
            else:
                reasons.append("return_pct above maximum")
        if price_min_ok and price_max_ok:
            score += 0.4

        volume_ok = False
        if features.volume_delta is None:
            reasons.append("volume_delta missing")
        elif features.volume_delta >= self.config.min_volume_delta:
            volume_ok = True
            reasons.append(f"volume_delta >= {self.config.min_volume_delta}")
            score += 0.3
        else:
            reasons.append("volume_delta below minimum")

        price_range_ok = True
        if self.config.min_current_price is not None:
            if current_price >= self.config.min_current_price:
                reasons.append(f"current_price >= {self.config.min_current_price}")
            else:
                price_range_ok = False
                reasons.append("current_price below minimum")
        if self.config.max_current_price is not None:
            if current_price <= self.config.max_current_price:
                reasons.append(f"current_price <= {self.config.max_current_price}")
            else:
                price_range_ok = False
                reasons.append("current_price above maximum")
        if price_range_ok:
            score += 0.2

        freshness_ok = True
        if self.config.max_latest_age_seconds is not None:
            freshness_ok = features.latest_age_seconds <= self.config.max_latest_age_seconds
            if freshness_ok:
                reasons.append("latest snapshot is fresh")
                score += 0.1
            else:
                reasons.append("latest snapshot is stale")

        passed = price_min_ok and price_max_ok and volume_ok and price_range_ok and freshness_ok
        return passed, reasons, min(score, 1.0)


def _ensure_aware_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _calculate_volatility_pct(prices: list[int]) -> float | None:
    if not prices:
        return None
    mean = sum(prices) / len(prices)
    if mean == 0:
        return None
    variance = sum((price - mean) ** 2 for price in prices) / len(prices)
    return math.sqrt(variance) / mean * 100


def _calculate_price_position_pct(latest_price: int, prices: list[int]) -> float | None:
    min_price = min(prices)
    max_price = max(prices)
    if max_price == min_price:
        return None
    return (latest_price - min_price) / (max_price - min_price) * 100
