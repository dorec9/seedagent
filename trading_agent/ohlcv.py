"""Historical OHLCV backfill and training dataset utilities."""

from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from .config import KiwoomConfig
    from .market_data import KiwoomAPIError, _RequestsAdapter, _first_present, _parse_int, _parse_return_code, requests
except ImportError:  # pragma: no cover
    from config import KiwoomConfig
    from market_data import KiwoomAPIError, _RequestsAdapter, _first_present, _parse_int, _parse_return_code, requests

SUPPORTED_OHLCV_INTERVALS = {"1d", "1m", "3m", "5m", "10m"}


@dataclass(frozen=True)
class OhlcvBar:
    stock_code: str
    stock_name: str | None
    interval: str
    bar_time: datetime
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int | None
    trading_value: int | None
    raw: dict[str, object]

    def __post_init__(self) -> None:
        if not self.stock_code or not str(self.stock_code).strip():
            raise ValueError("stock_code is required")
        if self.interval not in SUPPORTED_OHLCV_INTERVALS:
            raise ValueError(f"unsupported OHLCV interval: {self.interval}")
        for field in ("open_price", "high_price", "low_price", "close_price"):
            if getattr(self, field) < 0:
                raise ValueError(f"{field} must be greater than or equal to 0")
        if self.volume is not None and self.volume < 0:
            raise ValueError("volume must be greater than or equal to 0")
        if self.trading_value is not None and self.trading_value < 0:
            raise ValueError("trading_value must be greater than or equal to 0")
        if not isinstance(self.raw, dict):
            raise ValueError("raw must be a dict")
        object.__setattr__(self, "bar_time", ensure_aware_utc(self.bar_time))


class KiwoomHistoricalMarketData:
    """Fetch normalized historical OHLCV bars from Kiwoom REST endpoints."""

    DAILY_CHART_PATH = "/api/dostk/chart"
    DAILY_CHART_API_ID = "ka10081"

    def __init__(self, config: KiwoomConfig, timeout: float = 10.0) -> None:
        self.config = config
        self.timeout = timeout

    def get_daily_ohlcv(
        self,
        stock_code: str,
        access_token: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[OhlcvBar]:
        payload = self._request_daily_chart(stock_code, access_token, end_date=end_date)
        rows = _extract_daily_rows(payload, stock_code)
        bars = [_row_to_daily_bar(row, stock_code) for row in rows]
        if start_date is not None:
            bars = [bar for bar in bars if bar.bar_time.date() >= start_date]
        if end_date is not None:
            bars = [bar for bar in bars if bar.bar_time.date() <= end_date]
        return sorted(bars, key=lambda bar: bar.bar_time)

    def _request_daily_chart(self, stock_code: str, access_token: str, end_date: date | None) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}{self.DAILY_CHART_PATH}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {access_token}",
            "api-id": self.DAILY_CHART_API_ID,
        }
        body = {"stk_cd": stock_code, "base_dt": (end_date or date.today()).strftime("%Y%m%d"), "upd_stkpc_tp": "1"}
        try:
            response = requests.post(url, json=body, headers=headers, timeout=self.timeout)
        except TimeoutError as exc:
            raise KiwoomAPIError(f"Daily OHLCV request failed for stock_code={stock_code}: timed out") from exc
        except _RequestsAdapter.RequestException as exc:
            raise KiwoomAPIError(f"Daily OHLCV request failed for stock_code={stock_code}: network error") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise KiwoomAPIError(f"Daily OHLCV request failed for stock_code={stock_code} with HTTP status {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise KiwoomAPIError(f"Daily OHLCV response was not valid JSON for stock_code={stock_code}") from exc
        if not isinstance(payload, dict):
            raise KiwoomAPIError(f"Daily OHLCV response JSON was not an object for stock_code={stock_code}")
        if _parse_return_code(payload.get("return_code")) != 0:
            raise KiwoomAPIError(f"Kiwoom daily OHLCV API returned return_code={payload.get('return_code')} for stock_code={stock_code}")
        return payload


@dataclass(frozen=True)
class OhlcvBackfillConfig:
    stock_codes: tuple[str, ...]
    interval: str = "1d"
    start_date: date | None = None
    end_date: date | None = None
    per_request_delay_seconds: float = 0.2

    def __post_init__(self) -> None:
        if not self.stock_codes:
            raise ValueError("stock_codes must not be empty")
        if self.interval not in SUPPORTED_OHLCV_INTERVALS:
            raise ValueError(f"unsupported OHLCV interval: {self.interval}")
        if self.interval != "1d":
            raise ValueError("only 1d OHLCV backfill is currently implemented")
        if self.per_request_delay_seconds < 0:
            raise ValueError("per_request_delay_seconds must be greater than or equal to 0")


@dataclass(frozen=True)
class OhlcvBackfillResult:
    stock_code: str
    requested_count: int
    saved_count: int
    row_ids: list[int]
    error: str | None = None


class OhlcvBackfillRunner:
    def __init__(self, market_data: KiwoomHistoricalMarketData, store: Any, config: OhlcvBackfillConfig) -> None:
        self.market_data = market_data
        self.store = store
        self.config = config

    def run(self, access_token: str) -> list[OhlcvBackfillResult]:
        results: list[OhlcvBackfillResult] = []
        for index, stock_code in enumerate(self.config.stock_codes):
            try:
                bars = self.market_data.get_daily_ohlcv(stock_code, access_token, self.config.start_date, self.config.end_date)
                row_ids = self.store.save_ohlcv_bars(bars)
                results.append(OhlcvBackfillResult(stock_code, len(bars), len(row_ids), row_ids))
            except Exception as exc:  # keep processing other symbols
                results.append(OhlcvBackfillResult(stock_code, 0, 0, [], _sanitize_error(str(exc), access_token)))
            if self.config.per_request_delay_seconds > 0 and index < len(self.config.stock_codes) - 1:
                time.sleep(self.config.per_request_delay_seconds)
        return results


@dataclass(frozen=True)
class OhlcvTrainingDatasetConfig:
    interval: str = "1d"
    lookback_bars: int = 60
    prediction_horizon_bars: int = 5
    target_return_pct: float = 1.0
    min_bars: int = 60
    max_bars_per_stock: int = 10000
    include_advanced_features: bool = True
    min_lookback_for_advanced_features: int = 60

    def __post_init__(self) -> None:
        if self.interval not in SUPPORTED_OHLCV_INTERVALS:
            raise ValueError(f"unsupported OHLCV interval: {self.interval}")
        for name in ("lookback_bars", "prediction_horizon_bars", "min_bars", "max_bars_per_stock", "min_lookback_for_advanced_features"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than 0")
        if self.include_advanced_features and self.lookback_bars < self.min_lookback_for_advanced_features:
            raise ValueError("lookback_bars must be at least min_lookback_for_advanced_features when include_advanced_features=True")


@dataclass(frozen=True)
class OhlcvTrainingExample:
    stock_code: str; evaluated_at: datetime; current_close: int
    return_1: float; return_3: float; return_5: float; return_10: float; return_20: float
    volume_change_5: float; volatility_5: float; volatility_20: float; price_position_20: float
    ma_5: float = 0.0; ma_20: float = 0.0; ma_60: float = 0.0
    close_to_ma5_pct: float = 0.0; close_to_ma20_pct: float = 0.0; close_to_ma60_pct: float = 0.0
    ma5_to_ma20_pct: float = 0.0; ma20_to_ma60_pct: float = 0.0; trend_strength_20: float = 0.0
    rsi_14: float = 0.0; macd: float = 0.0; macd_signal: float = 0.0; macd_hist: float = 0.0; momentum_20: float = 0.0; momentum_60: float = 0.0
    bollinger_upper_20: float = 0.0; bollinger_lower_20: float = 0.0; bollinger_position_20: float = 0.5
    range_pct: float = 0.0; avg_range_5: float = 0.0; avg_range_20: float = 0.0; volatility_ratio_5_20: float = 0.0
    volume_ma_5: float = 0.0; volume_ma_20: float = 0.0; volume_ratio_5: float = 0.0; volume_ratio_20: float = 0.0; volume_zscore_20: float = 0.0
    gap_return_pct: float = 0.0; intraday_return_pct: float = 0.0; close_position_in_day: float = 0.5; close_position_20: float = 0.5
    high_breakout_20: float = 0.0; low_breakdown_20: float = 0.0; distance_to_high_20_pct: float = 0.0; distance_from_low_20_pct: float = 0.0
    relative_strength_20: float = 0.0; relative_strength_60: float = 0.0
    future_close: int = 0; future_at: datetime = datetime.min.replace(tzinfo=timezone.utc); future_return_pct: float = 0.0; label: int = 0


class OhlcvTrainingDatasetBuilder:
    BASE_CSV_COLUMNS = ["stock_code", "evaluated_at", "current_close", "return_1", "return_3", "return_5", "return_10", "return_20", "volume_change_5", "volatility_5", "volatility_20", "price_position_20", "future_close", "future_at", "future_return_pct", "label"]
    CSV_COLUMNS = list(OhlcvTrainingExample.__dataclass_fields__.keys())

    def __init__(self, store: Any, config: OhlcvTrainingDatasetConfig) -> None:
        self.store = store; self.config = config

    def build_for_stock(self, stock_code: str, start: datetime | None = None, end: datetime | None = None) -> list[OhlcvTrainingExample]:
        bars = self.store.get_ohlcv_bars(stock_code, self.config.interval, start, end, self.config.max_bars_per_stock)
        bars = sorted(bars, key=lambda b: b.bar_time)
        if len(bars) < self.config.min_bars:
            return []
        out: list[OhlcvTrainingExample] = []
        for idx in range(self.config.lookback_bars - 1, len(bars) - self.config.prediction_horizon_bars):
            window = bars[idx - self.config.lookback_bars + 1 : idx + 1]
            future = bars[idx + self.config.prediction_horizon_bars]
            if any(b.close_price <= 0 or b.open_price <= 0 or b.high_price <= 0 or b.low_price <= 0 for b in window) or future.close_price <= 0:
                continue
            current = bars[idx]
            future_return_pct = (future.close_price - current.close_price) / current.close_price * 100
            out.append(OhlcvTrainingExample(
                stock_code=stock_code, evaluated_at=current.bar_time, current_close=current.close_price,
                return_1=_ret(window, 1), return_3=_ret(window, 3), return_5=_ret(window, 5), return_10=_ret(window, 10), return_20=_ret(window, 20),
                volume_change_5=_volume_change(window, 5), volatility_5=_volatility(window, 5), volatility_20=_volatility(window, 20), price_position_20=_position(window, 20),
                **(_advanced_features(window) if self.config.include_advanced_features else {}),
                future_close=future.close_price, future_at=future.bar_time, future_return_pct=future_return_pct,
                label=1 if future_return_pct >= self.config.target_return_pct else 0,
            ))
        return out

    def build(self, stock_codes: Sequence[str], start: datetime | None = None, end: datetime | None = None) -> list[OhlcvTrainingExample]:
        examples = [ex for code in stock_codes for ex in self.build_for_stock(code, start, end)]
        if self.config.include_advanced_features:
            self._apply_relative_strength(examples)
        return examples

    def to_records(self, examples: Sequence[OhlcvTrainingExample]) -> list[dict[str, object]]:
        return [{k: (_dt(v) if isinstance(v, datetime) else v) for k, v in ex.__dict__.items()} for ex in examples]

    def write_csv(self, examples: Sequence[OhlcvTrainingExample], path: str | Path) -> None:
        columns = self.CSV_COLUMNS if self.config.include_advanced_features else self.BASE_CSV_COLUMNS
        rows = self.to_records(examples)
        if not self.config.include_advanced_features:
            rows = [{key: row[key] for key in columns} for row in rows]
        with Path(path).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columns); w.writeheader(); w.writerows(rows)

    @staticmethod
    def _apply_relative_strength(examples: Sequence[OhlcvTrainingExample]) -> None:
        by_date: dict[datetime, list[OhlcvTrainingExample]] = {}
        for ex in examples:
            by_date.setdefault(ex.evaluated_at, []).append(ex)
        for items in by_date.values():
            avg20 = sum(ex.momentum_20 for ex in items) / len(items) if items else 0.0
            avg60 = sum(ex.momentum_60 for ex in items) / len(items) if items else 0.0
            for ex in items:
                object.__setattr__(ex, "relative_strength_20", ex.momentum_20 - avg20)
                object.__setattr__(ex, "relative_strength_60", ex.momentum_60 - avg60)


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def _dt(v: datetime) -> str: return ensure_aware_utc(v).isoformat()
def _sanitize_error(message: str, token: str) -> str: return message.replace(token, "[REDACTED]") if token else message

def _extract_daily_rows(payload: dict[str, Any], stock_code: str) -> list[dict[str, Any]]:
    rows = _first_present(payload, "stk_dt_pole_chart_qry", "output", "items", "data")
    if not isinstance(rows, list):
        raise KiwoomAPIError(f"Daily OHLCV response did not include chart rows for stock_code={stock_code}")
    if not all(isinstance(row, dict) for row in rows):
        raise KiwoomAPIError(f"Daily OHLCV response included invalid rows for stock_code={stock_code}")
    return rows

def _row_to_daily_bar(row: dict[str, Any], stock_code: str) -> OhlcvBar:
    d = _first_present(row, "dt", "date", "base_dt", "trd_dd")
    if d is None: raise KiwoomAPIError(f"Daily OHLCV row missing date for stock_code={stock_code}")
    try:
        bar_time = datetime.combine(datetime.strptime(str(d).strip(), "%Y%m%d").date(), datetime_time.min, tzinfo=timezone.utc)
    except ValueError as exc:
        raise KiwoomAPIError(f"Daily OHLCV row had invalid date for stock_code={stock_code}") from exc
    vals = [_first_present(row, *keys) for keys in [("open_pric","open_price","open"),("high_pric","high_price","high"),("low_pric","low_price","low"),("cur_prc","close_price","close")]]
    if any(v is None for v in vals): raise KiwoomAPIError(f"Daily OHLCV row missing required OHLC fields for stock_code={stock_code}")
    return OhlcvBar(stock_code=str(_first_present(row,"stk_cd","stock_code") or stock_code), stock_name=_first_present(row,"stk_nm","stock_name"), interval="1d", bar_time=bar_time, open_price=abs(_parse_int(vals[0])), high_price=abs(_parse_int(vals[1])), low_price=abs(_parse_int(vals[2])), close_price=abs(_parse_int(vals[3])), volume=abs(_parse_int(_first_present(row,"trde_qty","volume"))) if _first_present(row,"trde_qty","volume") is not None else None, trading_value=abs(_parse_int(_first_present(row,"trde_prica","trading_value"))) if _first_present(row,"trde_prica","trading_value") is not None else None, raw=row)

def _ret(w: list[OhlcvBar], n: int) -> float:
    if len(w) <= n: return 0.0
    return (w[-1].close_price - w[-1-n].close_price) / w[-1-n].close_price * 100

def _volume_change(w: list[OhlcvBar], n: int) -> float:
    if len(w) <= n or not w[-1-n].volume: return 0.0
    return (((w[-1].volume or 0) - (w[-1-n].volume or 0)) / (w[-1-n].volume or 1)) * 100

def _volatility(w: list[OhlcvBar], n: int) -> float:
    vals = [b.close_price for b in w[-n:]]
    if len(vals) < 2: return 0.0
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) / mean * 100 if mean else 0.0

def _position(w: list[OhlcvBar], n: int) -> float:
    vals = w[-n:]; lo = min(b.low_price for b in vals); hi = max(b.high_price for b in vals)
    return 0.0 if hi == lo else (w[-1].close_price - lo) / (hi - lo) * 100


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0

def _pct(numerator: float, denominator: float) -> float:
    return numerator / denominator * 100 if denominator else 0.0

def _ma(w: list[OhlcvBar], n: int) -> float:
    return _mean([b.close_price for b in w[-n:]])

def _ema(values: Sequence[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = value * alpha + ema * (1 - alpha)
    return ema

def _rsi(w: list[OhlcvBar], n: int = 14) -> float:
    if len(w) <= n:
        return 0.0
    changes = [w[i].close_price - w[i - 1].close_price for i in range(len(w) - n, len(w))]
    gains = [max(c, 0) for c in changes]
    losses = [abs(min(c, 0)) for c in changes]
    avg_gain = _mean(gains); avg_loss = _mean(losses)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - (100 / (1 + avg_gain / avg_loss))

def _advanced_features(w: list[OhlcvBar]) -> dict[str, float]:
    close = w[-1].close_price; prev_close = w[-2].close_price if len(w) > 1 else close
    ma5, ma20, ma60 = _ma(w, 5), _ma(w, 20), _ma(w, 60)
    closes = [b.close_price for b in w]
    macd = _ema(closes, 12) - _ema(closes, 26)
    macd_series = [_ema(closes[:i], 12) - _ema(closes[:i], 26) for i in range(1, len(closes) + 1)]
    macd_signal = _ema(macd_series, 9)
    last20 = w[-20:]
    highs20 = [b.high_price for b in last20]; lows20 = [b.low_price for b in last20]
    high20, low20 = max(highs20), min(lows20)
    std20 = _volatility_abs([b.close_price for b in last20])
    upper = ma20 + 2 * std20; lower = ma20 - 2 * std20
    day_range = w[-1].high_price - w[-1].low_price
    range_pcts = [_pct(b.high_price - b.low_price, b.close_price) for b in w]
    vol5 = _volatility(w, 5); vol20 = _volatility(w, 20)
    vols = [float(b.volume or 0) for b in w]
    vma5, vma20 = _mean(vols[-5:]), _mean(vols[-20:])
    vstd20 = _volatility_abs(vols[-20:])
    prior20 = w[-21:-1]
    prior_high20 = max(b.high_price for b in prior20) if prior20 else high20
    prior_low20 = min(b.low_price for b in prior20) if prior20 else low20
    return {
        "ma_5": ma5, "ma_20": ma20, "ma_60": ma60,
        "close_to_ma5_pct": _pct(close - ma5, ma5), "close_to_ma20_pct": _pct(close - ma20, ma20), "close_to_ma60_pct": _pct(close - ma60, ma60),
        "ma5_to_ma20_pct": _pct(ma5 - ma20, ma20), "ma20_to_ma60_pct": _pct(ma20 - ma60, ma60), "trend_strength_20": _pct(ma20 - _ma(w[:-20] if len(w) > 20 else w, 20), ma20),
        "rsi_14": _rsi(w, 14), "macd": macd, "macd_signal": macd_signal, "macd_hist": macd - macd_signal, "momentum_20": _ret(w, 20), "momentum_60": _ret(w, 60),
        "bollinger_upper_20": upper, "bollinger_lower_20": lower, "bollinger_position_20": 0.5 if upper == lower else (close - lower) / (upper - lower),
        "range_pct": _pct(day_range, close), "avg_range_5": _mean(range_pcts[-5:]), "avg_range_20": _mean(range_pcts[-20:]), "volatility_ratio_5_20": vol5 / vol20 if vol20 else 0.0,
        "volume_ma_5": vma5, "volume_ma_20": vma20, "volume_ratio_5": vols[-1] / vma5 if vma5 else 0.0, "volume_ratio_20": vols[-1] / vma20 if vma20 else 0.0, "volume_zscore_20": (vols[-1] - vma20) / vstd20 if vstd20 else 0.0,
        "gap_return_pct": _pct(w[-1].open_price - prev_close, prev_close), "intraday_return_pct": _pct(close - w[-1].open_price, w[-1].open_price), "close_position_in_day": 0.5 if day_range == 0 else (close - w[-1].low_price) / day_range,
        "close_position_20": 0.5 if high20 == low20 else (close - low20) / (high20 - low20), "high_breakout_20": 1.0 if close > prior_high20 else 0.0, "low_breakdown_20": 1.0 if close < prior_low20 else 0.0,
        "distance_to_high_20_pct": _pct(close - high20, high20), "distance_from_low_20_pct": _pct(close - low20, low20),
    }

def _volatility_abs(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
