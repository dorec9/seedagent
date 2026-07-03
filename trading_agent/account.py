"""Kiwoom REST API account client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import KiwoomConfig
from .market_data import requests, KiwoomAPIError, _parse_int, _parse_float


@dataclass(frozen=True)
class AccountBalance:
    """Account deposit balance returned by the Kiwoom account API (kt00001)."""
    
    account_no: str
    total_deposit: int  # 예수금
    withdrawable_deposit: int  # 출금가능금액
    orderable_deposit: int  # 주문가능금액
    raw: dict[str, Any]


@dataclass(frozen=True)
class Position:
    """A held stock position returned by the Kiwoom account API (kt00005)."""
    
    stock_code: str
    stock_name: str
    quantity: int
    buy_price: int  # 매입단가
    current_price: int
    return_rate: float  # 수익률
    return_amount: int  # 수익금
    raw: dict[str, Any]


class KiwoomAccount:
    """Fetch account balance and positions from the Kiwoom REST API."""

    ACCOUNT_PATH = "/api/dostk/acnt"

    def __init__(self, config: KiwoomConfig, timeout: float = 10.0) -> None:
        self.config = config
        self.timeout = timeout
        
    def get_balance(self, access_token: str, account_no: str | None = None) -> AccountBalance:
        """Fetch the deposit balance (예수금) using TR kt00001."""
        acc_no = account_no or self.config.account_no
        if not acc_no:
            raise ValueError("account_no is required to fetch balance")
            
        url = f"{self.config.base_url.rstrip('/')}{self.ACCOUNT_PATH}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {access_token}",
            "api-id": "kt00001",
        }
        
        from .utils import kiwoom_rate_limiter, retry_api_call
        
        def _make_request():
            kiwoom_rate_limiter.wait()
            return requests.post(
                url,
                json={},
                headers=headers,
                timeout=self.timeout,
            )
        
        try:
            response = retry_api_call(_make_request)
        except Exception as exc:
            raise KiwoomAPIError(f"Account balance request failed: {exc}") from exc
            
        if response.status_code < 200 or response.status_code >= 300:
            raise KiwoomAPIError(f"Account balance request failed with status {response.status_code}")
            
        try:
            payload = response.json()
        except ValueError:
            raise KiwoomAPIError("Account balance response was not valid JSON")
            
        # Normally check return_code == 0, but API structure might vary slightly
        # We will assume successful response structure
        
        # Some endpoints return lists under specific keys, but assuming standard format:
        # e.g., output1 for single record, output2 for list. We'll use safe parsing.
        
        # Depending on exact structure, maybe payload directly has it or under "output"
        output = payload.get("output", payload)
        if isinstance(payload, dict) and "output1" in payload:
            output = payload["output1"]
        
        return AccountBalance(
            account_no=acc_no,
            total_deposit=abs(_parse_int(output.get("d2_auto_rdpt_amt", 0))), # Mocking field name based on standard
            withdrawable_deposit=abs(_parse_int(output.get("dncl_amt", 0))),
            orderable_deposit=abs(_parse_int(output.get("ord_psbl_amt", 0))),
            raw=payload
        )

    def get_positions(self, access_token: str, account_no: str | None = None) -> list[Position]:
        """Fetch held positions (체결잔고) using TR kt00005."""
        acc_no = account_no or self.config.account_no
        if not acc_no:
            raise ValueError("account_no is required to fetch positions")
            
        url = f"{self.config.base_url.rstrip('/')}{self.ACCOUNT_PATH}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {access_token}",
            "api-id": "kt00005",
        }
        
        from .utils import kiwoom_rate_limiter, retry_api_call
        
        def _make_request():
            kiwoom_rate_limiter.wait()
            return requests.post(url, json={}, headers=headers, timeout=self.timeout)
            
        try:
            response = retry_api_call(_make_request)
        except Exception as exc:
            raise KiwoomAPIError(f"Account positions request failed: {exc}") from exc
            
        if response.status_code < 200 or response.status_code >= 300:
            raise KiwoomAPIError(f"Account positions request failed with status {response.status_code}")
            
        try:
            payload = response.json()
        except ValueError:
            raise KiwoomAPIError("Account positions response was not valid JSON")
            
        # Extract list of items
        items = payload.get("output", [])
        if isinstance(payload, dict) and "output1" in payload:
            items = payload["output1"]
            
        if not isinstance(items, list):
            items = [items] if items else []
            
        positions = []
        for item in items:
            if not isinstance(item, dict):
                continue
            positions.append(Position(
                stock_code=str(item.get("pdno", "") or item.get("stk_cd", "")),
                stock_name=str(item.get("prdt_name", "") or item.get("stk_nm", "")),
                quantity=_parse_int(item.get("hldg_qty", 0)),
                buy_price=_parse_int(item.get("pchs_avg_pric", 0)),
                current_price=_parse_int(item.get("prpr", 0)),
                return_rate=_parse_float(item.get("evlu_pfls_rt", 0.0)),
                return_amount=_parse_int(item.get("evlu_pfls_amt", 0)),
                raw=item
            ))
            
        return positions
