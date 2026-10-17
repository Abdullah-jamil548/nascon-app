"""Roster rule checks in the exact order of section 5.11."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.errors import ApiError, validation

ROLES = {"captain", "vice_captain", "starter", "substitute"}


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise validation([field])
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise validation([field]) from exc


def transfer(state: dict[str, Any], body: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    fields: list[str] = []
    kind = body.get("type")
    if kind not in ("transfer", "loan", "release"):
        fields.append("type")
    effective_raw = body.get("effective_date")
    try:
        effective = _parse_date(effective_raw, "effective_date")
    except ApiError:
        fields.append("effective_date")
        effective = None
    loan_end = None
    if kind == "loan":
        if body.get("loan_end_date") is None:
            fields.append("loan_end_date")
        else:
            try:
                loan_end = _parse_date(body.get("loan_end_date"), "loan_end_date")
            except ApiError:
                fields.append("loan_end_date")
        if effective and loan_end and loan_end <= effective:
            fields.append("loan_end_date")
    elif body.get("loan_end_date") not in (None,):
        # Release and transfer must not require it; extra is ignored when null.
        pass
    if kind in ("transfer", "loan") and not body.get("to_squad_id"):
        fields.append("to_squad_id")
    if kind == "release" and body.get("to_squad_id") not in (None,):
        fields.append("to_squad_id")
    for required in ("request_id", "league_id", "player_id", "from_squad_id", "reason", "requested_by"):
        if not body.get(required):
            fields.append(required)
    if fields:
        raise validation(fields)

    league = state["leagues"].get(body["league_id"])
    if league is None:
        raise ApiError(404, "LEAGUE_NOT_FOUND", "League does not exist.")
    player = state["players"].get(body["player_id"])
    if player is None or player.get("league_id") != league["league_id"]:
        raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
    from_squad = state["squads"].get(body["from_squad_id"])
    to_squad = None if kind == "release" else state["squads"].get(body["to_squad_id"])
    if from_squad is None or from_squad.get("league_id") != league["league_id"]:
        raise ApiError(404, "SQUAD_NOT_FOUND", "Squad does not exist.")
    if kind != "release" and (to_squad is None or to_squad.get("league_id") != league["league_id"]):
        raise ApiError(404, "SQUAD_NOT_FOUND", "Squad does not exist.")

    if from_squad["tenant_id"] != league["tenant_id"] or (
        to_squad is not None and to_squad["tenant_id"] != league["tenant_id"]
    ):
        raise ApiError(409, "CROSS_TENANT_TRANSFER", "Squads must belong to the league tenant.")

    if player.get("squad_id") != from_squad["squad_id"]:
        raise ApiError(409, "NOT_IN_SQUAD", "Player is not in the source squad.")

    window = league["transfer_window"]
    if kind in ("transfer", "loan"):
        if effective < date.fromisoformat(window["open"]) or effective > date.fromisoformat(window["close"]):
            raise ApiError(409, "TRANSFER_WINDOW_CLOSED", "Effective date is outside the transfer window.")

    if player.get("on_loan"):
        raise ApiError(409, "PLAYER_ON_LOAN", "A loaned player cannot be moved.")

    if kind in ("transfer", "loan"):
        destination_count = _squad_count(state, to_squad["squad_id"])
        if destination_count + 1 > league["max_squad_size"]:
            raise ApiError(409, "SQUAD_FULL", "Destination squad is full.")

    if not _position_ok_after_removal(state, league, from_squad["squad_id"], player):
        raise ApiError(409, "MIN_POSITION_VIOLATION", "Source squad would miss a required position.")

    blocking = _blocking_match(state, player["player_id"], from_squad["squad_id"])
    deferred = blocking is not None
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    audit = {
        "audit_id": f"au-{len(state['audits']) + 1:04d}",
        "action": kind,
        "player_id": player["player_id"],
        "league_id": league["league_id"],
        "from_squad_id": from_squad["squad_id"],
        "to_squad_id": None if kind == "release" else to_squad["squad_id"],
        "effective_date": effective.isoformat(),
        "state": "deferred_until_match_end" if deferred else "active",
        "who": body["requested_by"],
        "when": stamp,
        "why": body["reason"],
    }
    state["audits"].append(audit)

    registration = None
    if not deferred:
        registration = _apply_now(state, player, league, from_squad, to_squad, kind, effective, loan_end)
    else:
        player["pending"] = {
            "type": kind,
            "to_squad_id": None if to_squad is None else to_squad["squad_id"],
            "effective_date": effective.isoformat(),
            "loan_end_date": None if loan_end is None else loan_end.isoformat(),
            "blocking_match_id": blocking,
        }
        registration = _registration(player, league, kind, effective, loan_end, from_squad["squad_id"])

    return {
        "request_id": body["request_id"],
        "status": "accepted",
        "state": audit["state"],
        "blocking_match_id": blocking,
        "registration": None if kind == "release" else registration,
        "audit_entry": audit,
    }


def _registration(player, league, kind, effective, loan_end, squad_id) -> dict[str, Any]:
    return {
        "player_id": player["player_id"],
        "league_id": league["league_id"],
        "squad_id": squad_id,
        "parent_squad_id": player.get("parent_squad_id"),
        "registration_type": kind,
        "start_date": effective.isoformat(),
        "loan_end_date": None if loan_end is None else loan_end.isoformat(),
        "role": player.get("role", "substitute"),
    }


def _apply_now(state, player, league, from_squad, to_squad, kind, effective, loan_end):
    old_role = player.get("role")
    if old_role in ("captain", "vice_captain"):
        player["role"] = "substitute"
    if kind == "release":
        player["squad_id"] = None
        player["role"] = None
        player["on_loan"] = False
        player["parent_squad_id"] = None
        return None
    player["role"] = "substitute"
    player["squad_id"] = to_squad["squad_id"]
    player["start_date"] = effective.isoformat()
    if kind == "loan":
        player["on_loan"] = True
        player["parent_squad_id"] = from_squad["squad_id"]
        player["loan_end_date"] = loan_end.isoformat()
        player["registration_type"] = "loan"
    else:
        player["on_loan"] = False
        player["parent_squad_id"] = None
        player["loan_end_date"] = None
        player["registration_type"] = "transfer"
    return _registration(player, league, kind, effective, loan_end, to_squad["squad_id"])


def _squad_count(state, squad_id: str) -> int:
    return sum(1 for player in state["players"].values() if player.get("squad_id") == squad_id)


def _position_ok_after_removal(state, league, squad_id, player) -> bool:
    required = league.get("required_positions") or {}
    needed = int(required.get(player["position"], 0))
    if needed <= 0:
        return True
    remaining = 0
    for other in state["players"].values():
        if other["player_id"] == player["player_id"]:
            continue
        if other.get("squad_id") == squad_id and other.get("position") == player["position"]:
            remaining += 1
    return remaining >= needed


def _blocking_match(state, player_id: str, squad_id: str) -> str | None:
    for match in state.get("matches_in_progress", []):
        if squad_id in (match.get("home_squad_id"), match.get("away_squad_id")):
            return match["match_id"]
    return None


def ensure_loan_returns(state: dict[str, Any], as_of: date, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    for player in list(state["players"].values()):
        if not player.get("on_loan") or not player.get("loan_end_date"):
            continue
        end = date.fromisoformat(player["loan_end_date"])
        if as_of <= end:
            continue
        if any(item["action"] == "loan_return" and item["player_id"] == player["player_id"] and item.get("loan_end_date") == player["loan_end_date"] for item in state["audits"]):
            continue
        parent = player.get("parent_squad_id")
        audit = {
            "audit_id": f"au-{len(state['audits']) + 1:04d}",
            "action": "loan_return",
            "player_id": player["player_id"],
            "league_id": player["league_id"],
            "from_squad_id": player.get("squad_id"),
            "to_squad_id": parent,
            "effective_date": (end + timedelta(days=1)).isoformat(),
            "state": "active",
            "who": "system",
            "when": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "why": "Loan ended",
            "loan_end_date": player["loan_end_date"],
        }
        state["audits"].append(audit)
        player["squad_id"] = parent
        player["on_loan"] = False
        player["parent_squad_id"] = None
        player["loan_end_date"] = None
        player["role"] = "substitute"
        player["registration_type"] = "loan_return"


def history_view(state: dict[str, Any], player_id: str, as_of: date) -> dict[str, Any]:
    player = state["players"].get(player_id)
    if player is None:
        raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
    ensure_loan_returns(state, as_of)
    player = state["players"][player_id]
    entries = [item for item in state["audits"] if item["player_id"] == player_id and date.fromisoformat(item["effective_date"]) <= as_of]
    return {
        "player_id": player_id,
        "as_of": as_of.isoformat(),
        "league_id": player.get("league_id"),
        "current_squad_id": player.get("squad_id") if _belongs_on(player, entries, as_of) else _squad_on(entries, player),
        "parent_squad_id": player.get("parent_squad_id"),
        "history": entries,
    }


def _belongs_on(player, entries, as_of) -> bool:
    return True


def _squad_on(entries, player):
    return player.get("squad_id")
