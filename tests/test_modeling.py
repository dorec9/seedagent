import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

from datasets import TrainingExample
from modeling import (
    DEFAULT_FEATURE_COLUMNS,
    BaselineModelTrainer,
    ClassificationMetrics,
    ModelTrainingConfig,
    ModelTrainingError,
)
from signals import CandidateFeatures

BASE = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)


def records(count=12, one_validation_class=False, include_future=True, future_horizon_minutes=0):
    rows = []
    labels = [0, 1] * ((count + 1) // 2)
    for i in range(count):
        label = labels[i]
        if one_validation_class and i >= count - 3:
            label = 1
        row = {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "evaluated_at": (BASE + timedelta(minutes=i)).isoformat(),
            "current_price": 100 + i + label * 10,
            "label": label,
            "return_pct": float(label) + i * 0.1,
            "price_change": label * 3 + i,
            "volume_delta": 100 + i + label * 5,
            "volatility_pct": 1.0 + label,
            "price_position_pct": 20.0 + i,
            "snapshot_count": 2 + i,
            "latest_age_seconds": 0.0,
        }
        if include_future:
            row.update({"future_return_pct": 9_999, "future_price": 999999, "future_at": (BASE + timedelta(minutes=i + future_horizon_minutes)).isoformat()})
        rows.append(row)
    return rows


def trainer(**kwargs):
    return BaselineModelTrainer(ModelTrainingConfig(validation_ratio=0.25, **kwargs))


def test_model_training_config_defaults():
    config = ModelTrainingConfig()
    assert config.model_type == "gradient_boosting"
    assert config.feature_columns == DEFAULT_FEATURE_COLUMNS
    assert config.label_column == "label"


@pytest.mark.parametrize("kwargs", [
    {"model_type": "xgboost"},
    {"validation_ratio": 0},
    {"validation_ratio": 0.6},
    {"min_train_examples": 0},
    {"min_validation_examples": 0},
    {"positive_label": 2},
    {"feature_columns": ()},
    {"feature_columns": ("label",)},
    {"feature_columns": ("future_return_pct",)},
    {"feature_columns": ("future_price",)},
    {"feature_columns": ("future_at",)},
])
def test_config_validation(kwargs):
    with pytest.raises(ValueError):
        ModelTrainingConfig(**kwargs)


def test_empty_records_raise_model_training_error():
    with pytest.raises(ModelTrainingError):
        trainer().train_records([])


@pytest.mark.parametrize("model_type", ["logistic_regression", "random_forest", "gradient_boosting"])
def test_supported_models_train(model_type):
    result = trainer(model_type=model_type).train_records(records())
    assert result.model_type == model_type
    assert result.train_size == 9
    assert result.validation_size == 3
    assert isinstance(result.metrics, ClassificationMetrics)
    assert result.model_path is None
    assert result.trained_at.tzinfo == timezone.utc


def test_split_is_time_based_and_validation_is_latest(monkeypatch):
    seen = {}
    class SpyTrainer(BaselineModelTrainer):
        def _build_matrix(self, rows):
            seen.setdefault("calls", []).append([r["evaluated_at"] for r in rows])
            return super()._build_matrix(rows)
    shuffled = list(reversed(records(8)))
    result = SpyTrainer(ModelTrainingConfig(validation_ratio=0.25)).train_records(shuffled)
    assert result.train_size == 6
    assert result.validation_size == 2
    assert seen["calls"][0] == [(BASE + timedelta(minutes=i)).isoformat() for i in range(6)]
    assert seen["calls"][1] == [(BASE + timedelta(minutes=i)).isoformat() for i in range(6, 8)]


def test_training_labels_crossing_validation_start_are_purged():
    result = trainer(model_type="random_forest").train_records(records(12, future_horizon_minutes=2))
    assert result.validation_size == 3
    assert result.train_size == 8


def test_train_label_single_class_raises():
    rows = records(8)
    for row in rows[:6]:
        row["label"] = 1
    with pytest.raises(ModelTrainingError):
        trainer().train_records(rows)


def test_invalid_label_missing_feature_and_bad_feature_raise():
    rows = records()
    rows[0]["label"] = 2
    with pytest.raises(ModelTrainingError):
        trainer().train_records(rows)
    rows = records()
    del rows[0]["return_pct"]
    with pytest.raises(ModelTrainingError):
        trainer().train_records(rows)
    rows = records()
    rows[0]["return_pct"] = "not-a-number"
    with pytest.raises(ModelTrainingError):
        trainer().train_records(rows)


def test_none_feature_uses_missing_value_fill(monkeypatch):
    captured = {}
    class SpyTrainer(BaselineModelTrainer):
        def _create_model(self):
            model = super()._create_model()
            original_fit = model.fit
            def fit(x, y):
                captured["x"] = x
                return original_fit(x, y)
            model.fit = fit
            return model
    rows = records(8)
    rows[0]["volume_delta"] = None
    SpyTrainer(ModelTrainingConfig(model_type="random_forest", missing_value_fill=-7.5, validation_ratio=0.25)).train_records(rows)
    assert captured["x"][0][DEFAULT_FEATURE_COLUMNS.index("volume_delta")] == -7.5


def test_metrics_include_scores_confusion_and_single_validation_class_roc_auc_none():
    result = trainer(model_type="random_forest").train_records(records(12, one_validation_class=True))
    assert 0 <= result.metrics.accuracy <= 1
    assert result.metrics.precision >= 0
    assert result.metrics.recall >= 0
    assert result.metrics.f1 >= 0
    assert result.metrics.roc_auc is None
    assert result.metrics.true_positive + result.metrics.true_negative + result.metrics.false_positive + result.metrics.false_negative == result.validation_size


def test_train_examples_converts_training_examples():
    examples = []
    for row in records(8):
        examples.append(TrainingExample(
            stock_code=row["stock_code"], stock_name=row["stock_name"], evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
            current_price=row["current_price"], future_at=datetime.fromisoformat(row["future_at"]), future_price=row["future_price"],
            future_return_pct=row["future_return_pct"], label=row["label"],
            features=CandidateFeatures(row["return_pct"], row["price_change"], row["volume_delta"], row["volatility_pct"], row["price_position_pct"], row["snapshot_count"], row["latest_age_seconds"]),
        ))
    assert trainer(model_type="random_forest").train_examples(examples).train_size == 6


def test_csv_load_train_and_bundle_roundtrip(tmp_path):
    path = tmp_path / "records.csv"
    rows = records(10)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    output = tmp_path / "artifacts" / "model.joblib"
    t = trainer(model_type="gradient_boosting")
    loaded = t.load_records_csv(path)
    assert len(loaded) == 10
    result = t.train_csv(path, model_output_path=output)
    assert output.exists()
    assert result.model_path == str(output)
    bundle = t.load_model_bundle(output)
    assert bundle["feature_columns"] == DEFAULT_FEATURE_COLUMNS
    assert isinstance(bundle["metrics"], ClassificationMetrics)


def test_iso_naive_and_timezone_aware_datetimes_sort_to_utc():
    plus_nine = timezone(timedelta(hours=9))
    rows = records(8)
    rows[0]["evaluated_at"] = datetime(2026, 6, 24, 9, 0)
    rows[1]["evaluated_at"] = datetime(2026, 6, 24, 18, 1, tzinfo=plus_nine)
    rows[2]["evaluated_at"] = "2026-06-24T09:02:00Z"
    result = trainer(model_type="random_forest").train_records(rows)
    assert result.train_size == 6


def test_future_return_can_exist_but_is_not_used_as_feature():
    result = trainer(feature_columns=("current_price", "return_pct", "price_change", "volume_delta", "volatility_pct", "price_position_pct", "snapshot_count", "latest_age_seconds")).train_records(records())
    assert result.feature_columns == DEFAULT_FEATURE_COLUMNS


def test_no_order_execution_or_candidate_model_score_assignment():
    modeling_source = Path("trading_agent/modeling.py").read_text(encoding="utf-8")
    assert "order" not in modeling_source.lower()
    assert "model_score" not in modeling_source


def test_error_messages_do_not_expose_secrets(tmp_path):
    bad = records()
    bad[0]["return_pct"] = "postgresql://user:password@host/db access token secret"
    with pytest.raises(ModelTrainingError) as exc_info:
        trainer().train_records(bad)
    message = str(exc_info.value).lower()
    assert "postgresql://" not in message
    assert "password" not in message
    assert "access token" not in message
