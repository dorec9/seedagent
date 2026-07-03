"""Utilities for trading bot stability, including rate limiting and retries."""

from __future__ import annotations

import time
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple rate limiter to ensure we don't exceed API limits (e.g., 5 req/sec)."""

    def __init__(self, max_calls: int, period: float) -> None:
        self.max_calls = max_calls
        self.period = period
        self.calls: list[float] = []

    def wait(self) -> None:
        """Block until a call is allowed."""
        now = time.time()
        # Remove calls older than the period
        self.calls = [t for t in self.calls if now - t < self.period]
        
        if len(self.calls) >= self.max_calls:
            # We hit the limit, calculate how long to sleep
            oldest_call = self.calls[0]
            sleep_time = self.period - (now - oldest_call)
            if sleep_time > 0:
                time.sleep(sleep_time)
            
            # Re-evaluate
            now = time.time()
            self.calls = [t for t in self.calls if now - t < self.period]
            
        self.calls.append(now)


# Global limiter for Kiwoom REST API: ~5 requests per second
kiwoom_rate_limiter = RateLimiter(max_calls=4, period=1.0)


def retry_api_call(func: Callable[..., Any], max_retries: int = 3, backoff_factor: float = 0.5, *args: Any, **kwargs: Any) -> Any:
    """Execute an API call with exponential backoff on failure."""
    from .market_data import KiwoomAPIError
    
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except KiwoomAPIError as e:
            last_exc = e
            # Usually 429 Too Many Requests or 5xx are retryable
            if "status 429" in str(e) or "status 5" in str(e) or "timeout" in str(e).lower():
                sleep_time = backoff_factor * (2 ** attempt)
                logger.warning(f"API Call failed ({e}). Retrying in {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(sleep_time)
            else:
                # 401 Unauthorized or 400 Bad Request shouldn't simply retry without fixing the token/payload
                raise
        except Exception as e:
            last_exc = e
            sleep_time = backoff_factor * (2 ** attempt)
            logger.warning(f"Unexpected error ({e}). Retrying in {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
            time.sleep(sleep_time)
            
    if last_exc:
        raise last_exc
