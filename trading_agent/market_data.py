"""Kiwoom REST API market data client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from config import KiwoomConfig


@dataclass(frozen=True)
class StockQuote:
    """Normalized stock quote returned by the Kiwoom market data API."""

    stock_code: str
    stock_name: str
    current_price: int
    change_price: int
    change_rate: float
    volume: int
    raw: dict[str, Any]


class KiwoomAPIError(Exception):
    """Raised when a Kiwoom REST API quote request fails."""


class _ResponseAdapter:
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return json.loads(self._body)


class _RequestsAdapter:
    class RequestException(Exception):
        """Local requests-compatible exception for network errors."""

    @staticmethod
    def post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> _ResponseAdapter:
        import json as jsonlib

        payload_bytes = jsonlib.dumps(json).encode("utf-8")
        quote_request = request.Request(
            url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(quote_request, timeout=timeout) as response:
                return _ResponseAdapter(response.status, response.read().decode("utf-8"))
        except error.HTTPError as exc:
            return _ResponseAdapter(exc.code, exc.read().decode("utf-8", errors="replace"))
        except TimeoutError as exc:
            raise
        except (error.URLError, OSError) as exc:
            raise _RequestsAdapter.RequestException(str(exc)) from exc


requests = _RequestsAdapter()


class KiwoomMarketData:
    """Fetch normalized stock market data from the Kiwoom REST API."""

    STOCK_INFO_PATH = "/api/dostk/stkinfo"

    def __init__(self, config: KiwoomConfig, timeout: float = 10.0) -> None:
        self.config = config
        self.timeout = timeout

    def get_stock_quote(self, stk_cd: str, access_token: str) -> StockQuote:
        """Request basic stock information (ka10001) and return a normalized quote.

        Kiwoom can return signed numeric strings for both current price and daily
        change. The normalized policy is to store ``current_price`` as an
        absolute price and preserve the sign for ``change_price``.
        """
        url = f"{self.config.base_url.rstrip('/')}{self.STOCK_INFO_PATH}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {access_token}",
            "api-id": "ka10001",
        }

        try:
            response = requests.post(
                url,
                json={"stk_cd": stk_cd},
                headers=headers,
                timeout=self.timeout,
            )
        except TimeoutError as exc:
            raise KiwoomAPIError(f"Stock quote request failed for stock_code={stk_cd}: timed out") from exc
        except _RequestsAdapter.RequestException as exc:
            raise KiwoomAPIError(f"Stock quote request failed for stock_code={stk_cd}: {exc}") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise KiwoomAPIError(
                f"Stock quote request failed for stock_code={stk_cd} with HTTP status {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise KiwoomAPIError(f"Stock quote response was not valid JSON for stock_code={stk_cd}") from exc

        if not isinstance(payload, dict):
            raise KiwoomAPIError(f"Stock quote response JSON was not an object for stock_code={stk_cd}")

        return_code = _parse_return_code(payload.get("return_code"))
        if return_code != 0:
            message = payload.get("return_msg") or payload.get("return_message") or "unknown error"
            raise KiwoomAPIError(
                f"Kiwoom stock quote API returned return_code={return_code} for stock_code={stk_cd}: {message}"
            )

        return StockQuote(
            stock_code=str(_first_present(payload, "stk_cd", "stock_code") or stk_cd),
            stock_name=str(_first_present(payload, "stk_nm", "stock_name", "name") or ""),
            current_price=abs(_parse_int(_first_present(payload, "cur_prc", "current_price"))),
            change_price=_parse_int(_first_present(payload, "pred_pre", "change_price")),
            change_rate=_parse_float(_first_present(payload, "flu_rt", "change_rate")),
            volume=abs(_parse_int(_first_present(payload, "trde_qty", "volume"))),
            raw=payload,
        )


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _parse_return_code(value: Any) -> int:
    if value is None:
        raise KiwoomAPIError("Kiwoom stock quote response did not include return_code")
    return _parse_int(value)


def _parse_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    cleaned = str(value).strip().replace(",", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned in ("", "+", "-"):
        return 0
    return int(cleaned)


def _parse_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "").replace("%", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned in ("", "+", "-"):
        return 0.0
    return float(cleaned)
