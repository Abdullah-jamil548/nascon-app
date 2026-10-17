"""Fixture generators. No sport-name branches. Third-party schedulers are not used."""

from __future__ import annotations

import math
from typing import Any

import networkx as nx
from networkx.algorithms.matching import max_weight_matching

from app.core.errors import ApiError, validation

TIE_BREAKS = ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"]


def _teams_ok(teams: list[Any], low: int, high: int) -> list[str]:
    if not isinstance(teams, list) or not (low <= len(teams) <= high):
        raise validation(["teams"], f"Team count must be between {low} and {high}.")
    if len(set(teams)) != len(teams):
        raise validation(["teams"], "Team ids must be unique.")
    for team in teams:
        if not isinstance(team, str):
            raise validation(["teams"], "Team ids must be strings.")
    return teams


def _circle_rounds(teams: list[str]) -> list[list[tuple[str | None, str | None]]]:
    """Circle method. None marks a bye slot. Returns one leg of rounds."""
    rotation = list(teams)
    if len(rotation) % 2 == 1:
        rotation.append(None)
    count = len(rotation)
    rounds: list[list[tuple[str | None, str | None]]] = []
    for round_index in range(count - 1):
        pairs: list[tuple[str | None, str | None]] = []
        for i in range(count // 2):
            left = rotation[i]
            right = rotation[count - 1 - i]
            # Only the fixed seat alternates. Flipping every pair breaks the bye balance.
            if i == 0 and round_index % 2 == 1:
                left, right = right, left
            pairs.append((left, right))
        rounds.append(pairs)
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
    return rounds


def _balance_leg(rounds: list[list[tuple[str | None, str | None]]]) -> list[list[tuple[str | None, str | None]]]:
    """Flip individual games until every team is within one home of away."""
    from collections import defaultdict

    home_count: dict[str, int] = defaultdict(int)
    away_count: dict[str, int] = defaultdict(int)
    placed: list[list[tuple[str | None, str | None]]] = []
    for pairs in rounds:
        new_pairs: list[tuple[str | None, str | None]] = []
        for left, right in pairs:
            if left is None or right is None:
                new_pairs.append((left, right))
                continue
            # Prefer the direction that reduces imbalance.
            def cost(home: str, away: str) -> tuple[int, int]:
                return (
                    abs((home_count[home] + 1) - away_count[home])
                    + abs(home_count[away] - (away_count[away] + 1)),
                    home_count[home] - away_count[home],
                )

            keep = cost(left, right)
            flip = cost(right, left)
            if flip < keep:
                left, right = right, left
            home_count[left] += 1
            away_count[right] += 1
            new_pairs.append((left, right))
        placed.append(new_pairs)
    return placed


def generate_round_robin(teams: list[str], params: dict[str, Any] | None) -> dict[str, Any]:
    teams = _teams_ok(teams, 2, 40)
    params = params or {}
    legs = params.get("legs", 1)
    if legs not in (1, 2):
        raise validation(["legs"], "legs must be 1 or 2.")
    policy = params.get("withdrawal_policy", "forfeit_remaining")
    if policy not in ("forfeit_remaining", "void_all"):
        raise validation(["withdrawal_policy"])
    withdrawals = params.get("withdrawals", [])
    if not isinstance(withdrawals, list):
        raise validation(["withdrawals"])

    n = len(teams)
    rounds_per_leg = (n - 1) if n % 2 == 0 else n
    round_count = rounds_per_leg * legs

    parsed: list[tuple[str, int]] = []
    known = set(teams)
    for item in withdrawals:
        if not isinstance(item, dict):
            raise validation(["withdrawals"])
        team_id = item.get("team_id")
        after_round = item.get("after_round")
        if team_id not in known or not isinstance(after_round, int) or isinstance(after_round, bool):
            raise validation(["withdrawals"])
        if after_round < 0 or after_round > round_count:
            raise validation(["withdrawals"], "after_round is outside 0..round_count.")
        parsed.append((team_id, after_round))
    withdrawn_at = {team_id: after for team_id, after in parsed}

    leg_rounds = _circle_rounds(teams)

    rounds_out: list[dict[str, Any]] = []
    summary = {"total": 0, "scheduled": 0, "forfeit": 0, "voided": 0}
    for leg in range(1, legs + 1):
        for offset, pairs in enumerate(leg_rounds, start=1):
            global_round = (leg - 1) * rounds_per_leg + offset
            matches = []
            byes: list[str] = []
            match_index = 1
            for left, right in pairs:
                if left is None and right is None:
                    continue
                if left is None or right is None:
                    present = right if left is None else left
                    if present is not None:
                        byes.append(present)
                    continue
                home, away = (left, right) if leg == 1 else (right, left)
                status = "scheduled"
                awarded = None
                home_after = withdrawn_at.get(home)
                away_after = withdrawn_at.get(away)
                home_out = home_after is not None
                away_out = away_after is not None
                if home_out and away_out:
                    status = "voided"
                elif policy == "void_all" and (home_out or away_out):
                    status = "voided"
                elif policy == "forfeit_remaining":
                    home_gone = home_out and global_round > home_after
                    away_gone = away_out and global_round > away_after
                    if home_gone and away_gone:
                        status = "voided"
                    elif home_gone:
                        status = "forfeit"
                        awarded = away
                    elif away_gone:
                        status = "forfeit"
                        awarded = home
                matches.append(
                    {
                        "match_id": f"RR-L{leg}-R{global_round}-M{match_index}",
                        "leg": leg,
                        "home": home,
                        "away": away,
                        "status": status,
                        "awarded_to": awarded,
                    }
                )
                match_index += 1
                summary["total"] += 1
                summary[status if status != "scheduled" else "scheduled"] += 1
            rounds_out.append({"round": global_round, "matches": matches, "byes": byes})

    return {
        "format": "round_robin",
        "legs": legs,
        "team_count": n,
        "round_count": round_count,
        "rounds": rounds_out,
        "summary": summary,
    }


def _force_balance(rounds: list[list[tuple[str | None, str | None]]]) -> list[list[tuple[str | None, str | None]]]:
    from collections import defaultdict

    def counts(source: list[list[tuple[str | None, str | None]]]) -> dict[str, list[int]]:
        found: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for pairs in source:
            for home, away in pairs:
                if home is not None and away is not None:
                    found[home][0] += 1
                    found[away][1] += 1
        return found

    current = [list(pairs) for pairs in rounds]
    for _ in range(len(current) * 4):
        tallies = counts(current)
        worst = None
        for team, (home, away) in tallies.items():
            gap = abs(home - away)
            if gap > 1 and (worst is None or gap > worst[0]):
                worst = (gap, team, home, away)
        if worst is None:
            return current
        _, team, home_n, away_n = worst
        flipped = False
        for pairs in current:
            for index, (left, right) in enumerate(pairs):
                if left is None or right is None:
                    continue
                if home_n > away_n and left == team:
                    pairs[index] = (right, left)
                    flipped = True
                    break
                if away_n > home_n and right == team:
                    pairs[index] = (right, left)
                    flipped = True
                    break
            if flipped:
                break
        if not flipped:
            break
    return current


def generate_swiss(teams: list[str], params: dict[str, Any] | None) -> dict[str, Any]:
    teams = _teams_ok(teams, 2, 64)
    params = params or {}
    n = len(teams)
    total_rounds = params.get("total_rounds", math.ceil(math.log2(n)))
    if not isinstance(total_rounds, int) or isinstance(total_rounds, bool) or not (1 <= total_rounds <= n - 1):
        raise validation(["total_rounds"])
    round_no = params.get("round", 1)
    if not isinstance(round_no, int) or isinstance(round_no, bool) or not (1 <= round_no <= total_rounds):
        raise validation(["round"])
    history = params.get("history", [])
    if not isinstance(history, list):
        raise validation(["history"])
    expected = list(range(1, round_no))
    got = [item.get("round") if isinstance(item, dict) else None for item in history]
    if got != expected:
        raise validation(["history"], "history must contain exactly rounds 1..round-1.")

    played: set[tuple[str, str]] = set()
    had_bye: set[str] = set()
    scores = {team: 0.0 for team in teams}
    opponents: dict[str, list[str]] = {team: [] for team in teams}

    for item in history:
        matches = item.get("matches", [])
        bye = item.get("bye", None)
        if bye is not None and bye not in scores:
            raise validation(["history"])
        if n % 2 == 0 and bye is not None:
            raise validation(["history"])
        if n % 2 == 1 and bye is None:
            raise validation(["history"])
        seen: set[str] = set()
        if bye is not None:
            seen.add(bye)
            had_bye.add(bye)
            scores[bye] += 1.0
        if not isinstance(matches, list):
            raise validation(["history"])
        for match in matches:
            if not isinstance(match, dict):
                raise validation(["history"])
            home = match.get("home")
            away = match.get("away")
            result = match.get("result")
            if home not in scores or away not in scores or home == away or result not in ("home", "away", "draw"):
                raise validation(["history"])
            if home in seen or away in seen:
                raise validation(["history"])
            seen.update((home, away))
            pair = tuple(sorted((home, away)))
            played.add(pair)
            opponents[home].append(away)
            opponents[away].append(home)
            if result == "home":
                scores[home] += 1.0
            elif result == "away":
                scores[away] += 1.0
            else:
                scores[home] += 0.5
                scores[away] += 0.5
        if len(seen) != n:
            raise validation(["history"], "Each history round must include every team once.")

    buchholz = {
        team: sum(scores[other] for other in opponents[team])
        for team in teams
    }
    ordered = sorted(teams, key=lambda team: (-scores[team], -buchholz[team], teams.index(team)))
    standings = [
        {
            "rank": index + 1,
            "team_id": team,
            "score": scores[team],
            "buchholz": buchholz[team],
        }
        for index, team in enumerate(ordered)
    ]

    if round_no == 1:
        pairings, bye = _swiss_round_one(teams)
    else:
        pairings, bye = _swiss_later(teams, scores, played, had_bye, round_no)

    return {
        "format": "swiss",
        "round": round_no,
        "total_rounds": total_rounds,
        "pairings": [
            {"match_id": f"SW-R{round_no}-M{index}", "home": home, "away": away}
            for index, (home, away) in enumerate(pairings, start=1)
        ],
        "bye": bye,
        "standings": standings,
    }


def _swiss_round_one(teams: list[str]) -> tuple[list[tuple[str, str]], str | None]:
    n = len(teams)
    midpoint = n // 2
    bye = teams[-1] if n % 2 == 1 else None
    pairings = []
    for index in range(1, midpoint + 1):
        left = teams[index - 1]
        right = teams[index - 1 + midpoint]
        pairings.append((left, right))
    return pairings, bye


def _swiss_later(
    teams: list[str],
    scores: dict[str, float],
    played: set[tuple[str, str]],
    had_bye: set[str],
    round_no: int,
) -> tuple[list[tuple[str, str]], str | None]:
    n = len(teams)

    def matching_of(pool: list[str]) -> list[tuple[str, str]] | None:
        if len(pool) % 2 == 1:
            return None
        graph = nx.Graph()
        graph.add_nodes_from(pool)
        for i, left in enumerate(pool):
            for right in pool[i + 1 :]:
                if tuple(sorted((left, right))) in played:
                    continue
                cost = abs(scores[left] - scores[right])
                graph.add_edge(left, right, weight=1000 - cost)
        if graph.number_of_edges() == 0 and len(pool) > 1:
            return None
        matched = max_weight_matching(graph, maxcardinality=True)
        edges = list(matched.items()) if isinstance(matched, dict) else list(matched)
        pairs = []
        seen: set[str] = set()
        for left, right in edges:
            if left in seen or right in seen:
                continue
            seen.add(left)
            seen.add(right)
            pairs.append(tuple(sorted((left, right), key=teams.index)))
        if len(seen) != len(pool):
            return None
        pairs.sort(key=lambda item: teams.index(item[0]))
        return pairs

    if n % 2 == 0:
        found = matching_of(teams)
        if found is None:
            raise ApiError(409, "NO_VALID_PAIRING", "No rematch-free pairing exists.", {"round": round_no})
        return found, None

    candidates = sorted(
        [team for team in teams if team not in had_bye],
        key=lambda team: (scores[team], -teams.index(team)),
    )
    for bye in candidates:
        pool = [team for team in teams if team != bye]
        found = matching_of(pool)
        if found is not None:
            return found, bye
    raise ApiError(409, "NO_VALID_PAIRING", "No rematch-free pairing exists.", {"round": round_no})


def _seed_order(bracket: int) -> list[int]:
    if bracket == 2:
        return [1, 2]
    half = _seed_order(bracket // 2)
    limit = bracket
    ordered: list[int] = []
    for seed in half:
        ordered.extend([seed, limit + 1 - seed])
    return ordered


def _next_power(n: int) -> int:
    size = 1
    while size < n:
        size *= 2
    return size


def generate_single_elim(teams: list[str], params: dict[str, Any] | None) -> dict[str, Any]:
    teams = _teams_ok(teams, 2, 128)
    params = params or {}
    third = bool(params.get("third_place_match", False))
    results = params.get("results", [])
    if not isinstance(results, list):
        raise validation(["results"])
    return _elimination(teams, third, results, double=False, bracket_reset=False)


def generate_double_elim(teams: list[str], params: dict[str, Any] | None) -> dict[str, Any]:
    teams = _teams_ok(teams, 3, 128)
    params = params or {}
    reset = bool(params.get("bracket_reset", False))
    results = params.get("results", [])
    if not isinstance(results, list):
        raise validation(["results"])
    return _elimination(teams, False, results, double=True, bracket_reset=reset)


def _blank_match(match_id: str, bracket: str, round_no: int, index: int) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "bracket": bracket,
        "round": round_no,
        "index": index,
        "home": None,
        "away": None,
        "home_seed": None,
        "away_seed": None,
        "bye": False,
        "winner": None,
        "loser": None,
        "winner_to": None,
        "loser_to": None,
        "conditional": False,
        "required": True,
    }


def _elimination(
    teams: list[str],
    third: bool,
    results: list[Any],
    double: bool,
    bracket_reset: bool,
) -> dict[str, Any]:
    n = len(teams)
    size = _next_power(n)
    order = _seed_order(size)
    rounds_n = int(math.log2(size))
    catalog: dict[str, dict[str, Any]] = {}

    winners_rounds: list[dict[str, Any]] = []
    prefix = "WR" if double else "R"
    bracket_name = "winners" if double else "main"
    for round_no in range(1, rounds_n + 1):
        match_count = size // (2 ** round_no)
        matches = []
        for index in range(1, match_count + 1):
            match_id = f"{prefix}{round_no}M{index}" if double else f"R{round_no}M{index}"
            match = _blank_match(match_id, bracket_name, round_no, index)
            if round_no < rounds_n:
                dest_index = (index + 1) // 2
                dest_id = (
                    f"{prefix}{round_no + 1}M{dest_index}" if double else f"R{round_no + 1}M{dest_index}"
                )
                slot = "home" if index % 2 == 1 else "away"
                match["winner_to"] = {"match_id": dest_id, "slot": slot}
            matches.append(match)
            catalog[match_id] = match
        winners_rounds.append({"round": round_no, "matches": matches})

    first = winners_rounds[0]["matches"]
    for index, match in enumerate(first):
        seed_a = order[2 * index]
        seed_b = order[2 * index + 1]
        _place_seed(match, seed_a, seed_b, teams)

    if double:
        _build_losers(catalog, winners_rounds, size, bracket_reset)
    elif third and rounds_n >= 2:
        third_match = _blank_match("TP", "third_place", rounds_n, 1)
        third_match["winner_to"] = None
        catalog["TP"] = third_match
        semis = winners_rounds[-2]["matches"]
        semis[0]["loser_to"] = {"match_id": "TP", "slot": "home"}
        if len(semis) > 1:
            semis[1]["loser_to"] = {"match_id": "TP", "slot": "away"}

    _propagate_byes(catalog)
    _apply_results(catalog, results)
    if double:
        _refresh_grand_final(catalog, bracket_reset)

    if double:
        losers = _rounds_from(catalog, "losers")
        winners = _rounds_from(catalog, "winners")
        grand = [catalog["GF1"]]
        if bracket_reset:
            grand.append(catalog["GF2"])
        return {
            "format": "double_elim",
            "bracket_size": size,
            "bracket_reset": bracket_reset,
            "winners": winners,
            "losers": losers,
            "grand_final": grand,
        }

    payload = {
        "format": "single_elim",
        "bracket_size": size,
        "rounds": _rounds_from(catalog, "main"),
    }
    if third and "TP" in catalog:
        payload["rounds"].append({"round": rounds_n, "matches": [catalog["TP"]]})
    return payload


def _place_seed(match: dict[str, Any], seed_a: int, seed_b: int, teams: list[str]) -> None:
    def team(seed: int) -> str | None:
        if seed > len(teams):
            return None
        return teams[seed - 1]

    team_a = team(seed_a)
    team_b = team(seed_b)
    # Lower seed number is home.
    if seed_a < seed_b:
        home, away = team_a, team_b
        home_seed, away_seed = seed_a, seed_b if team_b else None
    else:
        home, away = team_b, team_a
        home_seed, away_seed = seed_b if team_b else seed_a, seed_a if team_b else None
        if team_b is None:
            home, away = team_a, None
            home_seed, away_seed = seed_a, None
    match["home"] = home
    match["away"] = away
    match["home_seed"] = home_seed
    match["away_seed"] = away_seed
    if home is not None and away is None:
        match["bye"] = True
        match["winner"] = home
        match["loser"] = None
    elif away is not None and home is None:
        match["bye"] = True
        match["winner"] = away
        match["loser"] = None


def _build_losers(
    catalog: dict[str, dict[str, Any]],
    winners_rounds: list[dict[str, Any]],
    size: int,
    bracket_reset: bool,
) -> None:
    k = int(math.log2(size))
    losers_round_count = 2 * (k - 1)
    counts: dict[int, int] = {}
    for jump in range(1, k):
        match_count = size // (2 ** (jump + 1))
        counts[2 * jump - 1] = match_count
        counts[2 * jump] = match_count
    for round_no in range(1, losers_round_count + 1):
        for index in range(1, counts[round_no] + 1):
            match_id = f"LR{round_no}M{index}"
            match = _blank_match(match_id, "losers", round_no, index)
            if round_no < losers_round_count:
                if round_no % 2 == 1:
                    dest = f"LR{round_no + 1}M{index}"
                    match["winner_to"] = {"match_id": dest, "slot": "home"}
                else:
                    dest_index = (index + 1) // 2
                    dest = f"LR{round_no + 1}M{dest_index}"
                    slot = "home" if index % 2 == 1 else "away"
                    match["winner_to"] = {"match_id": dest, "slot": slot}
            catalog[match_id] = match

    # WR1 losers meet in LR1.
    first_winners = winners_rounds[0]["matches"]
    for index in range(0, len(first_winners), 2):
        left = first_winners[index]
        right = first_winners[index + 1]
        dest = catalog[f"LR1M{index // 2 + 1}"]
        left["loser_to"] = {"match_id": dest["match_id"], "slot": "home"}
        right["loser_to"] = {"match_id": dest["match_id"], "slot": "away"}

    # WRr (r >= 2) losers drop into LR(2r - 2), away slot when it is the final.
    for round_no in range(2, k + 1):
        drop_round = 2 * round_no - 2
        for match in winners_rounds[round_no - 1]["matches"]:
            dest = catalog[f"LR{drop_round}M{match['index']}"]
            slot = "away" if drop_round == losers_round_count else "away"
            # Major drop-ins take the away slot; LR winners already own home on odd-to-even hops.
            if drop_round % 2 == 0:
                slot = "away"
            match["loser_to"] = {"match_id": dest["match_id"], "slot": slot}

    gf1 = _blank_match("GF1", "grand_final", 1, 1)
    gf1["winner_to"] = None
    catalog["GF1"] = gf1
    winners_final = winners_rounds[-1]["matches"][0]
    losers_final = catalog[f"LR{losers_round_count}M1"]
    winners_final["winner_to"] = {"match_id": "GF1", "slot": "home"}
    # Spec: WR final loser drops to the losers-bracket final away slot.
    winners_final["loser_to"] = {"match_id": losers_final["match_id"], "slot": "away"}
    losers_final["winner_to"] = {"match_id": "GF1", "slot": "away"}
    losers_final["loser_to"] = None

    if bracket_reset:
        gf2 = _blank_match("GF2", "grand_final", 2, 1)
        gf2["conditional"] = True
        gf2["required"] = None
        gf2["winner_to"] = None
        catalog["GF2"] = gf2


def _propagate_byes(catalog: dict[str, dict[str, Any]]) -> None:
    changed = True
    while changed:
        changed = False
        for match in list(catalog.values()):
            if match["winner"] is not None and match["winner_to"] is not None:
                if _write_slot(catalog, match["winner_to"], match["winner"]):
                    changed = True
            if match["loser"] is not None and match["loser_to"] is not None:
                if _write_slot(catalog, match["loser_to"], match["loser"]):
                    changed = True
            if match["bye"] and match["winner"] is not None and match["loser_to"] is not None:
                # A winners-bracket bye produces no loser. Mark the fed slot only when its
                # sibling arrives; the missing side stays empty and the match can become a bye.
                pass
            home, away = match["home"], match["away"]
            if match["winner"] is None and match["bye"] is False:
                feeder_missing = _slot_missing(catalog, match)
                if feeder_missing and ((home is None) ^ (away is None)):
                    present = home or away
                    match["bye"] = True
                    match["winner"] = present
                    match["loser"] = None
                    changed = True


def _slot_missing(catalog: dict[str, dict[str, Any]], match: dict[str, Any]) -> bool:
    """True when one incoming slot can never receive a team (bye source)."""
    sources = {"home": False, "away": False}
    for other in catalog.values():
        for key in ("winner_to", "loser_to"):
            dest = other.get(key)
            if dest and dest["match_id"] == match["match_id"]:
                sources[dest["slot"]] = True
    if match["bracket"] == "main" or (match["bracket"] == "winners" and match["round"] == 1):
        return False
    # Initial WR1 matches have no sources.
    if match["round"] == 1 and match["bracket"] in ("main", "winners"):
        return False
    return sources["home"] != sources["away"] or (not sources["home"] and not sources["away"])


def _write_slot(catalog: dict[str, dict[str, Any]], dest: dict[str, str], team: str) -> bool:
    match = catalog[dest["match_id"]]
    slot = dest["slot"]
    if match[slot] == team:
        return False
    if match[slot] is None:
        match[slot] = team
        return True
    return False


def _apply_results(catalog: dict[str, dict[str, Any]], results: list[Any]) -> None:
    pending = []
    for item in results:
        if not isinstance(item, dict):
            raise validation(["results"])
        pending.append(item)
    guard = 0
    while pending and guard < len(catalog) + len(results) + 2:
        guard += 1
        progress = False
        rest = []
        for item in pending:
            match = catalog.get(item.get("match_id"))
            if match is None:
                raise validation(["results"], "Unknown match_id.")
            winner = item.get("winner")
            participants = [side for side in (match["home"], match["away"]) if side]
            if winner not in participants:
                if len(participants) < (1 if match["bye"] else 2):
                    rest.append(item)
                    continue
                raise validation(["results"], "Winner is not a participant of that match.")
            if match["winner"] != winner:
                match["winner"] = winner
                match["loser"] = next(side for side in participants if side != winner) if len(participants) == 2 else None
                progress = True
            _push_outcome(catalog, match)
        if not progress and rest:
            raise validation(["results"], "Winner is not a participant of that match.")
        pending = rest
        if not progress:
            break
    _propagate_byes(catalog)


def _push_outcome(catalog: dict[str, dict[str, Any]], match: dict[str, Any]) -> None:
    if match["winner"] is not None and match["winner_to"] is not None:
        _write_slot(catalog, match["winner_to"], match["winner"])
    if match["loser"] is not None and match["loser_to"] is not None:
        _write_slot(catalog, match["loser_to"], match["loser"])


def _refresh_grand_final(catalog: dict[str, dict[str, Any]], bracket_reset: bool) -> None:
    gf1 = catalog["GF1"]
    if "GF2" not in catalog:
        return
    gf2 = catalog["GF2"]
    if gf1["home"] is not None:
        gf2["home"] = gf1["home"]
    if gf1["away"] is not None:
        gf2["away"] = gf1["away"]
    if gf1["winner"] is None:
        gf2["required"] = None
        return
    gf2["required"] = bool(bracket_reset and gf1["winner"] == gf1["away"])


def _rounds_from(catalog: dict[str, dict[str, Any]], bracket: str) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for match in catalog.values():
        if match["bracket"] != bracket:
            continue
        grouped.setdefault(match["round"], []).append(match)
    rounds = []
    for round_no in sorted(grouped):
        matches = sorted(grouped[round_no], key=lambda item: item["index"])
        rounds.append({"round": round_no, "matches": matches})
    return rounds


def generate(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise validation(["format"])
    fmt = body.get("format")
    teams = body.get("teams")
    params = body.get("params", {})
    if not isinstance(params, dict):
        raise validation(["params"])
    if fmt == "round_robin":
        return generate_round_robin(teams, params)
    if fmt == "swiss":
        return generate_swiss(teams, params)
    if fmt == "single_elim":
        return generate_single_elim(teams, params)
    if fmt == "double_elim":
        return generate_double_elim(teams, params)
    raise validation(["format"])
