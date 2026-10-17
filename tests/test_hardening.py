import os
from time import time

import jwt

os.environ.setdefault("DAY1_SALT", "demo-salt")
os.environ.setdefault("DATABASE_URL", "sqlite:///./.pytest.db")

from fastapi.testclient import TestClient

from app.main import HITS, app

client = TestClient(app)


def _reset():
    HITS.clear()
    response = client.post("/audit/v1/reset", json={"seed": 42, "stock": {"var-jersey-001-M": 5, "var-jersey-001-L": 5}})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(email="manager@nascon.test"):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "Nascon@26"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_rbac_matrix_matches_the_contract():
    _reset()
    matrix = client.post("/audit/v1/rbac-matrix").json()
    tokens = matrix["tokens"]
    context = matrix["context"]
    roles = ["super_admin", "league_organizer", "club_manager", "player", "viewer"]

    def allow(role, method, path):
        response = client.request(method, path, headers={"Authorization": f"Bearer {tokens[role]}"}, json={})
        return response.status_code

    probes = []
    for role in roles:
        probes.append(("P1", role, allow(role, "POST", "/audit/v1/rbac/tenants")))
        probes.append(("P2-own", role, allow(role, "POST", f"/audit/v1/rbac/leagues/{context['own_league_id']}/fixtures")))
        probes.append(("P2-other", role, allow(role, "POST", f"/audit/v1/rbac/leagues/{context['other_league_id']}/fixtures")))
        probes.append(("P2-foreign", role, allow(role, "POST", f"/audit/v1/rbac/leagues/{context['foreign_league_id']}/fixtures")))
        probes.append(("P3-own", role, allow(role, "POST", f"/audit/v1/rbac/clubs/{context['own_club_id']}/roster")))
        probes.append(("P3-other", role, allow(role, "POST", f"/audit/v1/rbac/clubs/{context['other_club_id']}/roster")))
        probes.append(("P3-foreign", role, allow(role, "POST", f"/audit/v1/rbac/clubs/{context['foreign_club_id']}/roster")))
        probes.append(("P5-own", role, allow(role, "PATCH", f"/audit/v1/rbac/players/{context['own_player_id']}")))
        probes.append(("P5-other", role, allow(role, "PATCH", f"/audit/v1/rbac/players/{context['other_player_id']}")))
        probes.append(("P5-foreign", role, allow(role, "PATCH", f"/audit/v1/rbac/players/{context['foreign_player_id']}")))
        probes.append(("P6-own", role, allow(role, "GET", f"/audit/v1/rbac/clubs/{context['own_club_id']}")))
        probes.append(("P6-other", role, allow(role, "GET", f"/audit/v1/rbac/clubs/{context['other_club_id']}")))
        probes.append(("P6-foreign", role, allow(role, "GET", f"/audit/v1/rbac/clubs/{context['foreign_club_id']}")))
        probes.append(("P7", role, allow(role, "GET", "/audit/v1/rbac/platform/config")))

    expected = {
        ("P1", "super_admin"): 200,
        ("P2-own", "super_admin"): 200,
        ("P2-own", "league_organizer"): 200,
        ("P3-own", "super_admin"): 200,
        ("P3-own", "club_manager"): 200,
        ("P5-own", "super_admin"): 200,
        ("P5-own", "club_manager"): 200,
        ("P5-own", "player"): 200,
        ("P6-own", "super_admin"): 200,
        ("P6-own", "league_organizer"): 200,
        ("P6-own", "club_manager"): 200,
        ("P6-own", "player"): 200,
        ("P6-own", "viewer"): 200,
        ("P6-other", "super_admin"): 200,
        ("P6-other", "league_organizer"): 200,
        ("P6-other", "club_manager"): 200,
        ("P6-other", "player"): 200,
        ("P6-other", "viewer"): 200,
        ("P7", "super_admin"): 200,
        ("P2-own", "super_admin"): 200,
        ("P2-other", "super_admin"): 200,
        ("P2-foreign", "super_admin"): 200,
        ("P3-other", "super_admin"): 200,
        ("P3-foreign", "super_admin"): 200,
        ("P5-other", "super_admin"): 200,
        ("P5-foreign", "super_admin"): 200,
        ("P6-foreign", "super_admin"): 200,
    }
    for name, role, status in probes:
        want = expected.get((name, role), 403)
        assert status == want, f"{role} {name} returned {status}, wanted {want}"

    missing_club = client.get("/audit/v1/rbac/clubs/club-missing", headers={"Authorization": f"Bearer {tokens['super_admin']}"})
    assert missing_club.status_code == 404
    missing_league = client.post("/audit/v1/rbac/leagues/league-missing/fixtures", headers={"Authorization": f"Bearer {tokens['super_admin']}"}, json={})
    assert missing_league.status_code == 404
    assert missing_league.json()["error"]["code"] == "LEAGUE_NOT_FOUND"
    foreign = client.get(f"/audit/v1/rbac/clubs/{context['foreign_club_id']}", headers={"Authorization": f"Bearer {tokens['viewer']}"})
    assert foreign.status_code == 403


def test_expired_and_tampered_tokens_are_401():
    _reset()
    token = client.post("/audit/v1/rbac-matrix").json()["tokens"]["super_admin"]
    expired = jwt.encode({"role": "super_admin", "exp": int(time()) - 10}, "nascon-jwt-dev", algorithm="HS256")
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    for bad in (expired, tampered, ""):
        response = client.get("/audit/v1/rbac/platform/config", headers={"Authorization": f"Bearer {bad}"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_rate_limit_recovers_after_the_window():
    _reset()
    statuses = [client.get("/api/v1/ping").status_code for _ in range(101)]
    assert statuses[0] == 200
    blocked = client.get("/api/v1/ping")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert blocked.headers["retry-after"]
    bucket = next(iter(HITS.values()))
    for _ in range(len(bucket)):
        bucket[0] = time() - 11
        bucket.rotate(-1)
    assert client.get("/api/v1/ping").status_code == 200


def test_mobile_reads_orders_and_schedule():
    _reset()
    headers = _auth()
    fixtures = client.get("/api/v1/fixtures", headers=headers)
    assert fixtures.status_code == 200, fixtures.text
    assert any(row["match_id"] == "demo-live" for row in fixtures.json()["fixtures"])
    club = client.get("/api/v1/clubs/club-alpha-1", headers=headers)
    assert club.status_code == 200, club.text
    assert club.json()["tenant"]["tenant_id"] == "tenant-alpha"
    missing = client.get("/api/v1/clubs/club-missing", headers=headers)
    assert missing.status_code == 404
    products = client.get("/api/v1/products", headers=headers)
    assert products.status_code == 200
    assert "WELCOME10" in products.json()["discount_codes"]
    bought = client.post(
        "/audit/v1/checkout",
        json={
            "order_id": "ord-flow",
            "customer_id": "cust-001",
            "items": [{"variant_id": "var-jersey-001-L", "quantity": 1}],
            "discount_code": None,
            "payment": {"token": "tok_success"},
        },
    )
    assert bought.status_code == 201, bought.text
    before = client.get("/audit/v1/inventory/var-jersey-001-L").json()["stock"]
    moved = client.post("/api/v1/orders/ord-flow/transition", headers=headers, json={"status": "Refunded"})
    assert moved.status_code == 200, moved.text
    assert client.get("/audit/v1/inventory/var-jersey-001-L").json()["stock"] == before + 1
    illegal = client.post("/api/v1/orders/ord-flow/transition", headers=headers, json={"status": "Packed"})
    assert illegal.status_code == 409
    shaped = client.post("/api/v1/leagues/league-alpha-1/structure", headers=headers, json={"format": "multi_division", "divisions": ["A", "B"], "promotion_slots": 1})
    assert shaped.status_code == 200, shaped.text
    moved_match = client.post(
        "/api/v1/matches/demo-next/reschedule",
        headers=headers,
        json={"venue_id": "venue-alpha", "starts_at": "2026-10-19T10:00:00Z"},
    )
    assert moved_match.status_code == 200, moved_match.text
    clash = client.post(
        "/api/v1/matches/demo-live/reschedule",
        headers=headers,
        json={"venue_id": "venue-alpha", "starts_at": "2026-10-19T10:00:00Z"},
    )
    assert clash.status_code == 409
    assert clash.json()["error"]["details"]["conflicts"]
    pinned = client.post("/api/v1/matches/demo-live/pin", headers=headers, json={"variant_id": "var-jersey-001-M"})
    assert pinned.status_code == 200, pinned.text
    detail = client.get("/api/v1/matches/demo-live", headers=headers)
    assert detail.json()["pinned_variant_id"] == "var-jersey-001-M"


def test_store_and_schedule_reject_bad_input():
    _reset()
    headers = _auth("admin@nascon.test")
    bad_pay = client.post(
        "/audit/v1/checkout",
        json={"order_id": "ord-bad", "customer_id": "cust-001", "items": [{"variant_id": "var-jersey-001-M", "quantity": 1}], "payment": {"token": "tok_nope"}},
    )
    assert bad_pay.status_code == 400
    missing_variant = client.post(
        "/audit/v1/checkout",
        json={"order_id": "ord-miss", "customer_id": "cust-001", "items": [{"variant_id": "var-missing", "quantity": 1}], "payment": {"token": "tok_success"}},
    )
    assert missing_variant.status_code == 404
    assert client.get("/audit/v1/inventory/var-missing").status_code == 404
    assert client.post("/api/v1/orders/ord-missing/transition", headers=headers, json={"status": "Paid"}).status_code == 404
    assert client.post("/api/v1/leagues/league-alpha-1/structure", headers=headers, json={"format": "pyramid"}).status_code == 400
    assert client.post("/api/v1/leagues/league-missing/structure", headers=headers, json={"format": "single"}).status_code == 404
    assert client.post("/api/v1/matches/demo-next/reschedule", headers=headers, json={"venue_id": "venue-alpha", "starts_at": 12}).status_code == 400
    assert client.post("/api/v1/matches/missing-match/reschedule", headers=headers, json={"venue_id": "venue-alpha", "starts_at": "2026-10-19T10:00:00Z"}).status_code == 404
    closed = client.post(
        "/api/v1/matches/demo-next/reschedule",
        headers=headers,
        json={"venue_id": "venue-alpha", "starts_at": "2026-10-19T23:30:00Z"},
    )
    assert closed.status_code == 409
    assert client.post("/api/v1/matches/demo-live/pin", headers=headers, json={"variant_id": "var-missing"}).status_code == 404
    assert client.post("/api/v1/matches/missing-match/pin", headers=headers, json={"variant_id": "var-jersey-001-M"}).status_code == 404


def test_compensation_recomputes_standings():
    _reset()
    league = client.post(
        "/audit/v1/leagues",
        json={
            "league_id": "lg-comp",
            "sport_id": "football",
            "teams": ["t1", "t2"],
            "fixtures": [{"match_id": "comp-m", "home": "t1", "away": "t2"}],
            "qualification": {"spots": 1},
            "points": {"win": 3, "draw": 1, "loss": 0},
        },
    )
    assert league.status_code == 201, league.text
    for event_id, event_type, payload in (
        ("comp-start", "match_start", {}),
        ("comp-goal", "score", {"team_id": "t1", "event_type": "goal"}),
        ("comp-end", "match_end", {}),
    ):
        posted = client.post("/audit/v1/events", json={"event_id": event_id, "match_id": "comp-m", "type": event_type, "payload": payload})
        assert posted.status_code == 201, posted.text
    before = client.get("/audit/v1/standings/lg-comp").json()["table"]
    assert before[0]["team_id"] == "t1"
    assert before[0]["points"] == 3
    fixed = client.post(
        "/audit/v1/events",
        json={"event_id": "comp-fix", "match_id": "comp-m", "type": "compensation", "payload": {"compensates": "comp-goal", "reason": "offside"}},
    )
    assert fixed.status_code == 201, fixed.text
    after = client.get("/audit/v1/standings/lg-comp").json()["table"]
    assert {row["team_id"]: row["points"] for row in after} == {"t1": 1, "t2": 1}
    score = client.get("/audit/v1/score/comp-m").json()
    original = next(item for item in score["events"] if item["event_id"] == "comp-goal")
    assert original["compensated"] is True
    assert original["payload"]["event_type"] == "goal"
