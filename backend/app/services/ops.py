"""Database-backed use cases for the audit contract and the mobile API."""

from __future__ import annotations

import base64
import hashlib
import io
import os
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.canonical import canonical, sign
from app.core.errors import ApiError, validation
from app.core.security import issue_token, verify_password
from app.domain import fixtures as fixture_domain
from app.domain import roster as roster_domain
from app.domain.scoring import project, validate_new_event
from app.domain.sports import is_int, parse_sport
from app.domain.standings import standings as build_standings
from app.models import (
    ClientRequest,
    Club,
    CostreamSave,
    DlqItem,
    FollowEdge,
    GuestToken,
    MemberCart,
    QrLink,
    QrNotice,
    RevokedStreamKey,
    StreamKeyRow,
    Discount,
    DomainEvent,
    Event,
    League,
    LeagueTeam,
    LiveRosterMatch,
    Match,
    Order,
    OrderItem,
    Player,
    Product,
    RosterAudit,
    Sport,
    Squad,
    SyncAction,
    Tenant,
    User,
    Variant,
    Venue,
)
from app.seed import seed as seed_baseline

IDENT = __import__("re").compile(r"^[A-Za-z0-9_.:-]{1,64}$")
SALT = lambda: os.environ.get("DAY1_SALT", "")


def ident(value, field: str) -> str:
    if not isinstance(value, str) or not IDENT.match(value):
        raise validation([field])
    return value


def stamp(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(payload) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def emit(db: Session, name: str, payload: dict) -> None:
    db.add(DomainEvent(name=name, payload=payload))
    url = os.environ.get("REDIS_URL")
    if not url:
        return
    try:
        import redis

        redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1).xadd(
            "domain-events", {"name": name, "payload": canonical(payload)}, maxlen=1000
        )
    except Exception:
        return


def wipe(db: Session) -> None:
    for model in (
        OrderItem,
        Order,
        DlqItem,
        FollowEdge,
        CostreamSave,
        GuestToken,
        MemberCart,
        RevokedStreamKey,
        StreamKeyRow,
        QrNotice,
        QrLink,
        SyncAction,
        ClientRequest,
        DomainEvent,
        Event,
        Match,
        LeagueTeam,
        LiveRosterMatch,
        RosterAudit,
        Player,
        Squad,
        Variant,
        Product,
        Discount,
        Venue,
        Club,
        User,
        League,
        Sport,
        Tenant,
    ):
        db.query(model).delete()
    from app.models import Meta, ScheduledMatch

    db.query(ScheduledMatch).delete()
    db.query(Meta).delete()
    db.flush()


def do_reset(db: Session, body: dict | None) -> dict:
    body = body or {}
    fields = []
    seed_value = body.get("seed", 42) if isinstance(body, dict) else 42
    if "seed" in body and not is_int(body.get("seed")):
        fields.append("seed")
    stock = body.get("stock", {}) if isinstance(body, dict) else {}
    if "stock" in body and not isinstance(stock, dict):
        fields.append("stock")
    elif isinstance(stock, dict):
        for key, value in stock.items():
            if not isinstance(key, str) or not is_int(value) or value < 0:
                fields.append("stock")
                break
    if fields:
        raise validation(fields)
    from app.services.day2 import reset_counters

    reset_counters()
    wipe(db)
    return seed_baseline(db, seed_value if is_int(seed_value) else 42, stock if isinstance(stock, dict) else None)


def create_sport(db: Session, body: dict) -> tuple[int, dict]:
    config = parse_sport(body)
    if db.get(Sport, config["sport_id"]) is not None:
        raise ApiError(409, "SPORT_EXISTS", "Sport already exists.")
    db.add(Sport(sport_id=config["sport_id"], config=config))
    db.flush()
    return 201, {"sport_id": config["sport_id"], "status": "created", "config": config}


def create_league(db: Session, body: dict) -> tuple[int, dict]:
    fields = []
    for key in ("league_id", "sport_id", "teams", "fixtures"):
        if key not in body:
            fields.append(key)
    if fields:
        raise validation(fields)
    league_id = ident(body["league_id"], "league_id")
    sport_id = ident(body["sport_id"], "sport_id")
    sport = db.get(Sport, sport_id)
    if sport is None:
        raise ApiError(404, "SPORT_NOT_FOUND", "Sport does not exist.")
    if db.get(League, league_id) is not None and db.get(League, league_id).standings_ready:
        raise ApiError(409, "LEAGUE_EXISTS", "League already exists.")
    teams = body["teams"]
    if not isinstance(teams, list) or not (2 <= len(teams) <= 40) or len(set(teams)) != len(teams):
        raise validation(["teams"])
    for team in teams:
        ident(team, "teams")
    fixtures = body["fixtures"]
    if not isinstance(fixtures, list):
        raise validation(["fixtures"])
    points = body.get("points", {"win": 3, "draw": 1, "loss": 0})
    if not isinstance(points, dict) or any(not is_int(points.get(key, 0)) for key in ("win", "draw", "loss")):
        raise validation(["points"])
    points = {"win": points.get("win", 3), "draw": points.get("draw", 1), "loss": points.get("loss", 0)}
    order = body.get("tie_break_order", sport.config["tie_break_order"])
    allowed = {"h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"}
    if not isinstance(order, list) or any(item not in allowed for item in order) or len(order) != len(set(order)):
        raise validation(["tie_break_order"])
    order = [item for item in order if item != "draw_lots"] + ["draw_lots"]
    qualification = body.get("qualification")
    if not isinstance(qualification, dict) or not is_int(qualification.get("spots")):
        raise validation(["qualification"])
    spots = qualification["spots"]
    if spots < 1 or spots > len(teams):
        raise validation(["qualification"])
    from app.models import Meta

    meta = db.get(Meta, 1)
    lots = body.get("lots_seed", meta.seed if meta else 42)
    if not is_int(lots):
        raise validation(["lots_seed"])
    seen = set()
    clean = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise validation(["fixtures"])
        match_id = ident(fixture.get("match_id"), "match_id")
        home = ident(fixture.get("home"), "home")
        away = ident(fixture.get("away"), "away")
        if home == away or home not in teams or away not in teams:
            raise validation(["fixtures"])
        if match_id in seen or db.get(Match, match_id) is not None:
            raise ApiError(409, "MATCH_EXISTS", "Match id already used.")
        seen.add(match_id)
        clean.append((match_id, home, away))
    row = db.get(League, league_id)
    if row is None:
        row = League(league_id=league_id)
        db.add(row)
    row.sport_id = sport_id
    row.points = points
    row.tie_break = order
    row.qualification_spots = spots
    row.lots_seed = lots
    row.standings_ready = True
    row.team_order = list(teams)
    for team in teams:
        db.add(LeagueTeam(league_id=league_id, team_id=team))
    for match_id, home, away in clean:
        db.add(Match(match_id=match_id, league_id=league_id, sport_id=sport_id, home_team_id=home, away_team_id=away))
    db.flush()
    return 201, {"league_id": league_id, "status": "created", "team_count": len(teams), "fixture_count": len(clean)}


def _events(db: Session, match_id: str) -> list[Event]:
    return list(db.scalars(select(Event).where(Event.match_id == match_id).order_by(Event.sequence)))


def _event_dicts(rows: list[Event]) -> list[dict]:
    return [{"event_id": row.event_id, "type": row.type, "payload": row.payload, "sequence": row.sequence} for row in rows]


def append_event(db: Session, body: dict) -> tuple[int, dict]:
    fields = []
    for key in ("event_id", "match_id", "type", "payload"):
        if key not in body:
            fields.append(key)
    if fields:
        raise validation(fields)
    event_id = ident(body["event_id"], "event_id")
    match_id = ident(body["match_id"], "match_id")
    event_type = body["type"]
    payload = body["payload"]
    if not isinstance(event_type, str) or not isinstance(payload, dict):
        raise validation(["type"] if not isinstance(event_type, str) else ["payload"])
    body_key = digest({"event_id": event_id, "match_id": match_id, "type": event_type, "payload": payload})
    existing = db.get(Event, event_id)
    if existing is not None:
        if existing.body_hash == body_key:
            return 200, existing.response
        raise ApiError(409, "DUPLICATE_EVENT_ID", "event_id was already used with a different body.")
    if event_type == "match_init":
        return _match_init(db, event_id, match_id, payload, body_key)
    match = db.get(Match, match_id)
    if match is None:
        raise ApiError(404, "MATCH_NOT_FOUND", "Match stream does not exist.")
    sport = db.get(Sport, match.sport_id)
    rows = _events(db, match_id)
    if event_type == "compensation":
        _check_compensation(db, match_id, payload)
    validate_new_event(sport.config, _event_dicts(rows), match.home_team_id, match.away_team_id, event_type, payload)
    sequence = len(rows) + 1
    signature = sign(SALT(), event_id, payload)
    response = {
        "event_id": event_id,
        "match_id": match_id,
        "type": event_type,
        "sequence": sequence,
        "match_version": sequence,
        "signature": signature,
    }
    db.add(
        Event(
            event_id=event_id,
            match_id=match_id,
            type=event_type,
            payload=payload,
            sequence=sequence,
            signature=signature,
            body_hash=body_key,
            response=response,
        )
    )
    db.flush()
    emit(db, "match.event", response)
    from app.services.day2 import clear_qr_cache

    clear_qr_cache()
    return 201, response


def _match_init(db, event_id, match_id, payload, body_key) -> tuple[int, dict]:
    if db.get(Match, match_id) is not None:
        raise ApiError(409, "MATCH_EXISTS", "Match stream already exists.")
    for key in ("sport_id", "home_team_id", "away_team_id"):
        if key not in payload:
            raise validation([key])
    sport_id = ident(payload["sport_id"], "sport_id")
    home = ident(payload["home_team_id"], "home_team_id")
    away = ident(payload["away_team_id"], "away_team_id")
    if home == away:
        raise validation(["away_team_id"])
    if db.get(Sport, sport_id) is None:
        raise ApiError(404, "SPORT_NOT_FOUND", "Sport does not exist.")
    league_id = payload.get("league_id")
    if league_id is not None:
        league_id = ident(league_id, "league_id")
    stored = {"sport_id": sport_id, "home_team_id": home, "away_team_id": away}
    if league_id is not None:
        stored["league_id"] = league_id
    signature = sign(SALT(), event_id, stored)
    response = {
        "event_id": event_id,
        "match_id": match_id,
        "type": "match_init",
        "sequence": 1,
        "match_version": 1,
        "signature": signature,
    }
    db.add(Match(match_id=match_id, league_id=league_id, sport_id=sport_id, home_team_id=home, away_team_id=away))
    db.add(
        Event(
            event_id=event_id,
            match_id=match_id,
            type="match_init",
            payload=stored,
            sequence=1,
            signature=signature,
            body_hash=body_key,
            response=response,
        )
    )
    db.flush()
    return 201, response


def _check_compensation(db, match_id, payload) -> None:
    target_id = payload.get("compensates")
    if not isinstance(target_id, str) or "reason" not in payload:
        raise validation(["compensates"] if not isinstance(target_id, str) else ["reason"])
    target = db.get(Event, target_id)
    if target is None:
        raise ApiError(404, "EVENT_NOT_FOUND", "Compensated event does not exist.")
    if target.match_id != match_id or target.type not in ("score", "discipline", "tiebreak_winner"):
        raise ApiError(409, "INVALID_COMPENSATION", "That event cannot be compensated.")
    rows = _events(db, match_id)
    for row in rows:
        if row.type == "compensation" and row.payload.get("compensates") == target_id:
            raise ApiError(409, "INVALID_COMPENSATION", "Event is already compensated.")


def score_view(db: Session, match_id: str) -> dict:
    match = db.get(Match, match_id)
    if match is None:
        raise ApiError(404, "MATCH_NOT_FOUND", "Match does not exist.")
    sport = db.get(Sport, match.sport_id)
    rows = _events(db, match_id)
    # League fixtures exist before match_init. Projection still runs on an empty log.
    projected = project(sport.config, _event_dicts(rows), match.home_team_id, match.away_team_id)
    compensated_by = projected["compensated_by"]
    public_events = []
    for row in rows:
        public_events.append(
            {
                "sequence": row.sequence,
                "event_id": row.event_id,
                "type": row.type,
                "payload": row.payload,
                "signature": row.signature,
                "compensated": row.event_id in compensated_by,
                "compensated_by": compensated_by.get(row.event_id),
            }
        )
    return {
        "match_id": match_id,
        "sport_id": match.sport_id,
        "league_id": match.league_id,
        "status": projected["status"],
        "outcome": projected["outcome"],
        "winner_team_id": projected["winner_team_id"],
        "tiebreak_winner_team_id": projected["tiebreak_winner_team_id"],
        "home": projected["home"],
        "away": projected["away"],
        "version": len(rows),
        "events": public_events,
    }


def standings_view(db: Session, league_id: str) -> dict:
    league = db.get(League, league_id)
    if league is None or not league.standings_ready:
        raise ApiError(404, "LEAGUE_NOT_FOUND", "League does not exist.")
    sport = db.get(Sport, league.sport_id).config
    teams = list(league.team_order or [])
    matches = []
    for match in db.scalars(select(Match).where(Match.league_id == league_id)):
        view = score_view(db, match.match_id)
        matches.append(
            {
                "home": match.home_team_id,
                "away": match.away_team_id,
                "status": view["status"],
                "outcome": view["outcome"],
                "home_score": view["home"]["score"],
                "away_score": view["away"]["score"],
                "home_fair_play": view["home"]["fair_play"],
                "away_fair_play": view["away"]["fair_play"],
                "home_balls": view["home"]["balls"] or 0,
                "away_balls": view["away"]["balls"] or 0,
                "home_wickets": view["home"]["wickets"] or 0,
                "away_wickets": view["away"]["wickets"] or 0,
            }
        )
    table = build_standings(
        sport,
        teams,
        matches,
        league.points,
        league.tie_break,
        league.qualification_spots,
        league.lots_seed,
        league_id,
    )
    return {
        "league_id": league_id,
        "sport_id": league.sport_id,
        "qualification_spots": league.qualification_spots,
        "table": table["table"],
        "h2h": table["h2h"],
    }
