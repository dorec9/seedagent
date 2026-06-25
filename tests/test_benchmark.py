import pytest

from trading_agent.backtest import BacktestConfig, BacktestEngine, BacktestError
from trading_agent.benchmark import BenchmarkConfig, BenchmarkEngine, BenchmarkResult


def row(stock_code, date, proba, current=100, future=110, label=1, exit_date=None):
    exit_date = exit_date or date
    return {
        "stock_code": stock_code,
        "evaluated_at": f"{date}T09:00:00",
        "current_close": current,
        "future_close": future,
        "future_at": f"{exit_date}T15:30:00",
        "future_return_pct": (future - current) / current * 100,
        "label": label,
        "proba": proba,
    }


def records():
    return [
        row("A", "2024-01-02", 0.9, future=112, exit_date="2024-01-03"),
        row("B", "2024-01-02", 0.2, future=90, label=0, exit_date="2024-01-03"),
        row("C", "2024-01-02", 0.5, future=105, exit_date="2024-01-03"),
        row("A", "2024-02-02", 0.1, future=95, label=0, exit_date="2024-02-03"),
        row("B", "2024-02-02", 0.8, future=113, exit_date="2024-02-03"),
        row("C", "2024-02-02", 0.4, future=101, exit_date="2024-02-03"),
        row("A", "2025-01-02", 0.7, future=108, exit_date="2025-01-03"),
        row("B", "2025-01-02", 0.3, future=92, label=0, exit_date="2025-01-03"),
    ]


def test_equal_weight_benchmark_does_not_use_proba():
    cfg = BenchmarkConfig(top_k=1)
    shuffled_proba = [{**r, "proba": 1 - float(r["proba"])} for r in records()]

    first = BenchmarkEngine.run_equal_weight(records(), cfg)
    second = BenchmarkEngine.run_equal_weight(shuffled_proba, cfg)

    assert first.net_total_return_pct == pytest.approx(second.net_total_return_pct)
    assert first.total_trades == second.total_trades


def test_random_top_k_benchmark_is_reproducible_with_fixed_seed():
    cfg = BenchmarkConfig(top_k=2, random_seed=7, random_trials=5)

    first = BenchmarkEngine.run_random_top_k_trials(records(), cfg)
    second = BenchmarkEngine.run_random_top_k_trials(records(), cfg)

    assert first == second


def test_random_top_k_selects_no_more_than_top_k_per_day():
    cfg = BenchmarkConfig(top_k=2, random_seed=1, random_trials=1)
    selected = BenchmarkEngine._select_random_by_day(records(), cfg.top_k, __import__("random").Random(cfg.random_seed))
    counts = {}
    for item in selected:
        counts.setdefault(item["evaluated_at"][:10], 0)
        counts[item["evaluated_at"][:10]] += 1
    assert max(counts.values()) <= 2


def test_inverse_signal_selects_lowest_proba_first():
    cfg = BenchmarkConfig(top_k=1)
    result = BenchmarkEngine.run_inverse_signal(records(), cfg)

    assert [trade.stock_code for trade in BacktestEngine._simulate_trades(
        BenchmarkEngine._select_by_day(records(), key=lambda r: float(r["proba"]), reverse=False, limit=1),
        BenchmarkEngine._backtest_config(cfg, "top_k_per_day"),
    )[0]] == ["B", "A", "B"]
    assert result.total_trades == 3


def test_model_top_k_and_benchmark_use_same_cost_conditions():
    cfg = BenchmarkConfig(top_k=1, fee_rate=0.01, tax_rate=0.02, slippage_rate=0.03)
    model = BenchmarkEngine.run_model_top_k(records(), cfg)
    direct = BacktestEngine.run(records(), BacktestConfig(strategy="top_k_per_day", top_k=1, fee_rate=0.01, tax_rate=0.02, slippage_rate=0.03))

    assert model.net_total_return_pct == pytest.approx(direct.net_total_return_pct)


def test_benchmark_result_contains_core_metrics():
    comparison = BenchmarkEngine.run(records(), BenchmarkConfig(top_k=1, random_trials=3))

    assert isinstance(comparison.model_strategy, BenchmarkResult)
    assert hasattr(comparison.model_strategy, "net_total_return_pct")
    assert hasattr(comparison.model_strategy, "max_drawdown_pct")
    assert hasattr(comparison.model_strategy, "profit_factor")


def test_monthly_performance_breakdown_by_period():
    result = BacktestEngine.run(records(), BacktestConfig(strategy="top_k_per_day", top_k=1))

    breakdown = BenchmarkEngine.monthly_breakdown(result)

    assert [row["period"] for row in breakdown] == ["2024-01", "2024-02", "2025-01"]
    assert all("profit_factor" in row for row in breakdown)


def test_yearly_performance_breakdown_by_period():
    result = BacktestEngine.run(records(), BacktestConfig(strategy="top_k_per_day", top_k=1))

    breakdown = BenchmarkEngine.yearly_breakdown(result)

    assert [row["period"] for row in breakdown] == ["2024", "2025"]


def test_walk_forward_helper_returns_time_window_results():
    rows = BenchmarkEngine.walk_forward(records(), BenchmarkConfig(top_k=1), frequency="yearly")

    assert len(rows) == 2
    assert rows[0]["period_start"] == "2024-01-02T09:00:00"
    assert "max_drawdown_pct" in rows[0]


def test_empty_data_is_safe():
    comparison = BenchmarkEngine.run([], BenchmarkConfig(random_trials=2))
    assert comparison.model_strategy.total_trades == 0
    assert BenchmarkEngine.walk_forward([], BenchmarkConfig()) == []


def test_missing_required_column_raises_clear_error():
    bad = row("A", "2024-01-02", 0.9)
    del bad["proba"]
    with pytest.raises(BacktestError, match="missing required columns: proba"):
        BenchmarkEngine.run([bad], BenchmarkConfig())


def test_random_trials_less_than_one_raises_error():
    with pytest.raises(BacktestError, match="random_trials"):
        BenchmarkConfig(random_trials=0)


def test_top_k_less_than_one_raises_error():
    with pytest.raises(BacktestError, match="top_k"):
        BenchmarkConfig(top_k=0)
