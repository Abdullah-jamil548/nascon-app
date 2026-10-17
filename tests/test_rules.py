from datetime import datetime, timezone

import pytest

from app.core.errors import ApiError
from app.domain.roster import history_view, transfer
from app.domain.scoring import project, validate_new_event
from app.domain.standings import standings


def _roster():
    return {
        "leagues": {
            "lg": {
                "league_id": "lg",
                "tenant_id": "tenant",
                "transfer_window": {"open": "2026-09-01", "close": "2026-09-30"},
                "max_squad_size": 3,
                "required_positions": {"gk": 1},
            }
        },
        "squads": {
            "a": {"squad_id": "a", "league_id": "lg", "tenant_id": "tenant", "name": "A"},
            "b": {"squad_id": "b", "league_id": "lg", "tenant_id": "tenant", "name": "B"},
            "c": {"squad_id": "c", "league_id": "lg", "tenant_id": "other", "name": "C"},
        },
        "players": {
            "gk": {"player_id": "gk", "name": "K", "position": "gk", "league_id": "lg", "squad_id": "a", "role": "captain", "on_loan": False},
            "df": {"player_id": "df", "name": "D", "position": "df", "league_id": "lg", "squad_id": "a", "role": "starter", "on_loan": False},
            "x": {"player_id": "x", "name": "X", "position": "df", "league_id": "lg", "squad_id": "b", "role": "starter", "on_loan": False},
            "y": {"player_id": "y", "name": "Y", "position": "df", "league_id": "lg", "squad_id": "b", "role": "starter", "on_loan": False},
            "z": {"player_id": "z", "name": "Z", "position": "df", "league_id": "lg", "squad_id": "b", "role": "starter", "on_loan": False},
        },
        "audits": [],
        "matches_in_progress": [],
    }


def _move(state, **overrides):
    body = {
        "request_id": "tr-1",
        "league_id": "lg",
        "player_id": "df",
        "type": "transfer",
        "from_squad_id": "a",
        "to_squad_id": "b",
        "effective_date": "2026-09-15",
        "loan_end_date": None,
        "reason": "Depth",
        "requested_by": "user-1",
    }
    body.update(overrides)
    return transfer(state, body, datetime(2026, 9, 15, tzinfo=timezone.utc))


def test_roster_rule_order():
    state = _roster()
    with pytest.raises(ApiError) as bad:
        _move(state, effective_date="bad")
    assert bad.value.code == "VALIDATION_ERROR"
    with pytest.raises(ApiError) as missing:
        _move(state, league_id="nope")
    assert missing.value.code == "LEAGUE_NOT_FOUND"
    with pytest.raises(ApiError) as cross:
        _move(state, to_squad_id="c")
    assert cross.value.code == "CROSS_TENANT_TRANSFER"
    with pytest.raises(ApiError) as window:
        _move(state, effective_date="2026-10-17")
    assert window.value.code == "TRANSFER_WINDOW_CLOSED"
    state["players"]["df"]["on_loan"] = True
    with pytest.raises(ApiError) as loaned:
        _move(state, player_id="df")
    assert loaned.value.code == "PLAYER_ON_LOAN"
    state["players"]["df"]["on_loan"] = False
    with pytest.raises(ApiError) as full:
        _move(state)
    assert full.value.code == "SQUAD_FULL"
    state["players"]["z"]["squad_id"] = None
    with pytest.raises(ApiError) as keeper:
        _move(state, player_id="gk")
    assert keeper.value.code == "MIN_POSITION_VIOLATION"
    moved = _move(state, player_id="df")
    assert moved["state"] == "active"
    assert moved["registration"]["squad_id"] == "b"
    assert moved["registration"]["role"] == "substitute"


def test_deferred_transfer_and_loan_return():
    state = _roster()
    state["players"]["z"]["squad_id"] = None
    state["matches_in_progress"] = [{"match_id": "live", "home_squad_id": "a", "away_squad_id": "b"}]
    deferred = _move(state, player_id="df")
    assert deferred["state"] == "deferred_until_match_end"
    assert deferred["blocking_match_id"] == "live"
    assert state["players"]["df"]["squad_id"] == "a"
    state["matches_in_progress"] = []
    loan = _move(
        state,
        request_id="tr-loan",
        player_id="df",
        type="loan",
        loan_end_date="2026-09-20",
    )
    assert loan["registration"]["registration_type"] == "loan"
    view = history_view(state, "df", __import__("datetime").date(2026, 9, 21))
    assert any(item["action"] == "loan_return" for item in view["history"])
    assert view["current_squad_id"] == "a"


FOOTBALL = {
    "scoring_model": "event",
    "periods": {"count": 2, "duration_minutes": 45},
    "scoring_events": {"goal": {"points": 1}},
    "draw_allowed": True,
    "tie_break_order": ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"],
}
CRICKET = {
    "scoring_model": "innings",
    "periods": {"innings_per_team": 1, "overs_per_innings": 1, "balls_per_over": 2, "wickets_per_innings": 1},
    "scoring_events": {
        "single": {"runs": 1, "legal_ball": True, "wicket": False},
        "wide": {"runs": 1, "legal_ball": False, "wicket": False},
        "wicket": {"runs": 0, "legal_ball": True, "wicket": True},
    },
    "draw_allowed": True,
}
SERIES = {
    "scoring_model": "round",
    "periods": {"best_of": 3},
    "scoring_events": {"map_win": {"rounds": 1}},
    "draw_allowed": False,
}


def _event(event_id, event_type, payload):
    return {"event_id": event_id, "type": event_type, "payload": payload}


def test_scoring_edges():
    events = [
        _event("1", "match_init", {"sport_id": "football", "home_team_id": "t1", "away_team_id": "t2"}),
        _event("2", "match_start", {}),
        _event("3", "score", {"team_id": "t1", "event_type": "goal"}),
        _event("4", "compensation", {"compensates": "3", "reason": "offside"}),
    ]
    view = project(FOOTBALL, events, "t1", "t2")
    assert view["home"]["score"] == 0
    assert view["status"] == "in_progress"
    with pytest.raises(ApiError) as tied:
        validate_new_event(SERIES, [_event("1", "match_init", {}), _event("2", "match_start", {})], "t1", "t2", "match_end", {})
    assert tied.value.code == "TIE_NOT_ALLOWED"
    balls = [_event("s", "match_start", {})]
    validate_new_event(CRICKET, balls, "t1", "t2", "score", {"team_id": "t1", "event_type": "single"})
    balls.append(_event("b1", "score", {"team_id": "t1", "event_type": "single"}))
    balls.append(_event("b2", "score", {"team_id": "t1", "event_type": "single"}))
    with pytest.raises(ApiError) as done:
        validate_new_event(CRICKET, balls, "t1", "t2", "score", {"team_id": "t1", "event_type": "single"})
    assert done.value.code == "INNINGS_COMPLETE"
    shown = project(CRICKET, balls, "t1", "t2")
    assert shown["home"]["overs"] == "1.0"
    rounds = [_event("s", "match_start", {}), _event("r1", "score", {"team_id": "t1", "event_type": "map_win"}), _event("r2", "score", {"team_id": "t1", "event_type": "map_win"})]
    with pytest.raises(ApiError) as series:
        validate_new_event(SERIES, rounds, "t1", "t2", "score", {"team_id": "t1", "event_type": "map_win"})
    assert series.value.code == "SERIES_ALREADY_DECIDED"


def test_release_and_missing_roster_entities():
    state = _roster()
    released = _move(state, request_id="rel-1", type="release", to_squad_id=None, player_id="df")
    assert released["registration"] is None
    assert state["players"]["df"]["squad_id"] is None
    with pytest.raises(ApiError) as missing_player:
        _move(state, player_id="nobody")
    assert missing_player.value.code == "PLAYER_NOT_FOUND"
    with pytest.raises(ApiError) as not_in:
        _move(state, player_id="df")
    assert not_in.value.code == "NOT_IN_SQUAD"
    with pytest.raises(ApiError) as bad_type:
        _move(state, player_id="x", type="swap")
    assert bad_type.value.code == "VALIDATION_ERROR"


def test_discipline_tiebreak_and_nrr():
    started = [
        _event("1", "match_init", {}),
        _event("2", "match_start", {}),
    ]
    validate_new_event(FOOTBALL, started, "t1", "t2", "discipline", {"team_id": "t1", "points": 2})
    validate_new_event(SERIES, started, "t1", "t2", "tiebreak_winner", {"team_id": "t1"})
    finished = project(
        SERIES,
        started
        + [
            _event("3", "tiebreak_winner", {"team_id": "t1"}),
            _event("4", "match_end", {}),
        ],
        "t1",
        "t2",
    )
    assert finished["winner_team_id"] == "t1"
    assert finished["outcome"] == "home_win"
    sport = {
        "scoring_model": "innings",
        "periods": {"balls_per_over": 6, "overs_per_innings": 20, "wickets_per_innings": 10},
    }
    matches = [
        {
            "home": "t1",
            "away": "t2",
            "status": "completed",
            "outcome": "home_win",
            "home_score": 50,
            "away_score": 40,
            "home_balls": 30,
            "away_balls": 120,
            "home_wickets": 10,
            "away_wickets": 0,
            "home_fair_play": 0,
            "away_fair_play": 0,
        }
    ]
    table = standings(sport, ["t1", "t2"], matches, {"win": 2, "draw": 1, "loss": 0}, ["overall_diff", "draw_lots"], 1, 42, "lg")
    assert table["table"][0]["diff"] == 0.5
    assert table["table"][1]["diff"] == -0.5


def test_three_way_tie_and_lots():
    sport = FOOTBALL
    teams = ["t1", "t2", "t3"]
    matches = []
    # Each pair draws, so points are level and draw_lots decides.
    for home, away in (("t1", "t2"), ("t2", "t3"), ("t3", "t1")):
        matches.append(
            {
                "home": home,
                "away": away,
                "status": "completed",
                "outcome": "draw",
                "home_score": 1,
                "away_score": 1,
                "home_fair_play": 0,
                "away_fair_play": 0,
                "home_balls": 0,
                "away_balls": 0,
                "home_wickets": 0,
                "away_wickets": 0,
            }
        )
    table = standings(sport, teams, matches, {"win": 3, "draw": 1, "loss": 0}, sport["tie_break_order"], 1, 42, "lg-1")
    assert [row["team_id"] for row in table["table"]] == ["t1", "t3", "t2"]
    assert table["table"][0]["resolved_by"] == "draw_lots"
    assert table["h2h"]["t1"]["t2"]["played"] == 1
