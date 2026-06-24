import json
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_agent"))

import main
from auth import TokenResult
from config import KiwoomConfig

TOKEN = "sensitive-token"
APPKEY = "sensitive-appkey"
SECRETKEY = "sensitive-secretkey"


def test_main_json_output_does_not_expose_raw_access_token(monkeypatch, capsys):
    config = KiwoomConfig(
        mode="real",
        base_url="https://api.kiwoom.com",
        app_env="pytest",
        appkey=APPKEY,
        secretkey=SECRETKEY,
    )
    token_result = TokenResult(
        system="OK",
        mode="real",
        base_url_loaded=True,
        appkey_loaded=True,
        secretkey_loaded=True,
        token_issued=True,
        access_token_exists=True,
        errors=[],
        access_token=TOKEN,
    )
    auth_instance = Mock()
    auth_instance.issue_access_token.return_value = token_result
    auth_class = Mock(return_value=auth_instance)

    monkeypatch.setattr(main, "load_config", Mock(return_value=config))
    monkeypatch.setattr(main, "KiwoomAuth", auth_class)

    main.main()

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["token_issued"] is True
    assert payload["access_token_exists"] is True
    assert "access_token" not in payload
    assert TOKEN not in output
    assert APPKEY not in output
    assert SECRETKEY not in output
