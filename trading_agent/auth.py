"""Kiwoom REST API authentication client."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from config import KiwoomConfig


class KiwoomAuth:
    """Issue Kiwoom REST API access tokens without logging sensitive values."""

    TOKEN_PATH = "/oauth2/token"

    def __init__(self, config: KiwoomConfig, timeout: float = 10.0) -> None:
        self.config = config
        self.timeout = timeout
        self.last_status_code: int | None = None
        self.last_error: str | None = None

    def issue_access_token(self) -> dict[str, Any]:
        """Request an access token and return only success metadata."""
        errors = self.config.validation_errors()
        if errors:
            self.last_error = "; ".join(errors)
            return self._result(token_issued=False, access_token_exists=False, errors=errors)

        url = f"{self.config.base_url.rstrip('/')}{self.TOKEN_PATH}"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config.appkey,
            "secretkey": self.config.secretkey,
        }

        payload_bytes = json.dumps(body).encode("utf-8")
        token_request = request.Request(
            url,
            data=payload_bytes,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(token_request, timeout=self.timeout) as response:
                self.last_status_code = response.status
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            self.last_status_code = exc.code
            error_body = exc.read().decode("utf-8", errors="replace")
            error_message = self._extract_error_message(error_body, exc.reason)
            self.last_error = error_message
            return self._result(
                token_issued=False,
                access_token_exists=False,
                errors=[f"Token request failed with status {exc.code}: {error_message}"],
            )
        except error.URLError as exc:
            self.last_error = str(exc.reason)
            return self._result(
                token_issued=False,
                access_token_exists=False,
                errors=[f"Token request failed: {self.last_error}"],
            )
        except OSError as exc:
            self.last_error = str(exc)
            return self._result(
                token_issued=False,
                access_token_exists=False,
                errors=[f"Token request failed: {self.last_error}"],
            )

        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError:
            self.last_error = "Token response was not valid JSON"
            return self._result(
                token_issued=False,
                access_token_exists=False,
                errors=[self.last_error],
            )

        access_token_exists = bool(payload.get("access_token"))
        if not access_token_exists:
            self.last_error = "Token response did not include access_token"
            return self._result(
                token_issued=False,
                access_token_exists=False,
                errors=[self.last_error],
            )

        self.last_error = None
        return self._result(token_issued=True, access_token_exists=True, errors=[])

    def _result(
        self,
        *,
        token_issued: bool,
        access_token_exists: bool,
        errors: list[str],
    ) -> dict[str, Any]:
        return {
            "system": "OK" if token_issued and access_token_exists and not errors else "ERROR",
            "mode": self.config.mode,
            "base_url_loaded": self.config.base_url_loaded,
            "appkey_loaded": self.config.appkey_loaded,
            "secretkey_loaded": self.config.secretkey_loaded,
            "token_issued": token_issued,
            "access_token_exists": access_token_exists,
            "errors": errors,
        }

    @staticmethod
    def _extract_error_message(response_body: str, fallback: str) -> str:
        if not response_body.strip():
            return fallback

        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError:
            return response_body.strip()

        if isinstance(payload, dict):
            for key in ("message", "error_description", "error", "return_msg", "msg"):
                value = payload.get(key)
                if value:
                    return str(value)
        return str(payload)
