"""Offline backtest engine for OHLCV model prediction signals."""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


REQUIRED_COLUMNS = {
    "stock_code",
    "evaluated_at",
    "current_close",
    "future_close",
    "future_at",
    "future_return_pct",
    "label",
    "proba",
}
SUPPORTED_STRATEGIES = {"threshold", "top_k_per_day"}


class BacktestError(Exception):
    """Raised when backtest input data or configuration is invalid."""


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration for offline signal backtesting."""

    strategy: str
    threshold: float = 0.6
    top_k: int = 3
    initial_capital: float = 10_000_000
    position_size_pct: float = 0.1
    max_positions_per_day: int | None = None
    fee_rate: float = 0.00015
    tax_rate: float = 0.0018
    slippage_rate: float = 0.0005
    allow_rebuy_same_stock: bool = False

    def __post_init__(self) -> None:
        if self.strategy not in SUPPORTED_STRATEGIES:
            raise BacktestError("strategy must be one of threshold, top_k_per_day")
        if not 0 <= self.threshold <= 1:
            raise BacktestError("threshold must be between 0 and 1")
        if self.top_k < 1:
            raise BacktestError("top_k must be at least 1")
        if self.initial_capital <= 0:
            raise BacktestError("initial_capital must be greater than 0")
        if self.position_size_pct <= 0 or self.position_size_pct > 1:
            raise BacktestError("position_size_pct must be greater than 0 and less than or equal to 1")
        if self.max_positions_per_day is not None and self.max_positions_per_day < 1:
            raise BacktestError("max_positions_per_day must be at least 1 when provided")
        if self.fee_rate < 0 or self.tax_rate < 0 or self.slippage_rate < 0:
            raise BacktestError("fee_rate, tax_rate, and slippage_rate must be non-negative")


@dataclass(frozen=True)
class BacktestTrade:
    """A completed simulated trade created from one prediction row."""

    stock_code: str
    entry_at: object
    exit_at: object
    entry_price: float
    exit_price: float
    gross_return_pct: float
    net_return_pct: float
    position_value: float
    profit_loss: float
    proba: float
    label: int


@dataclass(frozen=True)
class BacktestResult:
    """Aggregate metrics and rows produced by a backtest run."""

    trades: list[BacktestTrade]
    total_trades: int
    win_rate: float
    gross_total_return_pct: float
    net_total_return_pct: float
    final_equity: float
    max_drawdown_pct: float
    avg_trade_return_pct: float
    median_trade_return_pct: float
    profit_factor: float
    avg_win_pct: float
    avg_loss_pct: float
    equity_curve: list[dict[str, object]]


class BacktestEngine:
    """Runs offline backtests from prediction records or CSV files."""

    @staticmethod
    def run_from_csv(path: str | Path, config: BacktestConfig) -> BacktestResult:
        with Path(path).open(newline="", encoding="utf-8") as file_obj:
            return BacktestEngine.run(list(csv.DictReader(file_obj)), config)

    @staticmethod
    def run(records: Sequence[dict[str, object]], config: BacktestConfig) -> BacktestResult:
        BacktestEngine._validate_records(records)
        selected_records = BacktestEngine._select_records(records, config)
        trades, equity_curve = BacktestEngine._simulate_trades(selected_records, config)
        return BacktestEngine._build_result(trades, equity_curve, config.initial_capital)

    @staticmethod
    def write_trades_csv(result: BacktestResult, path: str | Path) -> None:
        fieldnames = [
            "stock_code",
            "entry_at",
            "exit_at",
            "entry_price",
            "exit_price",
            "gross_return_pct",
            "net_return_pct",
            "position_value",
            "profit_loss",
            "proba",
            "label",
        ]
        with Path(path).open("w", newline="", encoding="utf-8") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
            writer.writeheader()
            for trade in result.trades:
                writer.writerow({field: getattr(trade, field) for field in fieldnames})

    @staticmethod
    def write_equity_curve_csv(result: BacktestResult, path: str | Path) -> None:
        fieldnames = ["date", "realized_pnl", "equity"]
        with Path(path).open("w", newline="", encoding="utf-8") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(result.equity_curve)

    @staticmethod
    def _validate_records(records: Sequence[dict[str, object]]) -> None:
        for record in records:
            missing = REQUIRED_COLUMNS - set(record)
            if missing:
                raise BacktestError(f"missing required columns: {', '.join(sorted(missing))}")

    @staticmethod
    def _select_records(records: Sequence[dict[str, object]], config: BacktestConfig) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for record in records:
            grouped.setdefault(BacktestEngine._date_key(record["evaluated_at"]), []).append(record)

        selected: list[dict[str, object]] = []
        seen_stocks: set[str] = set()
        for date_key in sorted(grouped):
            unique_by_stock = BacktestEngine._dedupe_same_day_stock(grouped[date_key])
            if not config.allow_rebuy_same_stock:
                unique_by_stock = [row for row in unique_by_stock if str(row["stock_code"]) not in seen_stocks]
            if config.strategy == "threshold":
                candidates = [row for row in unique_by_stock if BacktestEngine._float(row["proba"], "proba") >= config.threshold]
                candidates.sort(key=lambda row: BacktestEngine._float(row["proba"], "proba"), reverse=True)
                if config.max_positions_per_day is not None:
                    candidates = candidates[: config.max_positions_per_day]
            elif config.strategy == "top_k_per_day":
                candidates = sorted(unique_by_stock, key=lambda row: BacktestEngine._float(row["proba"], "proba"), reverse=True)[: config.top_k]
            else:  # pragma: no cover - config validation prevents this path
                raise BacktestError("unsupported strategy")
            selected.extend(candidates)
            if not config.allow_rebuy_same_stock:
                seen_stocks.update(str(row["stock_code"]) for row in candidates)
        return selected

    @staticmethod
    def _dedupe_same_day_stock(records: Sequence[dict[str, object]]) -> list[dict[str, object]]:
        best: dict[str, dict[str, object]] = {}
        for record in records:
            stock_code = str(record["stock_code"])
            if stock_code not in best or BacktestEngine._float(record["proba"], "proba") > BacktestEngine._float(best[stock_code]["proba"], "proba"):
                best[stock_code] = record
        return list(best.values())

    @staticmethod
    def _simulate_trades(records: Sequence[dict[str, object]], config: BacktestConfig) -> tuple[list[BacktestTrade], list[dict[str, object]]]:
        equity = config.initial_capital
        trades: list[BacktestTrade] = []
        equity_curve: list[dict[str, object]] = []
        pending_pnl_by_exit_date: dict[str, float] = {}

        def realize_pnl_through(date_key: str | None = None) -> None:
            nonlocal equity
            due_dates = sorted(
                exit_date for exit_date in pending_pnl_by_exit_date if date_key is None or exit_date <= date_key
            )
            for exit_date in due_dates:
                realized_pnl = pending_pnl_by_exit_date.pop(exit_date)
                equity += realized_pnl
                equity_curve.append({"date": exit_date, "realized_pnl": realized_pnl, "equity": equity})

        records_by_entry_date: dict[str, list[dict[str, object]]] = {}
        for record in records:
            records_by_entry_date.setdefault(BacktestEngine._date_key(record["evaluated_at"]), []).append(record)

        for entry_date in sorted(records_by_entry_date):
            realize_pnl_through(entry_date)
            day_records = records_by_entry_date[entry_date]
            position_value = equity * min(config.position_size_pct, 1 / len(day_records))
            for record in day_records:
                entry_price = BacktestEngine._float(record["current_close"], "current_close")
                exit_price = BacktestEngine._float(record["future_close"], "future_close")
                if entry_price <= 0:
                    raise BacktestError("current_close must be greater than 0")
                gross_return_pct = (exit_price - entry_price) / entry_price * 100
                cost_pct = (config.fee_rate * 2 + config.tax_rate + config.slippage_rate * 2) * 100
                net_return_pct = gross_return_pct - cost_pct
                profit_loss = position_value * net_return_pct / 100
                trade = BacktestTrade(
                    stock_code=str(record["stock_code"]),
                    entry_at=record["evaluated_at"],
                    exit_at=record["future_at"],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    gross_return_pct=gross_return_pct,
                    net_return_pct=net_return_pct,
                    position_value=position_value,
                    profit_loss=profit_loss,
                    proba=BacktestEngine._float(record["proba"], "proba"),
                    label=int(BacktestEngine._float(record["label"], "label")),
                )
                trades.append(trade)
                exit_date = BacktestEngine._date_key(record["future_at"])
                pending_pnl_by_exit_date[exit_date] = pending_pnl_by_exit_date.get(exit_date, 0.0) + profit_loss

        realize_pnl_through()
        return trades, equity_curve

    @staticmethod
    def _build_result(trades: list[BacktestTrade], equity_curve: list[dict[str, object]], initial_capital: float) -> BacktestResult:
        total_pnl = sum(trade.profit_loss for trade in trades)
        final_equity = initial_capital + total_pnl
        gross_returns = [trade.gross_return_pct for trade in trades]
        net_returns = [trade.net_return_pct for trade in trades]
        wins = [value for value in net_returns if value > 0]
        losses = [value for value in net_returns if value < 0]
        gross_profit = sum(trade.profit_loss for trade in trades if trade.profit_loss > 0)
        gross_loss = abs(sum(trade.profit_loss for trade in trades if trade.profit_loss < 0))
        return BacktestResult(
            trades=trades,
            total_trades=len(trades),
            win_rate=len(wins) / len(trades) if trades else 0.0,
            gross_total_return_pct=sum(gross_returns),
            net_total_return_pct=(final_equity - initial_capital) / initial_capital * 100,
            final_equity=final_equity,
            max_drawdown_pct=BacktestEngine._max_drawdown_pct(equity_curve, initial_capital),
            avg_trade_return_pct=sum(net_returns) / len(net_returns) if net_returns else 0.0,
            median_trade_return_pct=statistics.median(net_returns) if net_returns else 0.0,
            profit_factor=(gross_profit / gross_loss) if gross_loss else (float("inf") if gross_profit > 0 else 0.0),
            avg_win_pct=sum(wins) / len(wins) if wins else 0.0,
            avg_loss_pct=sum(losses) / len(losses) if losses else 0.0,
            equity_curve=equity_curve,
        )

    @staticmethod
    def _max_drawdown_pct(equity_curve: Sequence[dict[str, object]], initial_capital: float) -> float:
        peak = initial_capital
        max_drawdown = 0.0
        for point in equity_curve:
            equity = BacktestEngine._float(point["equity"], "equity")
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak * 100 if peak else 0.0
            max_drawdown = max(max_drawdown, drawdown)
        return max_drawdown

    @staticmethod
    def _date_key(value: object) -> str:
        return str(value)[:10]

    @staticmethod
    def _float(value: object, field_name: str) -> float:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise BacktestError(f"{field_name} must be numeric") from exc
