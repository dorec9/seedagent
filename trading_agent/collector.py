"""Polling collector that fetches and stores quote snapshots."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

try:
    from .market_data import KiwoomAPIError, KiwoomMarketData
    from .storage import QuoteSnapshotStore, StorageError
except ImportError:  # pragma: no cover - supports direct script-style imports
    from market_data import KiwoomAPIError, KiwoomMarketData
    from storage import QuoteSnapshotStore, StorageError


@dataclass(frozen=True)
class QuotePollingConfig:
    """Configuration for repeatedly collecting quote snapshots."""

    stock_codes: tuple[str, ...]
    interval_seconds: float = 5.0
    per_request_delay_seconds: float = 0.0
    max_iterations: int | None = None
    fail_fast: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "stock_codes", tuple(self.stock_codes))
        if not self.stock_codes:
            raise ValueError("stock_codes must not be empty")
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than 0")
        if self.per_request_delay_seconds < 0:
            raise ValueError("per_request_delay_seconds must be greater than or equal to 0")
        if self.max_iterations is not None and self.max_iterations < 1:
            raise ValueError("max_iterations must be greater than or equal to 1")


@dataclass(frozen=True)
class QuoteCollectionResult:
    """Summary of one quote collection iteration."""

    iteration: int
    requested_count: int
    saved_count: int
    snapshot_ids: list[int]
    observed_at: datetime


class QuotePollingCollector:
    """Fetch quotes for configured stock codes and save them on a polling loop."""

    def __init__(
        self,
        market_data: KiwoomMarketData,
        store: QuoteSnapshotStore,
        config: QuotePollingConfig,
    ) -> None:
        self.market_data = market_data
        self.store = store
        self.config = config

    def collect_once(self, access_token: str, iteration: int = 1) -> QuoteCollectionResult:
        """Fetch and store one batch of quotes."""
        observed_at = datetime.now(timezone.utc)
        quotes = self.market_data.get_stock_quotes(
            self.config.stock_codes,
            access_token,
            delay_seconds=self.config.per_request_delay_seconds,
        )
        snapshot_ids = self.store.save_quotes(quotes, observed_at=observed_at)
        return QuoteCollectionResult(
            iteration=iteration,
            requested_count=len(self.config.stock_codes),
            saved_count=len(snapshot_ids),
            snapshot_ids=snapshot_ids,
            observed_at=observed_at,
        )

    def run(self, access_token: str) -> list[QuoteCollectionResult]:
        """Run the polling loop until max_iterations is reached, or forever when unset.

        Results are only accumulated when ``max_iterations`` is set. In indefinite
        mode (``max_iterations=None``) results are discarded after each iteration to
        avoid unbounded memory growth.
        """
        results: list[QuoteCollectionResult] = []
        iteration = 1

        while self.config.max_iterations is None or iteration <= self.config.max_iterations:
            try:
                result = self.collect_once(access_token, iteration=iteration)
                if self.config.max_iterations is not None:
                    results.append(result)
            except (KiwoomAPIError, StorageError):
                if self.config.fail_fast:
                    raise

            should_continue = self.config.max_iterations is None or iteration < self.config.max_iterations
            if should_continue:
                time.sleep(self.config.interval_seconds)
            iteration += 1

        return results
