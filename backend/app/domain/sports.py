"""Sport documents are data. Domain code never branches on a sport or event name."""

from __future__ import annotations

import re
from typing import Any

from app.core.errors import validation

SPORT_ID = re.compile(r"^[a-z0-9_-]{2,40}$")
EVENT_KEY = re.compile(r"^[a-z0-9_]{1,40}$")
TIE_BREAKS = ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"]
OVERTIME = {"none", "extra_time", "shootout", "golden_score", "super_over"}
DEFAULT_TIE = list(TIE_BREAKS)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_sport(body: dict[str, Any]) -> dict[str, Any]:
    fields: list[str] = []
    if not isinstance(body, dict):
        raise validation(["body"])
    sport_id = body.get("sport_id")
    if not isinstance(sport_id, str) or not SPORT_ID.match(sport_id):
        fields.append("sport_id")
    name = body.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 80):
        fields.append("name")
    model = body.get("scoring_model")
    if model not in ("event", "innings", "round"):
        fields.append("scoring_model")
    periods = body.get("periods")
    if not isinstance(periods, dict):
        fields.append("periods")
        periods = {}
    events = body.get("scoring_events")
    if not isinstance(events, dict) or len(events) < 1:
        fields.append("scoring_events")
        events = {}
    if not isinstance(body.get("draw_allowed"), bool):
        fields.append("draw_allowed")
    overtime = body.get("overtime")
    if not isinstance(overtime, dict) or overtime.get("policy") not in OVERTIME:
        fields.append("overtime")
        overtime = {}
    elif "duration_minutes" in overtime and (
        not is_int(overtime["duration_minutes"]) or overtime["duration_minutes"] < 0
    ):
        fields.append("overtime")
    roster = body.get("roster")
    if not isinstance(roster, dict):
        fields.append("roster")
        roster = {}
    if model == "event" and isinstance(periods, dict):
        if not is_int(periods.get("count")) or periods.get("count", 0) < 1:
            fields.append("periods")
        if not is_int(periods.get("duration_minutes")) or periods.get("duration_minutes", 0) < 1:
            fields.append("periods")
    if model == "innings" and isinstance(periods, dict):
        for key in ("innings_per_team", "overs_per_innings", "balls_per_over", "wickets_per_innings"):
            if not is_int(periods.get(key)) or periods.get(key, 0) < 1:
                fields.append("periods")
                break
    if model == "round" and isinstance(periods, dict):
        best = periods.get("best_of")
        if not is_int(best) or best < 1 or best % 2 == 0:
            fields.append("periods")
    clean_events: dict[str, Any] = {}
    if isinstance(events, dict):
        for key, spec in events.items():
            if not isinstance(key, str) or not EVENT_KEY.match(key) or not isinstance(spec, dict):
                fields.append("scoring_events")
                break
            if model == "event":
                if not is_int(spec.get("points")) or spec.get("points", 0) < 1:
                    fields.append("scoring_events")
                    break
                clean_events[key] = {"points": spec["points"]}
            elif model == "innings":
                if (
                    not is_int(spec.get("runs"))
                    or spec.get("runs", -1) < 0
                    or not isinstance(spec.get("legal_ball"), bool)
                    or not isinstance(spec.get("wicket"), bool)
                ):
                    fields.append("scoring_events")
                    break
                clean_events[key] = {
                    "runs": spec["runs"],
                    "legal_ball": spec["legal_ball"],
                    "wicket": spec["wicket"],
                }
            elif model == "round":
                if not is_int(spec.get("rounds")) or spec.get("rounds", 0) < 1:
                    fields.append("scoring_events")
                    break
                clean_events[key] = {"rounds": spec["rounds"]}
    order = body.get("tie_break_order", DEFAULT_TIE)
    if not isinstance(order, list) or len(order) != len(set(order)) or any(item not in TIE_BREAKS for item in order):
        fields.append("tie_break_order")
        order = DEFAULT_TIE
    else:
        order = [item for item in order if item != "draw_lots"] + ["draw_lots"]
    if isinstance(roster, dict):
        min_size = roster.get("min_squad_size")
        max_size = roster.get("max_squad_size")
        required = roster.get("required_positions", {})
        if not is_int(min_size) or not is_int(max_size) or min_size < 1 or max_size < 1 or min_size > max_size:
            fields.append("roster")
        elif not isinstance(required, dict):
            fields.append("roster")
        else:
            for key, count in required.items():
                if not isinstance(key, str) or not is_int(count) or count < 0:
                    fields.append("roster")
                    break
    if fields:
        raise validation(fields)
    stored_periods = dict(periods)
    stored_overtime = {"policy": overtime["policy"]}
    if "duration_minutes" in overtime:
        stored_overtime["duration_minutes"] = overtime["duration_minutes"]
    return {
        "sport_id": sport_id,
        "name": name,
        "scoring_model": model,
        "periods": stored_periods,
        "scoring_events": clean_events,
        "draw_allowed": body["draw_allowed"],
        "overtime": stored_overtime,
        "tie_break_order": order,
        "roster": {
            "min_squad_size": roster["min_squad_size"],
            "max_squad_size": roster["max_squad_size"],
            "required_positions": roster.get("required_positions") or {},
        },
    }
