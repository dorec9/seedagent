"""Benchmark and walk-forward helpers for OHLCV prediction backtests."""

from __future__ import annotations

import csv
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from trading_agent.backtest import BacktestConfig, BacktestEngine, BacktestError, BacktestResult, REQUIRED_COLUMNS


@dataclass(frozen=True)
class BenchmarkConfig:
    initial_capital: float = 10_000_000
    position_size_pct: float = 0.1
    top_k: int = 3
    random_seed: int = 42
    fee_rate: float = 0.00015
    tax_rate: float = 0.0018
    slippage_rate: float = 0.0005
    random_trials: int = 100
    allow_rebuy_same_stock: bool = False

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise BacktestError("initial_capital must be greater than 0")
        if self.position_size_pct <= 0 or self.position_size_pct > 1:
            raise BacktestError("position_size_pct must be greater than 0 and less than or equal to 1")
        if self.top_k < 1:
            raise BacktestError("top_k must be at least 1")
        if self.random_trials < 1:
            raise BacktestError("random_trials must be at least 1")
        if self.fee_rate < 0 or self.tax_rate < 0 or self.slippage_rate < 0:
            raise BacktestError("fee_rate, tax_rate, and slippage_rate must be non-negative")


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    total_trades: int
    win_rate: float
    net_total_return_pct: float
    final_equity: float
    max_drawdown_pct: float
    avg_trade_return_pct: float
    median_trade_return_pct: float
    profit_factor: float


@dataclass(frozen=True)
class BenchmarkComparison:
    model_strategy: BenchmarkResult
    equal_weight: BenchmarkResult
    buy_all: BenchmarkResult
    inverse_signal: BenchmarkResult
    random_top_k_mean: BenchmarkResult
    random_top_k_median_return_pct: float
    random_top_k_p95_return_pct: float
    random_top_k_p05_return_pct: float
    model_excess_vs_equal_weight_pct: float
    model_excess_vs_random_mean_pct: float


class BenchmarkEngine:
    """Runs model-vs-baseline comparisons using the existing BacktestEngine rules."""

    @staticmethod
    def run_from_csv(path: str | Path, config: BenchmarkConfig = BenchmarkConfig()) -> BenchmarkComparison:
        with Path(path).open(newline="", encoding="utf-8") as file_obj:
            return BenchmarkEngine.run(list(csv.DictReader(file_obj)), config)

    @staticmethod
    def run(records: Sequence[dict[str, object]], config: BenchmarkConfig = BenchmarkConfig()) -> BenchmarkComparison:
        BenchmarkEngine._validate_records(records)
        model = BenchmarkEngine.run_model_top_k(records, config)
        equal = BenchmarkEngine.run_equal_weight(records, config)
        buy_all = BenchmarkEngine.run_buy_all(records, config)
        inverse = BenchmarkEngine.run_inverse_signal(records, config)
        random_results = BenchmarkEngine.run_random_top_k_trials(records, config)
        random_mean = BenchmarkEngine._mean_result("random_top_k_mean", random_results)
        returns = [result.net_total_return_pct for result in random_results]
        return BenchmarkComparison(
            model_strategy=model,
            equal_weight=equal,
            buy_all=buy_all,
            inverse_signal=inverse,
            random_top_k_mean=random_mean,
            random_top_k_median_return_pct=statistics.median(returns) if returns else 0.0,
            random_top_k_p95_return_pct=BenchmarkEngine._percentile(returns, 95),
            random_top_k_p05_return_pct=BenchmarkEngine._percentile(returns, 5),
            model_excess_vs_equal_weight_pct=model.net_total_return_pct - equal.net_total_return_pct,
            model_excess_vs_random_mean_pct=model.net_total_return_pct - random_mean.net_total_return_pct,
        )

    @staticmethod
    def run_model_top_k(records: Sequence[dict[str, object]], config: BenchmarkConfig) -> BenchmarkResult:
        result = BacktestEngine.run(records, BenchmarkEngine._backtest_config(config, "top_k_per_day"))
        return BenchmarkEngine._to_benchmark_result("model_top_k", result)

    @staticmethod
    def run_equal_weight(records: Sequence[dict[str, object]], config: BenchmarkConfig) -> BenchmarkResult:
        selected = BenchmarkEngine._select_all(records)
        return BenchmarkEngine._run_selected("equal_weight", selected, config)

    @staticmethod
    def run_buy_all(records: Sequence[dict[str, object]], config: BenchmarkConfig) -> BenchmarkResult:
        selected = BenchmarkEngine._select_all(records)
        return BenchmarkEngine._run_selected("buy_all", selected, config)

    @staticmethod
    def run_inverse_signal(records: Sequence[dict[str, object]], config: BenchmarkConfig) -> BenchmarkResult:
        selected = BenchmarkEngine._select_by_day(records, key=lambda row: BacktestEngine._float(row["proba"], "proba"), reverse=False, limit=config.top_k)
        return BenchmarkEngine._run_selected("inverse_signal", selected, config)

    @staticmethod
    def run_random_top_k_trials(records: Sequence[dict[str, object]], config: BenchmarkConfig) -> list[BenchmarkResult]:
        BenchmarkEngine._validate_records(records)
        rng = random.Random(config.random_seed)
        trials = []
        for trial in range(config.random_trials):
            selected = BenchmarkEngine._select_random_by_day(records, config.top_k, rng)
            trials.append(BenchmarkEngine._run_selected(f"random_top_k_trial_{trial + 1}", selected, config))
        return trials

    @staticmethod
    def write_comparison_csv(comparison: BenchmarkComparison, path: str | Path) -> None:
        rows = [comparison.model_strategy, comparison.equal_weight, comparison.buy_all, comparison.inverse_signal, comparison.random_top_k_mean]
        fieldnames = list(BenchmarkResult.__dataclass_fields__)
        with Path(path).open("w", newline="", encoding="utf-8") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row.__dict__)

    @staticmethod
    def monthly_breakdown(result: BacktestResult) -> list[dict[str, object]]:
        return BenchmarkEngine._period_breakdown(result, 7)

    @staticmethod
    def yearly_breakdown(result: BacktestResult) -> list[dict[str, object]]:
        return BenchmarkEngine._period_breakdown(result, 4)

    @staticmethod
    def walk_forward_from_csv(path: str | Path, config: BenchmarkConfig = BenchmarkConfig(), period: str = "monthly") -> list[dict[str, object]]:
        with Path(path).open(newline="", encoding="utf-8") as file_obj:
            return BenchmarkEngine.walk_forward(list(csv.DictReader(file_obj)), config, period=period)

    @staticmethod
    def walk_forward(records: Sequence[dict[str, object]], config: BenchmarkConfig = BenchmarkConfig(), period: str = "monthly", frequency: str | None = None) -> list[dict[str, object]]:
        if frequency is not None:
            period = frequency
        BenchmarkEngine._validate_records(records)
        if period not in {"monthly", "quarterly", "yearly"}:
            raise BacktestError("period must be monthly, quarterly, or yearly")
        grouped: dict[str, list[dict[str, object]]] = {}
        for record in records:
            key = BenchmarkEngine._period_key(str(record["evaluated_at"]), period)
            grouped.setdefault(key, []).append(record)
        rows = []
        for key in sorted(grouped):
            period_records = sorted(grouped[key], key=lambda row: str(row["evaluated_at"]))
            result = BacktestEngine.run(period_records, BenchmarkEngine._backtest_config(config, "top_k_per_day"))
            rows.append({
                "period_start": min(str(row["evaluated_at"]) for row in period_records) if period_records else "",
                "period_end": max(str(row["evaluated_at"]) for row in period_records) if period_records else "",
                "total_trades": result.total_trades,
                "net_total_return_pct": result.net_total_return_pct,
                "max_drawdown_pct": result.max_drawdown_pct,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
            })
        return rows

    @staticmethod
    def _validate_records(records: Sequence[dict[str, object]]) -> None:
        for record in records:
            missing = REQUIRED_COLUMNS - set(record)
            if missing:
                raise BacktestError(f"missing required columns: {', '.join(sorted(missing))}")

    @staticmethod
    def _backtest_config(config: BenchmarkConfig, strategy: str) -> BacktestConfig:
        return BacktestConfig(strategy=strategy, top_k=config.top_k, initial_capital=config.initial_capital, position_size_pct=config.position_size_pct, fee_rate=config.fee_rate, tax_rate=config.tax_rate, slippage_rate=config.slippage_rate, allow_rebuy_same_stock=config.allow_rebuy_same_stock)

    @staticmethod
    def _run_selected(name: str, selected: Sequence[dict[str, object]], config: BenchmarkConfig) -> BenchmarkResult:
        result = BacktestEngine.run_selected_records(selected, BenchmarkEngine._backtest_config(config, "top_k_per_day"))
        return BenchmarkEngine._to_benchmark_result(name, result)

    @staticmethod
    def _select_all(records: Sequence[dict[str, object]]) -> list[dict[str, object]]:
        return BenchmarkEngine._select_by_day(records, key=lambda row: str(row["stock_code"]), reverse=False, limit=None)

    @staticmethod
    def _select_by_day(records, key, reverse: bool, limit: int | None) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for record in records:
            grouped.setdefault(BacktestEngine._date_key(record["evaluated_at"]), []).append(record)
        selected = []
        for date_key in sorted(grouped):
            rows = BacktestEngine._dedupe_same_day_stock(grouped[date_key])
            rows = sorted(rows, key=key, reverse=reverse)
            selected.extend(rows[:limit] if limit is not None else rows)
        return selected

    @staticmethod
    def _select_random_by_day(records, top_k: int, rng: random.Random) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for record in records:
            grouped.setdefault(BacktestEngine._date_key(record["evaluated_at"]), []).append(record)
        selected = []
        for date_key in sorted(grouped):
            rows = BacktestEngine._dedupe_same_day_stock(grouped[date_key])
            selected.extend(rng.sample(rows, k=min(top_k, len(rows))))
        return selected

    @staticmethod
    def _to_benchmark_result(name: str, result: BacktestResult) -> BenchmarkResult:
        return BenchmarkResult(name, result.total_trades, result.win_rate, result.net_total_return_pct, result.final_equity, result.max_drawdown_pct, result.avg_trade_return_pct, result.median_trade_return_pct, result.profit_factor)

    @staticmethod
    def _mean_result(name: str, results: Sequence[BenchmarkResult]) -> BenchmarkResult:
        if not results:
            return BenchmarkResult(name, 0, 0, 0, 0, 0, 0, 0, 0)
        return BenchmarkResult(name, round(statistics.mean(r.total_trades for r in results)), statistics.mean(r.win_rate for r in results), statistics.mean(r.net_total_return_pct for r in results), statistics.mean(r.final_equity for r in results), statistics.mean(r.max_drawdown_pct for r in results), statistics.mean(r.avg_trade_return_pct for r in results), statistics.mean(r.median_trade_return_pct for r in results), statistics.mean(r.profit_factor for r in results))

    @staticmethod
    def _percentile(values: Sequence[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = (len(ordered) - 1) * percentile / 100
        lower = int(index)
        upper = min(lower + 1, len(ordered) - 1)
        weight = index - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    @staticmethod
    def _period_breakdown(result: BacktestResult, chars: int) -> list[dict[str, object]]:
        grouped = {}
        for trade in result.trades:
            grouped.setdefault(str(trade.exit_at)[:chars], []).append(trade)
        rows = []
        for period in sorted(grouped):
            trades = grouped[period]
            pnl = sum(t.profit_loss for t in trades)
            wins = [t for t in trades if t.net_return_pct > 0]
            gross_profit = sum(t.profit_loss for t in trades if t.profit_loss > 0)
            gross_loss = abs(sum(t.profit_loss for t in trades if t.profit_loss < 0))
            rows.append({
                "period": period,
                "total_trades": len(trades),
                "win_rate": len(wins) / len(trades) if trades else 0.0,
                "net_return_pct": pnl / result.final_equity * 100 if result.final_equity else 0.0,
                "avg_trade_return_pct": sum(t.net_return_pct for t in trades) / len(trades) if trades else 0.0,
                "profit_factor": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit > 0 else 0.0),
            })
        return rows

    @staticmethod
    def _period_key(value: str, period: str) -> str:
        if period == "yearly":
            return value[:4]
        if period == "monthly":
            return value[:7]
        month = int(value[5:7])
        quarter = (month - 1) // 3 + 1
        return f"{value[:4]}-Q{quarter}"
