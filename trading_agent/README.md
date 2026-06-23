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
   python main.py
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
