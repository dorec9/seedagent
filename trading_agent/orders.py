"""Kiwoom REST API order execution client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import KiwoomConfig
from .market_data import requests, KiwoomAPIError


@dataclass(frozen=True)
class OrderResult:
    """Result of an order execution."""
    
    order_no: str
    stock_code: str
    quantity: int
    price: int
    is_buy: bool
    raw: dict[str, Any]


class KiwoomOrder:
    """Execute buy and sell orders via Kiwoom REST API."""

    ORDER_PATH = "/api/dostk/ordr"

    def __init__(self, config: KiwoomConfig, timeout: float = 10.0) -> None:
        self.config = config
        self.timeout = timeout

    def _place_order(
        self,
        access_token: str,
        account_no: str,
        stock_code: str,
        quantity: int,
        price: int,
        is_buy: bool,
    ) -> OrderResult:
        """Place an order (buy or sell)."""
        url = f"{self.config.base_url.rstrip('/')}{self.ORDER_PATH}"
        api_id = "kt10000" if is_buy else "kt10001"
        
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {access_token}",
            "api-id": api_id,
        }
        
        # 0:보통, 3:시장가
        trde_tp = "3" if price == 0 else "0"
        
        payload = {
            "dmst_stex_tp": "KRX",
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_uv": "" if price == 0 else str(price),
            "trde_tp": trde_tp,
            "cond_uv": ""
        }
        
        from .utils import kiwoom_rate_limiter, retry_api_call
        
        def _make_request():
            kiwoom_rate_limiter.wait()
            return requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            
        try:
            response = retry_api_call(_make_request)
        except Exception as exc:
            raise KiwoomAPIError(f"Order request failed: {exc}") from exc
            
        if response.status_code < 200 or response.status_code >= 300:
            raise KiwoomAPIError(f"Order request failed with status {response.status_code}")
            
        try:
            data = response.json()
        except ValueError:
            raise KiwoomAPIError("Order response was not valid JSON")
            
        # Error checking
        return_code = data.get("return_code")
        if return_code is not None and int(return_code) != 0:
            msg = data.get("return_msg", "unknown error")
            raise KiwoomAPIError(f"Order failed with return_code={return_code}: {msg}")
            
        order_no = data.get("ord_no", "")
        
        return OrderResult(
            order_no=str(order_no),
            stock_code=stock_code,
            quantity=quantity,
            price=price,
            is_buy=is_buy,
            raw=data
        )

    def buy_market(self, access_token: str, stock_code: str, quantity: int, account_no: str | None = None) -> OrderResult:
        """Execute a market buy order."""
        acc_no = account_no or self.config.account_no
        if not acc_no:
            raise ValueError("account_no is required to place an order")
        return self._place_order(access_token, acc_no, stock_code, quantity, price=0, is_buy=True)

    def sell_market(self, access_token: str, stock_code: str, quantity: int, account_no: str | None = None) -> OrderResult:
        """Execute a market sell order."""
        acc_no = account_no or self.config.account_no
        if not acc_no:
            raise ValueError("account_no is required to place an order")
        return self._place_order(access_token, acc_no, stock_code, quantity, price=0, is_buy=False)

    def check_order_status(self, access_token: str, order_no: str, account_no: str | None = None) -> str:
        """
        Check the status of an order.
        Returns one of: 'FILLED', 'PARTIAL', 'PENDING', 'REJECTED', 'CANCELED', 'UNKNOWN'
        """
        acc_no = account_no or self.config.account_no
        if not acc_no:
            raise ValueError("account_no is required to check order status")
            
        # TODO: Implement actual TR call (e.g. KIS inquire-daily-ccld TR)
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"check_order_status is mocked. Assuming order {order_no} is FILLED.")
        return "FILLED"

