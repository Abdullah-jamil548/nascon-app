import hashlib
import hmac
import json
from typing import Any


def canonical(obj: Any) -> str:
    """Section 5.3 canonical JSON. Integers stay integers; key order is sorted."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign(salt: str, id_: str, payload_obj: Any) -> str:
    message = (id_ + "|" + canonical(payload_obj)).encode("utf-8")
    return hmac.new(salt.encode("utf-8"), message, hashlib.sha256).hexdigest()


def lots_key(seed: int, league_id: str, team_id: str) -> str:
    return hashlib.sha256(f"{seed}|{league_id}|{team_id}".encode("utf-8")).hexdigest()
