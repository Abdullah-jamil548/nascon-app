"""Standings rebuilt from completed match projections. No sport-name branches."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.core.canonical import lots_key


CRITERIA = ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"]


def standings(
    sport: dict[str, Any],
    teams: list[str],
    matches: list[dict[str, Any]],
    points: dict[str, int],
    tie_break_order: list[str],
    spots: int,
    lots_seed: int,
    league_id: str,
) -> dict[str, Any]:
    rows = {
        team: {
            "team_id": team,
            "played": 0,
            "won": 0,
            "drawn": 0,
            "lost": 0,
            "scored": 0,
            "conceded": 0,
            "fair_play": 0,
            "points": 0,
        }
        for team in teams
    }
    counted: list[dict[str, Any]] = []
    remaining = {team: 0 for team in teams}
    for match in matches:
        home = match["home"]
        away = match["away"]
        played = match["status"] == "completed" and match["outcome"] != "undecided"
        if not played:
            remaining[home] += 1
            remaining[away] += 1
            continue
        match["win_points"] = points["win"]
        match["draw_points"] = points["draw"]
        match["loss_points"] = points["loss"]
        counted.append(match)
        _apply_match(rows, match, points)

    for team, row in rows.items():
        row["diff"] = _diff(sport, team, counted)
        row["remaining"] = remaining[team]

    criteria = [item for item in tie_break_order if item in CRITERIA and item != "draw_lots"]
    criteria.append("draw_lots")
    groups = _points_groups(teams, rows)
    ordered: list[str] = []
    for group in groups:
        ordered.extend(_resolve(group, criteria, sport, counted, rows, lots_seed, league_id))

    resolved = {}
    for index, team in enumerate(ordered):
        nxt = ordered[index + 1] if index + 1 < len(ordered) else None
        if nxt is not None and rows[team]["points"] == rows[nxt]["points"]:
            group = [item for item in ordered if rows[item]["points"] == rows[team]["points"]]
            resolved[team] = _separating(team, nxt, group, criteria, sport, counted, rows, lots_seed, league_id)
        else:
            resolved[team] = None

    table = []
    for index, team in enumerate(ordered):
        row = rows[team]
        table.append(
            {
                "rank": index + 1,
                "team_id": team,
                "played": row["played"],
                "won": row["won"],
                "drawn": row["drawn"],
                "lost": row["lost"],
                "scored": row["scored"],
                "conceded": row["conceded"],
                "diff": row["diff"],
                "points": row["points"],
                "fair_play": row["fair_play"],
                "status": _qualification(team, rows, remaining, spots, points["win"]),
                "resolved_by": resolved[team],
            }
        )
    h2h = {}
    for left in teams:
        h2h[left] = {}
        for right in teams:
            if left == right:
                continue
            h2h[left][right] = _pair(left, right, counted, points)
    return {"table": table, "h2h": h2h}


def _apply_match(rows: dict[str, dict], match: dict[str, Any], points: dict[str, int]) -> None:
    home = match["home"]
    away = match["away"]
    home_score = match["home_score"]
    away_score = match["away_score"]
    rows[home]["played"] += 1
    rows[away]["played"] += 1
    rows[home]["scored"] += home_score
    rows[home]["conceded"] += away_score
    rows[away]["scored"] += away_score
    rows[away]["conceded"] += home_score
    rows[home]["fair_play"] += match["home_fair_play"]
    rows[away]["fair_play"] += match["away_fair_play"]
    outcome = match["outcome"]
    if outcome == "home_win":
        rows[home]["won"] += 1
        rows[away]["lost"] += 1
        rows[home]["points"] += points["win"]
        rows[away]["points"] += points["loss"]
    elif outcome == "away_win":
        rows[away]["won"] += 1
        rows[home]["lost"] += 1
        rows[away]["points"] += points["win"]
        rows[home]["points"] += points["loss"]
    else:
        rows[home]["drawn"] += 1
        rows[away]["drawn"] += 1
        rows[home]["points"] += points["draw"]
        rows[away]["points"] += points["draw"]


def _diff(sport: dict[str, Any], team: str, matches: list[dict[str, Any]]) -> Any:
    model = sport["scoring_model"]
    if model == "innings":
        return _nrr(sport, team, matches)
    scored = conceded = 0
    for match in matches:
        if match["home"] == team:
            scored += match["home_score"]
            conceded += match["away_score"]
        elif match["away"] == team:
            scored += match["away_score"]
            conceded += match["home_score"]
    return scored - conceded


def _nrr(sport: dict[str, Any], team: str, matches: list[dict[str, Any]]) -> float:
    runs_for = runs_against = 0
    overs_for = overs_against = Decimal(0)
    balls_per = Decimal(int(sport["periods"]["balls_per_over"]))
    full = Decimal(int(sport["periods"]["overs_per_innings"]))
    wicket_cap = int(sport["periods"]["wickets_per_innings"])
    for match in matches:
        if team not in (match["home"], match["away"]):
            continue
        mine = "home" if match["home"] == team else "away"
        other = "away" if mine == "home" else "home"
        runs_for += match[f"{mine}_score"]
        runs_against += match[f"{other}_score"]
        overs_for += _overs(match[f"{mine}_balls"], match[f"{mine}_wickets"], balls_per, full, wicket_cap)
        overs_against += _overs(match[f"{other}_balls"], match[f"{other}_wickets"], balls_per, full, wicket_cap)
    if overs_for == 0 or overs_against == 0:
        return 0.0
    rate = (Decimal(runs_for) / overs_for) - (Decimal(runs_against) / overs_against)
    return float(rate.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def _overs(balls: int, wickets: int, balls_per: Decimal, full: Decimal, wicket_cap: int) -> Decimal:
    if wickets >= wicket_cap:
        return full
    return Decimal(balls) / balls_per


def _points_groups(teams: list[str], rows: dict[str, dict]) -> list[list[str]]:
    order = sorted(teams, key=lambda team: -rows[team]["points"])
    groups: list[list[str]] = []
    for team in order:
        if not groups or rows[groups[-1][0]]["points"] != rows[team]["points"]:
            groups.append([team])
        else:
            groups[-1].append(team)
    return groups


def _value(criterion, team, group, sport, matches, rows, lots_seed, league_id):
    inside = [match for match in matches if match["home"] in group and match["away"] in group]
    if criterion == "h2h_points":
        return _mini_points(team, inside)
    if criterion == "h2h_diff":
        return _diff(sport, team, inside)
    if criterion == "overall_diff":
        return rows[team]["diff"]
    if criterion == "scored":
        return rows[team]["scored"]
    if criterion == "fair_play":
        return rows[team]["fair_play"]
    return lots_key(lots_seed, league_id, team)


def _mini_points(team: str, matches: list[dict[str, Any]]) -> int:
    total = 0
    for match in matches:
        if match["home"] == team and match["outcome"] == "home_win":
            total += match["win_points"]
        elif match["away"] == team and match["outcome"] == "away_win":
            total += match["win_points"]
        elif team in (match["home"], match["away"]) and match["outcome"] == "draw":
            total += match["draw_points"]
        elif team in (match["home"], match["away"]) and match["outcome"] in ("home_win", "away_win"):
            total += match["loss_points"]
    return total


def _better(criterion: str, left, right) -> bool:
    if criterion == "draw_lots":
        return left < right
    return left > right


def _resolve(group, criteria, sport, matches, rows, lots_seed, league_id) -> list[str]:
    if len(group) <= 1:
        return list(group)
    for criterion in criteria:
        values = {
            team: _value(criterion, team, group, sport, matches, rows, lots_seed, league_id)
            for team in group
        }
        if len(set(values.values())) == 1:
            continue
        buckets: list[list[str]] = []
        ordered_values = sorted(set(values.values()), key=lambda value: (value if criterion == "draw_lots" else -value))
        # Numeric higher is better. Hashes: ascending, so smaller is better. sorted(set) for hashes is ascending.
        if criterion != "draw_lots":
            ordered_values = sorted(set(values.values()), reverse=True)
        for value in ordered_values:
            bucket = [team for team in group if values[team] == value]
            if len(bucket) == 1:
                buckets.append(bucket)
            else:
                buckets.append(_resolve(bucket, criteria, sport, matches, rows, lots_seed, league_id))
        flat: list[str] = []
        for bucket in buckets:
            flat.extend(bucket)
        return flat
    return sorted(group, key=lambda team: lots_key(lots_seed, league_id, team))


def _separating(better, worse, group, criteria, sport, matches, rows, lots_seed, league_id):
    for criterion in criteria:
        values = {
            team: _value(criterion, team, group, sport, matches, rows, lots_seed, league_id)
            for team in group
        }
        if len(set(values.values())) == 1:
            continue
        if values[better] != values[worse]:
            return criterion
        bucket = [team for team in group if values[team] == values[better]]
        return _separating(better, worse, bucket, criteria, sport, matches, rows, lots_seed, league_id)
    return "draw_lots"


def _qualification(team, rows, remaining, spots, win_points) -> str:
    mine = rows[team]["points"]
    ceiling = mine + win_points * remaining[team]
    others_can_reach = 0
    others_ahead = 0
    for other, row in rows.items():
        if other == team:
            continue
        other_ceiling = row["points"] + win_points * remaining[other]
        if other_ceiling >= mine:
            others_can_reach += 1
        if row["points"] > ceiling:
            others_ahead += 1
    if others_can_reach < spots:
        return "clinched"
    if others_ahead >= spots:
        return "eliminated"
    return "still_possible"


def _pair(left: str, right: str, matches: list[dict[str, Any]], points: dict[str, int]) -> dict[str, int]:
    played = scored = conceded = earned = 0
    for match in matches:
        if {match["home"], match["away"]} != {left, right}:
            continue
        played += 1
        if match["home"] == left:
            scored += match["home_score"]
            conceded += match["away_score"]
            if match["outcome"] == "home_win":
                earned += points["win"]
            elif match["outcome"] == "away_win":
                earned += points["loss"]
            else:
                earned += points["draw"]
        else:
            scored += match["away_score"]
            conceded += match["home_score"]
            if match["outcome"] == "away_win":
                earned += points["win"]
            elif match["outcome"] == "home_win":
                earned += points["loss"]
            else:
                earned += points["draw"]
    return {"played": played, "points": earned, "scored": scored, "conceded": conceded}
