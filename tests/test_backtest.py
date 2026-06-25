import csv
import math

import pytest

from trading_agent.backtest import BacktestConfig, BacktestEngine, BacktestError


def row(stock_code, day, proba, current=100, future=110, label=1):
    return {
        "stock_code": stock_code,
        "evaluated_at": f"2024-01-{day:02d}T09:00:00",
        "current_close": current,
        "future_close": future,
        "future_at": f"2024-01-{day + 1:02d}T09:00:00",
        "future_return_pct": (future - current) / current * 100,
        "label": label,
        "proba": proba,
    }


def test_threshold_strategy_selects_by_proba():
    result = BacktestEngine.run([row("A", 1, 0.59), row("B", 1, 0.6), row("C", 1, 0.7)], BacktestConfig(strategy="threshold", threshold=0.6))

    assert [trade.stock_code for trade in result.trades] == ["C", "B"]


def test_top_k_per_day_selects_daily_top_k():
    records = [row("A", 1, 0.9), row("B", 1, 0.8), row("C", 1, 0.7), row("D", 2, 0.6), row("E", 2, 0.5)]

    result = BacktestEngine.run(records, BacktestConfig(strategy="top_k_per_day", top_k=2))

    assert [trade.stock_code for trade in result.trades] == ["A", "B", "D", "E"]


def test_same_day_same_stock_duplicate_removed_by_highest_proba():
    records = [row("A", 1, 0.61, future=101), row("A", 1, 0.9, future=120), row("B", 1, 0.62)]

    result = BacktestEngine.run(records, BacktestConfig(strategy="threshold", threshold=0.6))

    assert [trade.stock_code for trade in result.trades] == ["A", "B"]
    assert result.trades[0].proba == 0.9
    assert result.trades[0].exit_price == 120


def test_trading_costs_are_reflected_in_net_return_pct():
    result = BacktestEngine.run([row("A", 1, 0.9, current=100, future=110)], BacktestConfig(strategy="threshold", threshold=0.6))

    assert result.trades[0].gross_return_pct == 10
    assert result.trades[0].net_return_pct == pytest.approx(9.69)


def test_final_equity_calculation():
    result = BacktestEngine.run([row("A", 1, 0.9, current=100, future=110)], BacktestConfig(strategy="threshold", initial_capital=1_000_000, position_size_pct=0.1))

    assert result.final_equity == pytest.approx(1_009_690)
    assert result.net_total_return_pct == pytest.approx(0.969)


def test_max_drawdown_calculation():
    records = [row("A", 1, 0.9, current=100, future=120), row("B", 2, 0.9, current=100, future=50)]

    result = BacktestEngine.run(records, BacktestConfig(strategy="threshold", initial_capital=1_000_000, position_size_pct=1.0))

    assert result.max_drawdown_pct == pytest.approx(50.31)


def test_win_rate_calculation():
    records = [row("A", 1, 0.9, future=110), row("B", 1, 0.8, future=90)]

    result = BacktestEngine.run(records, BacktestConfig(strategy="threshold"))

    assert result.win_rate == 0.5


def test_profit_factor_calculation():
    records = [row("A", 1, 0.9, future=110), row("B", 1, 0.8, future=95)]

    result = BacktestEngine.run(records, BacktestConfig(strategy="threshold", initial_capital=1_000_000, position_size_pct=0.1))

    assert result.profit_factor == pytest.approx(9690 / 5310)


def test_missing_required_column_raises_backtest_error():
    bad = row("A", 1, 0.9)
    del bad["proba"]

    with pytest.raises(BacktestError):
        BacktestEngine.run([bad], BacktestConfig(strategy="threshold"))


def test_invalid_strategy_raises_backtest_error():
    with pytest.raises(BacktestError):
        BacktestConfig(strategy="bad")


def test_invalid_threshold_raises_backtest_error():
    with pytest.raises(BacktestError):
        BacktestConfig(strategy="threshold", threshold=1.1)


def test_top_k_less_than_one_raises_backtest_error():
    with pytest.raises(BacktestError):
        BacktestConfig(strategy="top_k_per_day", top_k=0)


def test_empty_trade_result_is_not_error():
    result = BacktestEngine.run([row("A", 1, 0.1)], BacktestConfig(strategy="threshold", threshold=0.6))

    assert result.total_trades == 0
    assert result.final_equity == 10_000_000
    assert result.equity_curve == []


def test_trades_csv_writing(tmp_path):
    result = BacktestEngine.run([row("A", 1, 0.9)], BacktestConfig(strategy="threshold"))
    path = tmp_path / "trades.csv"

    BacktestEngine.write_trades_csv(result, path)

    with path.open(newline="", encoding="utf-8") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert rows[0]["stock_code"] == "A"
    assert float(rows[0]["net_return_pct"]) == pytest.approx(9.69)


def test_equity_curve_csv_writing(tmp_path):
    result = BacktestEngine.run([row("A", 1, 0.9)], BacktestConfig(strategy="threshold"))
    path = tmp_path / "equity.csv"

    BacktestEngine.write_equity_curve_csv(result, path)

    with path.open(newline="", encoding="utf-8") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert rows[0]["date"] == "2024-01-02"
    assert float(rows[0]["equity"]) > 10_000_000
