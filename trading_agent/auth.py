"""Kiwoom REST API authentication client."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from config import KiwoomConfig


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
        token_request = request.Request(
            url,
            data=payload_bytes,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with request.urlopen(token_request, timeout=timeout) as response:
                return _ResponseAdapter(response.status, response.read().decode("utf-8"))
        except error.HTTPError as exc:
            return _ResponseAdapter(exc.code, exc.read().decode("utf-8", errors="replace"))
        except (error.URLError, OSError) as exc:
            raise _RequestsAdapter.RequestException(str(exc)) from exc


requests = _RequestsAdapter()


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

        try:
            response = requests.post(
                url,
                json=body,
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except _RequestsAdapter.RequestException as exc:
            self.last_error = str(exc)
            return self._result(
                token_issued=False,
                access_token_exists=False,
                errors=[f"Token request failed: {self.last_error}"],
            )

        self.last_status_code = response.status_code
        if response.status_code != 200:
            self.last_error = f"Token request failed with status {response.status_code}"
            return self._result(
                token_issued=False,
                access_token_exists=False,
                errors=[self.last_error],
            )

        try:
            payload = response.json()
        except ValueError:
            self.last_error = "Token response was not valid JSON"
            return self._result(
                token_issued=False,
                access_token_exists=False,
                errors=[self.last_error],
            )

        token = payload.get("token") or payload.get("access_token")
        access_token_exists = bool(token)
        if not access_token_exists:
            self.last_error = "Token response did not include token or access_token"
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
