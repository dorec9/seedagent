import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

@dataclass
class WSMessage:
    action: str
    payload: Dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> 'WSMessage':
        obj = json.loads(data)
        return cls(**obj)
