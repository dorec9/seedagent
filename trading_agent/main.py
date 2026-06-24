"""Entry point for the Kiwoom REST API authentication check."""

from __future__ import annotations

import json

from auth import KiwoomAuth
from config import load_config


def main() -> None:
    config = load_config()
    auth = KiwoomAuth(config)
    result = auth.issue_access_token()
    print(json.dumps(result, ensure_ascii=False, indent=4))


if __name__ == "__main__":
    main()
