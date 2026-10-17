import hashlib
import os

from app.core.canonical import canonical, sign
from app.core.security import hash_password
from app.models import (
    Club,
    Discount,
    Event,
    League,
    LeagueTeam,
    Match,
    Meta,
    Player,
    Product,
    Sport,
    Squad,
    Tenant,
    User,
    Variant,
    Venue,
)

TIE = ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"]
PASSWORD = hash_password("Nascon@26")


def baseline_sports() -> list[dict]:
    return [
        {
            "sport_id": "football",
            "name": "Football",
            "scoring_model": "event",
            "periods": {"count": 2, "duration_minutes": 45},
            "scoring_events": {"goal": {"points": 1}},
            "draw_allowed": True,
            "overtime": {"policy": "none"},
            "tie_break_order": list(TIE),
            "roster": {"min_squad_size": 11, "max_squad_size": 25, "required_positions": {"gk": 1}},
        },
        {
            "sport_id": "basketball",
            "name": "Basketball",
            "scoring_model": "event",
            "periods": {"count": 4, "duration_minutes": 12},
            "scoring_events": {
                "free_throw": {"points": 1},
                "two_pointer": {"points": 2},
                "three_pointer": {"points": 3},
            },
            "draw_allowed": False,
            "overtime": {"policy": "extra_time", "duration_minutes": 5},
            "tie_break_order": list(TIE),
            "roster": {"min_squad_size": 5, "max_squad_size": 15, "required_positions": {}},
        },
        {
            "sport_id": "cricket",
            "name": "Cricket",
            "scoring_model": "innings",
            "periods": {
                "innings_per_team": 1,
                "overs_per_innings": 20,
                "balls_per_over": 6,
                "wickets_per_innings": 10,
            },
            "scoring_events": {
                "dot_ball": {"runs": 0, "legal_ball": True, "wicket": False},
                "single": {"runs": 1, "legal_ball": True, "wicket": False},
                "two": {"runs": 2, "legal_ball": True, "wicket": False},
                "four": {"runs": 4, "legal_ball": True, "wicket": False},
                "six": {"runs": 6, "legal_ball": True, "wicket": False},
                "wide": {"runs": 1, "legal_ball": False, "wicket": False},
                "no_ball": {"runs": 1, "legal_ball": False, "wicket": False},
                "wicket": {"runs": 0, "legal_ball": True, "wicket": True},
            },
            "draw_allowed": True,
            "overtime": {"policy": "super_over"},
            "tie_break_order": list(TIE),
            "roster": {"min_squad_size": 11, "max_squad_size": 18, "required_positions": {"wicketkeeper": 1}},
        },
        {
            "sport_id": "esports",
            "name": "Esports",
            "scoring_model": "round",
            "periods": {"best_of": 3},
            "scoring_events": {"round_win": {"rounds": 1}},
            "draw_allowed": False,
            "overtime": {"policy": "none"},
            "tie_break_order": list(TIE),
            "roster": {"min_squad_size": 5, "max_squad_size": 8, "required_positions": {}},
        },
    ]


def _demo_event(db, event_id: str, match_id: str, event_type: str, payload: dict, sequence: int) -> None:
    signature = sign(os.environ.get("DAY1_SALT", ""), event_id, payload)
    body = {"event_id": event_id, "match_id": match_id, "type": event_type, "payload": payload}
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
            body_hash=hashlib.sha256(canonical(body).encode("utf-8")).hexdigest(),
            response=response,
        )
    )


def seed(db, seed_value: int = 42, stock: dict | None = None) -> dict:
    levels = {"var-jersey-001-M": 1, "var-jersey-001-L": 5}
    if stock:
        for key, value in stock.items():
            levels[key] = value
    for sport in baseline_sports():
        db.add(Sport(sport_id=sport["sport_id"], config=sport))
    db.add(Tenant(tenant_id="tenant-alpha", name="Alpha Sports", logo="alpha", primary_color="#0B3D2E", secondary_color="#F5C518"))
    db.add(Tenant(tenant_id="tenant-beta", name="Beta Athletics", logo="beta", primary_color="#1D4E89", secondary_color="#F0F4F8"))
    clubs = [
        ("club-alpha-1", "tenant-alpha", "Alpha FC"),
        ("club-alpha-2", "tenant-alpha", "Alpha United"),
        ("club-beta-1", "tenant-beta", "Beta FC"),
    ]
    for club_id, tenant_id, name in clubs:
        db.add(
            Club(
                club_id=club_id,
                tenant_id=tenant_id,
                name=name,
                venue_name=f"{name} Ground",
                latitude=33.6844,
                longitude=73.0479,
                socials={"instagram": name.replace(" ", "").lower()},
                staff={"coach": "Coach", "manager": "Manager", "physio": "Physio"},
            )
        )
    for league_id, tenant_id in (
        ("league-alpha-1", "tenant-alpha"),
        ("league-alpha-2", "tenant-alpha"),
        ("league-beta-1", "tenant-beta"),
    ):
        db.add(League(league_id=league_id, tenant_id=tenant_id, sport_id="football", standings_ready=False))
    db.add(Player(player_id="player-alpha-1", name="Alpha One", position="gk", club_id="club-alpha-1", league_id="league-alpha-1"))
    db.add(Player(player_id="player-alpha-2", name="Alpha Two", position="df", club_id="club-alpha-2", league_id="league-alpha-1"))
    db.add(Player(player_id="player-beta-1", name="Beta One", position="gk", club_id="club-beta-1", league_id="league-beta-1"))
    accounts = [
        ("admin@nascon.test", "super_admin", "Platform Admin", None, None, None, None),
        ("organizer@nascon.test", "league_organizer", "League Organizer", "tenant-alpha", "league-alpha-1", None, None),
        ("manager@nascon.test", "club_manager", "Club Manager", "tenant-alpha", None, "club-alpha-1", None),
        ("player@nascon.test", "player", "Player", "tenant-alpha", None, "club-alpha-1", "player-alpha-1"),
        ("viewer@nascon.test", "viewer", "Viewer", "tenant-alpha", None, None, None),
    ]
    for email, role, name, tenant_id, league_id, club_id, player_id in accounts:
        db.add(
            User(
                email=email,
                password_hash=PASSWORD,
                role=role,
                name=name,
                tenant_id=tenant_id,
                league_id=league_id,
                club_id=club_id,
                player_id=player_id,
            )
        )
    db.add(Product(product_id="prod-jersey-001", tenant_id="tenant-alpha", name="Alpha Jersey", category="apparel", description="Home jersey"))
    db.add(Product(product_id="prod-cap-001", tenant_id="tenant-alpha", name="Alpha Cap", category="accessories", description="Cap"))
    for variant_id, size, price in (("var-jersey-001-M", "M", 450000), ("var-jersey-001-L", "L", 450000)):
        db.add(
            Variant(
                variant_id=variant_id,
                product_id="prod-jersey-001",
                size=size,
                color="green",
                price_minor=price,
                currency="PKR",
                stock=int(levels.get(variant_id, 0)),
                reserved=0,
            )
        )
    db.add(Variant(variant_id="var-cap-001", product_id="prod-cap-001", size="OS", color="green", price_minor=150000, currency="PKR", stock=20, reserved=0))
    db.add(Discount(code="WELCOME10", percent=10))
    db.add(
        Venue(
            venue_id="venue-alpha",
            name="Alpha Ground",
            latitude=33.6844,
            longitude=73.0479,
            windows=[{"open": "08:00", "close": "22:00"}],
        )
    )
    db.add(
        Squad(squad_id="squad-alpha-1", league_id="demo-roster", club_id="club-alpha-1", tenant_id="tenant-alpha", name="Alpha First")
    )
    db.add(
        Squad(squad_id="squad-alpha-2", league_id="demo-roster", club_id="club-alpha-1", tenant_id="tenant-alpha", name="Alpha Reserves")
    )
    db.add(
        League(
            league_id="demo-roster",
            tenant_id="tenant-alpha",
            sport_id="football",
            transfer_open="2026-01-01",
            transfer_close="2026-12-31",
            max_squad_size=12,
            required_positions={"gk": 1},
            standings_ready=False,
        )
    )
    db.add(Player(player_id="demo-gk", name="Demo Keeper", position="gk", league_id="demo-roster", squad_id="squad-alpha-1", club_id="club-alpha-1", role="captain"))
    db.add(Player(player_id="demo-df", name="Demo Back", position="df", league_id="demo-roster", squad_id="squad-alpha-1", club_id="club-alpha-1", role="starter"))
    db.add(Player(player_id="demo-mf", name="Demo Mid", position="mf", league_id="demo-roster", squad_id="squad-alpha-2", club_id="club-alpha-1", role="substitute"))
    db.add(
        League(
            league_id="demo-league",
            tenant_id="tenant-alpha",
            sport_id="football",
            points={"win": 3, "draw": 1, "loss": 0},
            tie_break=list(TIE),
            qualification_spots=2,
            lots_seed=seed_value,
            standings_ready=True,
            team_order=["demo-a", "demo-b", "demo-c", "demo-d"],
        )
    )
    for team_id in ("demo-a", "demo-b", "demo-c", "demo-d"):
        db.add(LeagueTeam(league_id="demo-league", team_id=team_id))
    db.add(Match(match_id="demo-live", league_id="demo-league", sport_id="football", home_team_id="demo-a", away_team_id="demo-b", venue_id="venue-alpha", starts_at="2026-10-17T14:00:00Z"))
    db.add(Match(match_id="demo-next", league_id="demo-league", sport_id="football", home_team_id="demo-c", away_team_id="demo-d", venue_id="venue-alpha", starts_at="2026-10-18T14:00:00Z"))
    db.add(Match(match_id="demo-done", league_id="demo-league", sport_id="football", home_team_id="demo-a", away_team_id="demo-c", venue_id="venue-alpha", starts_at="2026-10-16T14:00:00Z"))
    _demo_event(db, "demo-live-start", "demo-live", "match_start", {}, 1)
    _demo_event(db, "demo-done-start", "demo-done", "match_start", {}, 1)
    _demo_event(db, "demo-done-goal", "demo-done", "score", {"team_id": "demo-a", "event_type": "goal"}, 2)
    _demo_event(db, "demo-done-end", "demo-done", "match_end", {}, 3)
    db.add(Meta(id=1, seed=seed_value, seeded=True))
    db.flush()
    return {
        "status": "reset_complete",
        "seed": seed_value,
        "sports": ["football", "basketball", "cricket", "esports"],
        "tenants": ["tenant-alpha", "tenant-beta"],
        "stock": {"var-jersey-001-M": int(levels["var-jersey-001-M"]), "var-jersey-001-L": int(levels["var-jersey-001-L"])},
    }
