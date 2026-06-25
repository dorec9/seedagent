# Kiwoom REST API Trading Agent

Python 기반 키움 REST API 자동매매 시스템의 초기 인증 모듈입니다.

## 현재 단계에서 구현한 기능

- 환경변수에서 `KIWOOM_MODE`, `KIWOOM_BASE_URL`, `APP_ENV`를 읽습니다.
- 비밀 환경변수에서 `KIWOOM_APPKEY`, `KIWOOM_SECRETKEY`를 읽습니다.
- 키움 REST API 접근토큰 발급 요청을 수행합니다.
- 접근토큰 발급 성공 여부와 토큰 존재 여부만 출력합니다.
- `appkey`, `secretkey`, `access_token` 원문은 출력하지 않습니다.

## 아직 구현하지 않은 기능

- 현재가 조회
- 계좌 조회
- 매수 주문
- 매도 주문
- 매매 전략
- 자동매매 루프

## 환경변수

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `KIWOOM_MODE` | `real` | 키움 API 실행 모드입니다. |
| `KIWOOM_BASE_URL` | `https://api.kiwoom.com` | 키움 REST API 기본 URL입니다. |
| `APP_ENV` | `codex` | 애플리케이션 실행 환경입니다. |
| `KIWOOM_APPKEY` | 없음 | 키움 REST API 앱 키입니다. 반드시 설정해야 합니다. |
| `KIWOOM_SECRETKEY` | 없음 | 키움 REST API 시크릿 키입니다. 반드시 설정해야 합니다. |

## 실행 방법

1. 의존성을 설치합니다.

   ```bash
   pip install -r trading_agent/requirements.txt
   ```

2. 환경변수를 설정합니다. 로컬 실행 시에는 `.env.example`을 참고해 값을 준비한 뒤 셸 환경에 export합니다.

   ```bash
   export KIWOOM_MODE=real
   export KIWOOM_BASE_URL=https://api.kiwoom.com
   export APP_ENV=codex
   export KIWOOM_APPKEY=your_app_key_here
   export KIWOOM_SECRETKEY=your_secret_key_here
   ```

3. 인증 확인 프로그램을 실행합니다.

   ```bash
   python trading_agent/main.py
   ```

## 출력 예시

성공 시:

```json
{
    "system": "OK",
    "mode": "real",
    "base_url_loaded": true,
    "appkey_loaded": true,
    "secretkey_loaded": true,
    "token_issued": true,
    "access_token_exists": true,
    "errors": []
}
```

실패 시:

```json
{
    "system": "ERROR",
    "mode": "real",
    "base_url_loaded": true,
    "appkey_loaded": false,
    "secretkey_loaded": true,
    "token_issued": false,
    "access_token_exists": false,
    "errors": [
        "KIWOOM_APPKEY is missing"
    ]
}
```

## 현재가 조회 예시

`KiwoomMarketData`는 설정의 `base_url`을 사용해 운영/모의투자 환경을 선택하고, 키움 REST API `ka10001` 주식기본정보요청으로 특정 종목의 현재가를 조회합니다. 키움 응답에서 현재가는 부호가 포함될 수 있으므로 `current_price`는 절대값 가격으로 정규화하고, `change_price`는 전일대비 방향을 알 수 있도록 부호를 유지합니다.

```python
from config import load_config
from market_data import KiwoomMarketData

config = load_config()

# KiwoomAuth.issue_access_token() currently returns only metadata and does not expose the token.
access_token = "<YOUR_KIWOOM_ACCESS_TOKEN>"

market_data = KiwoomMarketData(config)
quote = market_data.get_stock_quote("005930", access_token)
print(quote.current_price)
```

반환되는 `StockQuote`에는 최소한 `stock_code`, `stock_name`, `current_price`, `change_price`, `change_rate`, `volume`, `raw` 필드가 포함됩니다. HTTP 오류, 타임아웃, JSON 파싱 오류, 또는 `return_code`가 `0`이 아닌 키움 API 실패 응답은 `KiwoomAPIError`로 처리됩니다.

### 복수 종목 현재가 조회 예시

`get_stock_quotes`는 단일 종목 조회와 동일한 `StockQuote` 목록을 입력한 종목코드 순서대로 반환합니다. 중복 종목코드도 입력 순서대로 각각 조회하며, 기본 정책은 fail-fast이므로 개별 종목 조회 중 `KiwoomAPIError`가 발생하면 즉시 예외를 발생시키고 남은 종목은 조회하지 않습니다. 과도한 API 호출을 피해야 하는 경우 `delay_seconds`를 양수로 지정해 각 요청 사이에 대기 시간을 둘 수 있습니다.

```python
from config import load_config
from auth import KiwoomAuth
from market_data import KiwoomMarketData

config = load_config()
auth = KiwoomAuth(config)
auth.issue_access_token()

# KiwoomAuth.issue_access_token() currently returns only metadata and does not expose the token.
access_token = "<YOUR_KIWOOM_ACCESS_TOKEN>"

market_data = KiwoomMarketData(config)
quotes = market_data.get_stock_quotes(["005930", "000660", "035420"], access_token)
for quote in quotes:
    print(quote.stock_code, quote.stock_name, quote.current_price)
```

## PostgreSQL 시세 스냅샷 저장

시세 스냅샷 저장소는 SQLite가 아니라 PostgreSQL을 사용합니다. 로컬 개발에서는 PostgreSQL을 직접 설치하지 않고 Docker Compose로 실행합니다. 실제 `.env`, API 키, DB 비밀번호, Kiwoom access token은 커밋하지 않고, Git에는 `.env.example`만 포함합니다.

### PostgreSQL 실행

1. 예시 환경 파일을 복사해 로컬 `.env`를 만듭니다.

   ```bash
   cp .env.example .env
   ```

2. 필요하면 `.env`의 DB 연결 정보를 조정합니다. 기본 예시는 다음과 같습니다.

   ```dotenv
   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_agent
   TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_agent
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   POSTGRES_DB=trading_agent
   ```

3. Docker Compose로 PostgreSQL을 실행합니다.

   ```bash
   docker compose up -d
   ```

4. 컨테이너 실행 상태를 확인합니다.

   ```bash
   docker ps
   ```

### 현재가 조회 후 PostgreSQL 저장 예시

```python
from trading_agent.config import load_config
from trading_agent.auth import KiwoomAuth
from trading_agent.market_data import KiwoomMarketData
from trading_agent.storage import QuoteSnapshotStore

config = load_config()

auth = KiwoomAuth(config)
token_result = auth.issue_access_token()

market_data = KiwoomMarketData(config)
quotes = market_data.get_stock_quotes(["005930", "000660"], token_result.access_token)

store = QuoteSnapshotStore(config.database_url)
store.initialize()
store.save_quotes(quotes)

latest = store.get_latest_quote("005930")
print(latest.stock_code, latest.current_price, latest.observed_at)
```

`QuoteSnapshotStore.initialize()`는 `price_snapshots` 테이블과 `(stock_code, observed_at DESC)` 인덱스를 생성합니다. `observed_at`은 timezone-aware UTC `datetime`으로 저장되고, Kiwoom 원본 응답은 PostgreSQL `JSONB`에 저장됩니다.

### 테스트 실행

PostgreSQL 서버가 없어도 기본 테스트는 실패하지 않아야 합니다. `TEST_DATABASE_URL`이 없으면 storage integration test는 skip됩니다.

```bash
python -m pytest
```

PostgreSQL integration test까지 실행하려면 Docker Compose로 PostgreSQL을 띄운 뒤 `TEST_DATABASE_URL`을 지정합니다.

```bash
docker compose up -d
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_agent python -m pytest
```

### 보안 주의사항

- `.env`는 Git에 올리지 않습니다.
- `.env.example`만 Git에 올립니다.
- 실제 API 키, DB 비밀번호, access token은 커밋하지 않습니다.
- PostgreSQL이 없어도 기본 테스트는 통과해야 합니다.
- `TEST_DATABASE_URL`이 있을 때만 storage integration test가 실행됩니다.

## Polling Collector 사용 예시

`QuotePollingCollector`는 관심종목 리스트를 일정 간격으로 REST polling 조회한 뒤, 조회된 현재가 스냅샷을 PostgreSQL에 저장합니다. 이 수집기는 REST polling 기반 준실시간 수집기이며, 틱 단위 실시간 체결/호가 스트리밍은 아직 구현하지 않습니다.

```python
from trading_agent.config import load_config
from trading_agent.auth import KiwoomAuth
from trading_agent.market_data import KiwoomMarketData
from trading_agent.storage import QuoteSnapshotStore
from trading_agent.collector import QuotePollingCollector, QuotePollingConfig

config = load_config()

auth = KiwoomAuth(config)
auth.issue_access_token()

# KiwoomAuth.issue_access_token() currently returns only metadata and does not expose the token.
access_token = "<YOUR_KIWOOM_ACCESS_TOKEN>"

market_data = KiwoomMarketData(config)

store = QuoteSnapshotStore(config.database_url)
store.initialize()

collector = QuotePollingCollector(
    market_data=market_data,
    store=store,
    config=QuotePollingConfig(
        stock_codes=["005930", "000660"],
        interval_seconds=5.0,
        per_request_delay_seconds=0.2,
        max_iterations=10,
    ),
)

results = collector.run(access_token)

for result in results:
    print(result.iteration, result.requested_count, result.saved_count, result.snapshot_ids)
```

### Polling Collector 주의사항

- 너무 짧은 `interval_seconds`는 Kiwoom API 제한이나 과도한 요청 문제를 만들 수 있으므로 주의해야 합니다.
- `access_token`, `DATABASE_URL`, DB 비밀번호는 로그에 남기지 않습니다.
- 실제 운영 전에는 장 운영시간 체크, 토큰 만료 갱신, 장애 복구 로직이 추가로 필요합니다.
- 이번 단계에서는 조회, 저장, 반복 실행까지만 구현하며 매수/매도 주문은 수행하지 않습니다.

## Candidate Feature & Rule Signal Engine 사용 예시

`CandidateFilter`는 PostgreSQL `price_snapshots`에 저장된 최근 시세 스냅샷을 읽어 가격 변화율, 거래량 증가량, 변동성, 가격 위치, 최신 데이터 여부를 계산하고 규칙 기반 매수 후보 신호를 생성합니다. 이 기능은 모델링 가능한 피처와 후보 필터링 결과만 제공하며 실제 주문은 실행하지 않습니다.

```python
from trading_agent.config import load_config
from trading_agent.storage import QuoteSnapshotStore
from trading_agent.signals import CandidateFilter, CandidateFilterConfig

config = load_config()

store = QuoteSnapshotStore(config.database_url)

candidate_filter = CandidateFilter(
    store=store,
    config=CandidateFilterConfig(
        lookback_minutes=5.0,
        min_price_change_pct=1.0,
        max_price_change_pct=8.0,
        min_volume_delta=10000,
        min_current_price=1000,
        max_current_price=500000,
        min_snapshot_count=2,
        max_latest_age_seconds=30.0,
    ),
)

signals = candidate_filter.evaluate(["005930", "000660", "035420"])

for signal in signals:
    print(
        signal.stock_code,
        signal.current_price,
        signal.features.return_pct,
        signal.features.volume_delta,
        signal.features.volatility_pct,
        signal.features.price_position_pct,
        signal.rule_score,
        signal.model_score,
        signal.passed,
        signal.reasons,
    )

candidates = candidate_filter.get_candidates(["005930", "000660", "035420"])

for candidate in candidates:
    print("candidate:", candidate.stock_code, candidate.features.return_pct, candidate.rule_score)
```

### Candidate Feature & Rule Signal Engine 주의사항

- 이 기능은 매수 후보 필터링일 뿐 실제 주문을 실행하지 않습니다.
- 조건을 통과한 종목이 반드시 좋은 투자 대상이라는 뜻은 아닙니다.
- REST polling 기반 스냅샷 데이터는 틱 단위 실시간 데이터가 아닙니다.
- 너무 짧은 `lookback_minutes`나 너무 낮은 조건값은 노이즈가 많은 후보를 만들 수 있습니다.
- `model_score`는 현재 `None`입니다. 머신러닝 모델은 이후 Feature에서 추가합니다.
- 실제 주문 전에는 주문 전 안전장치, 계좌 잔고 확인, 보유 종목 중복 확인, 일일 손실 제한, 주문 수량 제한이 필요합니다.

## Historical Training Dataset Builder 사용 예시

`TrainingDatasetBuilder`는 PostgreSQL `price_snapshots`에 저장된 과거 스냅샷을 읽어 각 평가 시점의 과거 lookback window 기반 feature와 미래 horizon 기반 label을 생성합니다. 이 기능은 모델 학습용 데이터셋 생성기이며, 실제 모델 학습/예측, 주문 실행, 백테스트 수익률 계산은 수행하지 않습니다.

```python
from trading_agent.config import load_config
from trading_agent.storage import QuoteSnapshotStore
from trading_agent.datasets import TrainingDatasetBuilder, TrainingDatasetConfig

config = load_config()
store = QuoteSnapshotStore(config.database_url)

builder = TrainingDatasetBuilder(
    store=store,
    config=TrainingDatasetConfig(
        lookback_minutes=5.0,
        prediction_horizon_minutes=10.0,
        target_return_pct=1.0,
        min_snapshot_count=2,
        max_snapshots_per_window=1000,
        max_snapshots_per_stock=10000,
        max_future_lag_seconds=60.0,
    ),
)

examples = builder.build(["005930", "000660", "035420"])
records = builder.to_records(examples)

builder.write_csv(examples, "training_dataset.csv")

print(len(examples))
print(records[0] if records else None)
```

### Historical Training Dataset Builder 주의사항

- 이 기능은 모델 학습용 데이터셋 생성기입니다.
- 실제 모델 학습은 아직 하지 않습니다.
- 실제 모델 예측도 아직 하지 않습니다.
- 실제 주문 실행도 하지 않습니다.
- `future_return_pct`와 `label`은 학습 타깃이며, feature로 사용하면 정보 누수가 발생합니다.
- 실제 모델 학습 시에는 시간 기준 train/validation split이 필요합니다.
- 랜덤 셔플 기반 검증은 미래 정보 누수 위험이 있습니다.
- 수수료, 슬리피지, 체결 실패는 아직 반영하지 않습니다.

## Baseline ML Model Trainer 사용 예시

`BaselineModelTrainer`는 `TrainingDatasetBuilder`가 만든 `TrainingExample` 또는 CSV 레코드를 입력으로 받아 baseline classification model을 학습합니다. 검증은 random split이나 shuffle을 사용하지 않고, `evaluated_at` 기준으로 정렬한 뒤 가장 최신 구간을 validation set으로 사용하는 time-based validation입니다. 학습된 모델과 metrics, feature schema, config는 joblib model bundle로 저장할 수 있습니다.

```python
from trading_agent.datasets import TrainingDatasetBuilder, TrainingDatasetConfig
from trading_agent.modeling import BaselineModelTrainer, ModelTrainingConfig
from trading_agent.storage import QuoteSnapshotStore
from trading_agent.config import load_config

config = load_config()
store = QuoteSnapshotStore(config.database_url)

dataset_builder = TrainingDatasetBuilder(
    store=store,
    config=TrainingDatasetConfig(
        lookback_minutes=5.0,
        prediction_horizon_minutes=10.0,
        target_return_pct=1.0,
    ),
)

examples = dataset_builder.build(["005930", "000660", "035420"])

trainer = BaselineModelTrainer(
    ModelTrainingConfig(
        model_type="gradient_boosting",
        validation_ratio=0.2,
        random_state=42,
    )
)

result = trainer.train_examples(
    examples,
    model_output_path="artifacts/baseline_model.joblib",
)

print(result.metrics)
print(result.train_size, result.validation_size)
print(result.model_path)
```

### CSV 기반 Baseline ML Model Trainer 예시

`TrainingDatasetBuilder.write_csv(...)`로 저장한 CSV는 pandas 없이 stdlib `csv.DictReader`로 읽고 학습할 수 있습니다.

```python
from trading_agent.modeling import BaselineModelTrainer, ModelTrainingConfig

trainer = BaselineModelTrainer(
    ModelTrainingConfig(model_type="gradient_boosting")
)

result = trainer.train_csv(
    "training_dataset.csv",
    model_output_path="artifacts/baseline_model.joblib",
)

print(result.metrics)
```

### Baseline ML Model Trainer 주의사항

- 이 기능은 baseline model trainer입니다.
- 아직 실시간 예측을 자동투자 판단에 연결하지 않습니다.
- 아직 `CandidateSignal.model_score`를 채우지 않습니다.
- 아직 실제 주문을 실행하지 않습니다.
- random split을 사용하지 않고 time-based validation을 사용합니다.
- `future_return_pct`, `future_price`, `future_at`, `label`은 feature로 사용하면 안 됩니다.
- validation 성능이 좋다고 실제 매매 수익이 보장되지 않습니다.
- 수수료, 슬리피지, 체결 실패, 호가 공백은 아직 반영하지 않았습니다.
- label 불균형이 심하면 accuracy만 보고 판단하면 안 됩니다.
- precision, recall, roc_auc를 함께 봐야 합니다.

## Historical OHLCV Backfill

Historical OHLCV bars are intended for model-training dataset generation. They complement, but do not replace, live polling snapshots: historical OHLCV gives the trainer enough past bars to compute leak-free features and labels, while live `price_snapshots` remain the real-time operating data captured by the polling collector.

### 일봉 데이터 조회 예시

```python
from datetime import date

from trading_agent.config import KiwoomConfig
from trading_agent.ohlcv import KiwoomHistoricalMarketData

client = KiwoomHistoricalMarketData(KiwoomConfig(mode="real", base_url="https://api.kiwoom.com", app_env="local", appkey=None, secretkey=None))
bars = client.get_daily_ohlcv(
    stock_code="005930",
    access_token="<KIWOOM_ACCESS_TOKEN>",
    start_date=date(2025, 1, 1),
    end_date=date(2025, 12, 31),
)
```

### 여러 종목 OHLCV backfill 저장 예시

```python
from datetime import date

from trading_agent.config import KiwoomConfig
from trading_agent.ohlcv import OhlcvBackfillConfig, OhlcvBackfillRunner, KiwoomHistoricalMarketData
from trading_agent.storage import QuoteSnapshotStore

store = QuoteSnapshotStore("postgresql://postgres:postgres@localhost:5432/trading_agent")
store.initialize_ohlcv()

runner = OhlcvBackfillRunner(
    market_data=KiwoomHistoricalMarketData(KiwoomConfig(mode="real", base_url="https://api.kiwoom.com", app_env="local", appkey=None, secretkey=None)),
    store=store,
    config=OhlcvBackfillConfig(
        stock_codes=("005930", "000660"),
        interval="1d",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
    ),
)
results = runner.run(access_token="<KIWOOM_ACCESS_TOKEN>")
```

### OHLCV 기반 `training_dataset.csv` 생성 예시

```python
from trading_agent.ohlcv import OhlcvTrainingDatasetBuilder, OhlcvTrainingDatasetConfig
from trading_agent.storage import QuoteSnapshotStore

store = QuoteSnapshotStore("postgresql://postgres:postgres@localhost:5432/trading_agent")
builder = OhlcvTrainingDatasetBuilder(
    store=store,
    config=OhlcvTrainingDatasetConfig(
        interval="1d",
        lookback_bars=20,
        prediction_horizon_bars=5,
        target_return_pct=1.0,
    ),
)
examples = builder.build(["005930", "000660"])
builder.write_csv(examples, "training_dataset.csv")
```

> ⚠️ Validation 성능은 실제 매매 수익을 보장하지 않습니다. 실거래 전에는 수수료, 세금, 슬리피지, 체결 실패를 반영한 별도 백테스트와 검증이 필요합니다. 모델 학습 기능 자체는 주문 실행, 계좌 조회, paper trading, 실거래를 포함하지 않습니다.

## Backtest Engine

`trading_agent.backtest`는 OHLCV 기반 모델이 이미 계산한 상승 확률(`proba`)을 사용해 오프라인 백테스트를 수행합니다. 이 엔진은 실거래 주문, 계좌 조회, Kiwoom 주문 API, paper trading, 실시간 collector 연동을 하지 않으며, `training_dataset.csv` 또는 같은 스키마의 예측 CSV를 검증하는 용도입니다.

필수 입력 컬럼은 다음과 같습니다.

| 컬럼 | 설명 |
| --- | --- |
| `stock_code` | 종목 코드 |
| `evaluated_at` | 신호 평가 시각 |
| `current_close` | 진입 기준 종가 |
| `future_close` | 청산 기준 미래 종가 |
| `future_at` | 청산 기준 시각 |
| `future_return_pct` | 실제 미래 수익률 |
| `label` | 실제 상승/하락 라벨 |
| `proba` | 모델이 예측한 상승 확률 |

### threshold 전략 예시

`threshold` 전략은 `proba >= threshold`인 행만 매수 후보로 선택합니다. 같은 날짜의 같은 종목은 `proba`가 가장 높은 행 하나만 사용하며, `max_positions_per_day`가 지정되면 날짜별 후보를 `proba` 높은 순서로 제한합니다.

```python
from trading_agent.backtest import BacktestConfig, BacktestEngine

config = BacktestConfig(
    strategy="threshold",
    threshold=0.60,
    initial_capital=10_000_000,
    position_size_pct=0.1,
)

result = BacktestEngine.run_from_csv("training_dataset_with_proba.csv", config)
BacktestEngine.write_trades_csv(result, "backtest_trades_threshold.csv")
BacktestEngine.write_equity_curve_csv(result, "backtest_equity_threshold.csv")
print(result.final_equity, result.max_drawdown_pct, result.win_rate)
```

### top-k per day 전략 예시

`top_k_per_day` 전략은 날짜별로 `proba` 상위 `top_k`개 종목만 선택합니다. 같은 날짜의 같은 종목이 여러 번 있으면 가장 높은 `proba` 하나만 남깁니다.

```python
from trading_agent.backtest import BacktestConfig, BacktestEngine

config = BacktestConfig(
    strategy="top_k_per_day",
    top_k=3,
    initial_capital=10_000_000,
    position_size_pct=0.1,
)

result = BacktestEngine.run_from_csv("training_dataset_with_proba.csv", config)
BacktestEngine.write_trades_csv(result, "backtest_trades_top_k.csv")
BacktestEngine.write_equity_curve_csv(result, "backtest_equity_top_k.csv")
```

### 수수료, 세금, 슬리피지 반영

백테스트의 총 거래비용은 매수 수수료, 매도 수수료, 세금, 왕복 슬리피지를 합산합니다.

```text
cost_pct = (fee_rate * 2 + tax_rate + slippage_rate * 2) * 100
net_return_pct = gross_return_pct - cost_pct
```

기본값은 `fee_rate=0.00015`, `tax_rate=0.0018`, `slippage_rate=0.0005`입니다. 각 거래의 `position_value`는 현재 equity의 `position_size_pct`로 계산합니다. 진입 시점(`evaluated_at`)에는 position value를 available cash에서 차감하고, 청산 시점(`future_at`)에만 position value와 realized PnL을 cash로 되돌립니다. available cash가 부족하면 새 포지션을 열지 않아 총 open position value가 사용 가능한 자본을 초과하지 않습니다. `allow_rebuy_same_stock=False`이면 같은 종목의 기존 포지션이 열려 있는 동안에는 재매수하지 않고, 기존 포지션이 `future_at`에 청산된 뒤에는 다시 매수할 수 있습니다. Equity curve는 `future_at`별 realized PnL, cash, open position value, equity를 기록하며 이를 기준으로 최대 낙폭(MDD)을 계산합니다.

### 백테스트 해석 주의사항

- 모델의 accuracy가 수익률을 의미하지 않습니다. 낮은 accuracy에서도 일부 threshold 또는 top-k 구간의 손익 특성이 다를 수 있고, 반대로 높은 accuracy라도 거래비용 반영 후 손실이 날 수 있습니다.
- validation 성능과 백테스트 성능은 실거래 수익을 보장하지 않습니다. 과최적화, 표본 부족, 거래 정지/호가 공백, 체결 실패, 시장 충격, 슬리피지 확대가 실제 성과를 크게 바꿀 수 있습니다.
- 실거래 전에는 별도의 paper trading, risk guard, 주문 차단/한도 로직, 계좌/체결 검증, 장애 대응 절차가 필요합니다.

## Benchmark & Walk-forward Backtest

`trading_agent.benchmark`는 OHLCV prediction CSV의 모델 신호가 단순 상승장 효과인지, 실제 종목 선택 능력인지 확인하기 위한 benchmark와 walk-forward helper를 제공합니다. 입력 CSV에는 `stock_code`, `evaluated_at`, `current_close`, `future_close`, `future_at`, `future_return_pct`, `label`, `proba` 컬럼이 필요합니다.

> 단일 validation 기간의 수익률만으로 실전 투자를 결정하면 안 됩니다. 모델 top-k 전략이 equal-weight, buy-all, random top-k, inverse signal benchmark를 일관되게 이기지 못한다면 모델의 `proba`를 유의미한 종목 선택 신호로 보기 어렵습니다.

### model top-k 전략과 benchmark 비교 예시

```python
from trading_agent.benchmark import BenchmarkConfig, BenchmarkEngine

config = BenchmarkConfig(
    initial_capital=10_000_000,
    position_size_pct=0.1,
    top_k=3,
    random_seed=42,
    random_trials=100,
)
comparison = BenchmarkEngine.run_from_csv("model_predictions_valid.csv", config)
print(comparison.model_strategy.net_total_return_pct)
print(comparison.model_excess_vs_equal_weight_pct)
print(comparison.model_excess_vs_random_mean_pct)
```

비교 결과는 CSV로 저장할 수 있습니다.

```python
BenchmarkEngine.write_comparison_csv(comparison, "benchmark_comparison.csv")
```

### equal-weight benchmark 예시

Equal-weight benchmark는 날짜별 사용 가능한 모든 후보를 동일한 position sizing 규칙으로 매수 대상으로 보며, `proba`는 사용하지 않습니다. 실제 진입 가능 여부는 기존 `BacktestEngine`과 같은 cash/open position/future_at 실현 규칙을 따릅니다.

```python
equal_weight = BenchmarkEngine.run_equal_weight(records, config)
print(equal_weight.net_total_return_pct, equal_weight.max_drawdown_pct)
```

### random top-k benchmark 예시

Random top-k benchmark는 날짜별 후보 중 최대 `top_k`개를 무작위로 선택하고 `random_trials`회 반복합니다. `random_seed`가 같으면 결과가 재현됩니다.

```python
random_trials = BenchmarkEngine.run_random_top_k_trials(records, config)
for trial in random_trials:
    print(trial.name, trial.net_total_return_pct, trial.max_drawdown_pct, trial.profit_factor)
```

### inverse signal benchmark 예시

Inverse signal benchmark는 날짜별 `proba`가 가장 낮은 종목부터 `top_k`개를 선택합니다. 모델 신호가 강하다면 inverse signal은 model top-k보다 낮은 성능을 보이는 것이 자연스럽습니다.

```python
inverse = BenchmarkEngine.run_inverse_signal(records, config)
print(inverse.net_total_return_pct, inverse.profit_factor)
```

### 월별/연도별 성능 분해 예시

성능 분해는 완료된 trades의 `future_at` 기준 period별로 거래 수, 승률, 순수익률, 평균 거래 수익률, profit factor를 계산합니다.

```python
from trading_agent.backtest import BacktestConfig, BacktestEngine

result = BacktestEngine.run_from_csv(
    "model_predictions_valid.csv",
    BacktestConfig(strategy="top_k_per_day", top_k=3),
)
monthly = BenchmarkEngine.monthly_breakdown(result)
yearly = BenchmarkEngine.yearly_breakdown(result)
print(monthly)
print(yearly)
```

### walk-forward 검증 예시

Walk-forward helper는 이미 `proba`가 포함된 prediction CSV를 시간순 구간으로 나누어 같은 top-k 전략을 반복 검증합니다. 초기 구현은 모델 재학습을 수행하지 않으며, yearly 또는 quarterly 구간을 지원합니다.

```python
walk_forward_rows = BenchmarkEngine.walk_forward(records, config, frequency="yearly")
quarterly_rows = BenchmarkEngine.walk_forward(records, config, frequency="quarterly")
for row in walk_forward_rows:
    print(row["period_start"], row["period_end"], row["net_total_return_pct"])
```
