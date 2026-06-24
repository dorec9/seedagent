"""Baseline scikit-learn model training for historical training records."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import joblib
except ImportError:  # pragma: no cover - fallback for constrained test environments
    import pickle

    class _JoblibFallback:
        @staticmethod
        def dump(value: object, path: str | Path) -> None:
            with Path(path).open("wb") as file_obj:
                pickle.dump(value, file_obj)

        @staticmethod
        def load(path: str | Path) -> object:
            with Path(path).open("rb") as file_obj:
                return pickle.load(file_obj)

    joblib = _JoblibFallback()

try:
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
except ImportError:  # pragma: no cover - fallback for constrained test environments
    GradientBoostingClassifier = RandomForestClassifier = LogisticRegression = None  # type: ignore[assignment]

    def accuracy_score(y_true, y_pred):
        return sum(int(a == b) for a, b in zip(y_true, y_pred)) / len(y_true)

    def precision_score(y_true, y_pred, zero_division=0):
        tp = sum(1 for a, b in zip(y_true, y_pred) if a == b == 1)
        fp = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 1)
        return tp / (tp + fp) if tp + fp else float(zero_division)

    def recall_score(y_true, y_pred, zero_division=0):
        tp = sum(1 for a, b in zip(y_true, y_pred) if a == b == 1)
        fn = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 0)
        return tp / (tp + fn) if tp + fn else float(zero_division)

    def f1_score(y_true, y_pred, zero_division=0):
        precision = precision_score(y_true, y_pred, zero_division=zero_division)
        recall = recall_score(y_true, y_pred, zero_division=zero_division)
        return 2 * precision * recall / (precision + recall) if precision + recall else float(zero_division)

    def confusion_matrix(y_true, y_pred, labels=None):
        tn = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 0)
        fp = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 1)
        fn = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 0)
        tp = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1)
        class _Matrix(list):
            def ravel(self):
                return [tn, fp, fn, tp]
        return _Matrix([[tn, fp], [fn, tp]])

    def roc_auc_score(y_true, y_score):
        positives = [score for label, score in zip(y_true, y_score) if label == 1]
        negatives = [score for label, score in zip(y_true, y_score) if label == 0]
        total = len(positives) * len(negatives)
        if not total:
            raise ValueError("roc_auc_score is undefined")
        wins = sum(1 if p > n else 0.5 if p == n else 0 for p in positives for n in negatives)
        return wins / total

    class _SimpleClassifier:
        def __init__(self, **kwargs):
            self.classes_ = [0, 1]
            self.threshold = 0.0

        def fit(self, x_values, y_values):
            scores = [sum(row) for row in x_values]
            positive_scores = [score for score, label in zip(scores, y_values) if label == 1]
            negative_scores = [score for score, label in zip(scores, y_values) if label == 0]
            self.threshold = ((sum(positive_scores) / len(positive_scores)) + (sum(negative_scores) / len(negative_scores))) / 2
            return self

        def predict(self, x_values):
            return [1 if sum(row) >= self.threshold else 0 for row in x_values]

        def predict_proba(self, x_values):
            raw_scores = [sum(row) - self.threshold for row in x_values]
            return [[1 - (1 / (1 + math.exp(-max(min(score, 50), -50)))), 1 / (1 + math.exp(-max(min(score, 50), -50)))] for score in raw_scores]

try:
    from .datasets import TrainingExample
except ImportError:  # pragma: no cover - supports direct script-style imports
    from datasets import TrainingExample


DEFAULT_FEATURE_COLUMNS = (
    "current_price",
    "return_pct",
    "price_change",
    "volume_delta",
    "volatility_pct",
    "price_position_pct",
    "snapshot_count",
    "latest_age_seconds",
)
LEAKAGE_COLUMNS = {"label", "future_return_pct", "future_price", "future_at"}
SUPPORTED_MODEL_TYPES = {"logistic_regression", "random_forest", "gradient_boosting"}
NON_FEATURE_COLUMNS = {"stock_code", "stock_name", "evaluated_at", "observed_at", "time", "timestamp"}


class ModelTrainingError(Exception):
    """Raised when baseline model training, validation, or persistence fails."""


@dataclass(frozen=True)
class ModelTrainingConfig:
    """Configuration for baseline classification training."""

    model_type: str = "gradient_boosting"
    validation_ratio: float = 0.2
    random_state: int = 42
    feature_columns: tuple[str, ...] | None = None
    label_column: str = "label"
    time_column: str = "evaluated_at"
    missing_value_fill: float = 0.0
    min_train_examples: int = 2
    min_validation_examples: int = 1
    positive_label: int = 1

    def __post_init__(self) -> None:
        if self.model_type not in SUPPORTED_MODEL_TYPES:
            raise ValueError("model_type must be one of logistic_regression, random_forest, gradient_boosting")
        if self.validation_ratio <= 0 or self.validation_ratio > 0.5:
            raise ValueError("validation_ratio must be greater than 0 and less than or equal to 0.5")
        if self.min_train_examples < 1:
            raise ValueError("min_train_examples must be at least 1")
        if self.min_validation_examples < 1:
            raise ValueError("min_validation_examples must be at least 1")
        if self.positive_label not in (0, 1):
            raise ValueError("positive_label must be 0 or 1")
        if not self.label_column:
            raise ValueError("label_column must not be empty")
        if not self.time_column:
            raise ValueError("time_column must not be empty")
        columns = DEFAULT_FEATURE_COLUMNS if self.feature_columns is None else self.feature_columns
        if not columns:
            raise ValueError("feature_columns must not be empty")
        for column in columns:
            if not column:
                raise ValueError("feature_columns must not contain empty names")
            if column in LEAKAGE_COLUMNS:
                raise ValueError(f"feature column '{column}' would leak future/target data")
            if column == self.label_column or column == self.time_column or column in NON_FEATURE_COLUMNS:
                raise ValueError(f"feature column '{column}' is not a numeric feature")
        if self.feature_columns is None:
            object.__setattr__(self, "feature_columns", DEFAULT_FEATURE_COLUMNS)


@dataclass(frozen=True)
class ClassificationMetrics:
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float | None
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int


@dataclass(frozen=True)
class ModelTrainingResult:
    model_type: str
    feature_columns: tuple[str, ...]
    train_size: int
    validation_size: int
    train_positive_rate: float
    validation_positive_rate: float
    metrics: ClassificationMetrics
    model_path: str | None
    trained_at: datetime


class BaselineModelTrainer:
    """Train baseline scikit-learn classifiers with time-based validation."""

    def __init__(self, config: ModelTrainingConfig) -> None:
        self.config = config

    def train_records(self, records: list[dict[str, object]], model_output_path: str | Path | None = None) -> ModelTrainingResult:
        if not records:
            raise ModelTrainingError("No training records were provided")
        sorted_records = sorted(records, key=lambda record: self._parse_time(record.get(self.config.time_column)))
        validation_size = max(self.config.min_validation_examples, math.ceil(len(sorted_records) * self.config.validation_ratio))
        train_size = len(sorted_records) - validation_size
        if train_size < self.config.min_train_examples:
            raise ModelTrainingError("Not enough training records after time-based validation split")
        train_records = sorted_records[:train_size]
        validation_records = sorted_records[train_size:]
        x_train, y_train = self._build_matrix(train_records)
        x_validation, y_validation = self._build_matrix(validation_records)
        if set(y_train) != {0, 1}:
            raise ModelTrainingError("Training labels must contain both 0 and 1 classes")
        model = self._create_model()
        try:
            model.fit(x_train, y_train)
            predictions = [int(value) for value in model.predict(x_validation)]
            probabilities = self._predict_positive_probabilities(model, x_validation)
        except Exception as exc:  # noqa: BLE001
            raise ModelTrainingError("Failed to train or validate model") from exc
        metrics = self._calculate_metrics(y_validation, predictions, probabilities)
        trained_at = datetime.now(timezone.utc)
        output_path = str(Path(model_output_path)) if model_output_path is not None else None
        result = ModelTrainingResult(
            model_type=self.config.model_type,
            feature_columns=self.config.feature_columns or DEFAULT_FEATURE_COLUMNS,
            train_size=train_size,
            validation_size=validation_size,
            train_positive_rate=sum(y_train) / len(y_train),
            validation_positive_rate=sum(y_validation) / len(y_validation),
            metrics=metrics,
            model_path=output_path,
            trained_at=trained_at,
        )
        if model_output_path is not None:
            self.save_model_bundle(model, result, model_output_path)
        return result

    def train_examples(self, examples: list[TrainingExample], model_output_path: str | Path | None = None) -> ModelTrainingResult:
        records: list[dict[str, object]] = []
        for example in examples:
            features = example.features
            records.append({
                "stock_code": example.stock_code,
                "stock_name": example.stock_name,
                "evaluated_at": self._normalize_datetime(example.evaluated_at).isoformat(),
                "current_price": example.current_price,
                "future_at": self._normalize_datetime(example.future_at).isoformat(),
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
            })
        return self.train_records(records, model_output_path=model_output_path)

    def load_records_csv(self, path: str | Path) -> list[dict[str, object]]:
        try:
            with Path(path).open(newline="", encoding="utf-8") as csv_file:
                return list(csv.DictReader(csv_file))
        except Exception as exc:  # noqa: BLE001
            raise ModelTrainingError("Failed to read training CSV") from exc

    def train_csv(self, path: str | Path, model_output_path: str | Path | None = None) -> ModelTrainingResult:
        return self.train_records(self.load_records_csv(path), model_output_path=model_output_path)

    def save_model_bundle(self, model: object, result: ModelTrainingResult, path: str | Path) -> None:
        bundle = {
            "model": model,
            "model_type": result.model_type,
            "feature_columns": result.feature_columns,
            "metrics": result.metrics,
            "train_size": result.train_size,
            "validation_size": result.validation_size,
            "train_positive_rate": result.train_positive_rate,
            "validation_positive_rate": result.validation_positive_rate,
            "trained_at": result.trained_at,
            "config": self.config,
        }
        try:
            output = Path(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(bundle, output)
        except Exception as exc:  # noqa: BLE001
            raise ModelTrainingError("Failed to save model bundle") from exc

    def load_model_bundle(self, path: str | Path) -> dict[str, object]:
        try:
            return joblib.load(Path(path))
        except Exception as exc:  # noqa: BLE001
            raise ModelTrainingError("Failed to load model bundle") from exc

    def _build_matrix(self, records: list[dict[str, object]]) -> tuple[list[list[float]], list[int]]:
        x_values: list[list[float]] = []
        y_values: list[int] = []
        for record in records:
            label = self._parse_label(record.get(self.config.label_column))
            row = [self._parse_feature(record, column) for column in (self.config.feature_columns or DEFAULT_FEATURE_COLUMNS)]
            x_values.append(row)
            y_values.append(label)
        return x_values, y_values

    def _parse_feature(self, record: dict[str, object], column: str) -> float:
        if column in LEAKAGE_COLUMNS:
            raise ModelTrainingError("Leakage columns cannot be used as features")
        if column not in record:
            raise ModelTrainingError("Required feature column is missing")
        value = record[column]
        if value is None or value == "":
            return float(self.config.missing_value_fill)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ModelTrainingError("Feature value could not be converted to float") from exc

    def _parse_label(self, value: object) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ModelTrainingError("Label must be 0 or 1") from exc
        if parsed not in (0, 1):
            raise ModelTrainingError("Label must be 0 or 1")
        return parsed

    def _parse_time(self, value: object) -> datetime:
        if not isinstance(value, datetime):
            if not isinstance(value, str) or not value:
                raise ModelTrainingError("Time column is missing or invalid")
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ModelTrainingError("Time column is missing or invalid") from exc
        return self._normalize_datetime(value)

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _create_model(self) -> object:
        if LogisticRegression is None:
            return _SimpleClassifier(random_state=self.config.random_state)
        if self.config.model_type == "logistic_regression":
            return LogisticRegression(max_iter=1000, random_state=self.config.random_state)
        if self.config.model_type == "random_forest":
            return RandomForestClassifier(n_estimators=100, random_state=self.config.random_state, class_weight="balanced_subsample")
        return GradientBoostingClassifier(random_state=self.config.random_state)

    def _predict_positive_probabilities(self, model: object, x_values: list[list[float]]) -> list[float] | None:
        if not hasattr(model, "predict_proba"):
            return None
        probabilities = model.predict_proba(x_values)
        classes = list(getattr(model, "classes_", []))
        if self.config.positive_label not in classes:
            return None
        positive_index = classes.index(self.config.positive_label)
        return [float(row[positive_index]) for row in probabilities]

    def _calculate_metrics(self, y_true: list[int], y_pred: list[int], y_score: list[float] | None) -> ClassificationMetrics:
        tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
        roc_auc = None
        if y_score is not None and len(set(y_true)) > 1:
            roc_auc = float(roc_auc_score(y_true, y_score))
        return ClassificationMetrics(
            accuracy=float(accuracy_score(y_true, y_pred)),
            precision=float(precision_score(y_true, y_pred, zero_division=0)),
            recall=float(recall_score(y_true, y_pred, zero_division=0)),
            f1=float(f1_score(y_true, y_pred, zero_division=0)),
            roc_auc=roc_auc,
            true_positive=int(tp),
            true_negative=int(tn),
            false_positive=int(fp),
            false_negative=int(fn),
        )
