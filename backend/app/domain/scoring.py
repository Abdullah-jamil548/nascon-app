"""Event-sourced score projection. Event names come from sport config, never from code branches."""

from __future__ import annotations

from typing import Any

from app.core.errors import ApiError, validation


def project(sport: dict[str, Any], events: list[dict[str, Any]], home: str, away: str) -> dict[str, Any]:
    model = sport["scoring_model"]
    compensated_by: dict[str, str] = {}
    for event in events:
        if event["type"] == "compensation":
            target = event["payload"].get("compensates")
            if target:
                compensated_by[target] = event["event_id"]

    def active(event: dict[str, Any]) -> bool:
        return event["event_id"] not in compensated_by and event["type"] != "compensation"

    status = "upcoming"
    for event in events:
        if event["type"] == "match_start":
            status = "in_progress"
        elif event["type"] == "match_end":
            status = "completed"

    home_side = _blank_side(home, model)
    away_side = _blank_side(away, model)
    sides = {home: home_side, away: away_side}
    tiebreak = None
    for event in events:
        if not active(event):
            continue
        payload = event["payload"]
        if event["type"] == "score":
            _apply_score(sport, sides[payload["team_id"]], payload["event_type"])
        elif event["type"] == "discipline":
            sides[payload["team_id"]]["fair_play"] += int(payload["points"])
        elif event["type"] == "tiebreak_winner":
            tiebreak = payload["team_id"]

    outcome, winner = _outcome(sport, status, home, away, home_side, away_side, tiebreak)
    return {
        "status": status,
        "outcome": outcome,
        "winner_team_id": winner,
        "tiebreak_winner_team_id": tiebreak,
        "home": _public_side(home_side, model, sport),
        "away": _public_side(away_side, model, sport),
        "compensated_by": compensated_by,
    }


def _blank_side(team_id: str, model: str) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "score": 0,
        "wickets": 0 if model == "innings" else None,
        "balls": 0 if model == "innings" else None,
        "fair_play": 0,
        "innings": [],
    }


def _apply_score(sport: dict[str, Any], side: dict[str, Any], event_type: str) -> None:
    spec = sport["scoring_events"][event_type]
    model = sport["scoring_model"]
    if model == "event":
        side["score"] += int(spec["points"])
    elif model == "round":
        side["score"] += int(spec["rounds"])
    else:
        side["score"] += int(spec["runs"])
        if spec["legal_ball"]:
            side["balls"] += 1
        if spec["wicket"]:
            side["wickets"] += 1


def _public_side(side: dict[str, Any], model: str, sport: dict[str, Any]) -> dict[str, Any]:
    overs = None
    wickets = None
    balls = None
    if model == "innings":
        balls = side["balls"]
        wickets = side["wickets"]
        balls_per = int(sport["periods"]["balls_per_over"])
        overs = f"{balls // balls_per}.{balls % balls_per}"
    return {
        "team_id": side["team_id"],
        "score": side["score"],
        "wickets": wickets,
        "balls": balls,
        "overs": overs,
        "fair_play": side["fair_play"],
    }


def _outcome(sport, status, home, away, home_side, away_side, tiebreak):
    if status != "completed":
        return "pending", None
    if home_side["score"] > away_side["score"]:
        return "home_win", home
    if away_side["score"] > home_side["score"]:
        return "away_win", away
    if tiebreak == home:
        return "home_win", home
    if tiebreak == away:
        return "away_win", away
    if sport["draw_allowed"]:
        return "draw", None
    return "undecided", None


def limits_block(sport: dict[str, Any], side: dict[str, Any]) -> str | None:
    model = sport["scoring_model"]
    periods = sport["periods"]
    if model == "innings":
        ball_cap = (
            int(periods["innings_per_team"])
            * int(periods["overs_per_innings"])
            * int(periods["balls_per_over"])
        )
        wicket_cap = int(periods["innings_per_team"]) * int(periods["wickets_per_innings"])
        if side["balls"] >= ball_cap or side["wickets"] >= wicket_cap:
            return "INNINGS_COMPLETE"
    if model == "round":
        needed = (int(periods["best_of"]) + 1) // 2
        if side["score"] >= needed:
            return "SERIES_ALREADY_DECIDED"
    return None


def validate_new_event(sport: dict[str, Any], events: list[dict[str, Any]], home: str, away: str, event_type: str, payload: dict[str, Any]) -> None:
    """Apply the section 5.9 rule order for one new event. The event is not yet in the log."""
    if event_type == "match_init":
        raise ApiError(409, "MATCH_EXISTS", "Match stream already exists.")
    projection = project(sport, events, home, away)
    status = projection["status"]
    if event_type == "match_start":
        if status != "upcoming":
            raise ApiError(409, "ILLEGAL_TRANSITION", "Match cannot be started from its current status.")
        return
    if event_type == "score":
        if status != "in_progress":
            raise ApiError(409, "MATCH_NOT_IN_PROGRESS", "Score events require a match in progress.")
        team_id = payload.get("team_id")
        if team_id not in (home, away):
            raise validation(["team_id"], "team_id must be a participant.")
        name = payload.get("event_type")
        if name not in sport["scoring_events"]:
            raise ApiError(400, "UNKNOWN_EVENT_TYPE", "event_type is not defined for this sport.")
        current = project(sport, events, home, away)
        side = current["home"] if team_id == home else current["away"]
        internal = {
            "team_id": team_id,
            "score": side["score"],
            "wickets": side["wickets"] or 0,
            "balls": side["balls"] or 0,
            "fair_play": side["fair_play"],
        }
        blocked = limits_block(sport, internal)
        if blocked:
            message = "Innings is complete." if blocked == "INNINGS_COMPLETE" else "Series is already decided."
            raise ApiError(409, blocked, message)
        return
    if event_type == "discipline":
        if status != "in_progress":
            raise ApiError(409, "MATCH_NOT_IN_PROGRESS", "Discipline events require a match in progress.")
        if payload.get("team_id") not in (home, away):
            raise validation(["team_id"])
        if not isinstance(payload.get("points"), int) or isinstance(payload.get("points"), bool):
            raise validation(["points"])
        return
    if event_type == "tiebreak_winner":
        if status != "in_progress":
            raise ApiError(409, "MATCH_NOT_IN_PROGRESS", "Tie-break requires a match in progress.")
        if payload.get("team_id") not in (home, away):
            raise validation(["team_id"])
        return
    if event_type == "match_end":
        if status != "in_progress":
            raise ApiError(409, "ILLEGAL_TRANSITION", "Match cannot be ended from its current status.")
        home_score = projection["home"]["score"]
        away_score = projection["away"]["score"]
        if home_score == away_score and not sport["draw_allowed"] and projection["tiebreak_winner_team_id"] is None:
            raise ApiError(409, "TIE_NOT_ALLOWED", "Level scores need a tie-break when draws are not allowed.")
        return
    if event_type == "compensation":
        return
    raise validation(["type"])
