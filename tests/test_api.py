import os

os.environ["DAY1_SALT"] = "demo-salt"
os.environ["DATABASE_URL"] = "sqlite:///./.pytest.db"
os.environ.pop("REDIS_URL", None)
os.environ.pop("MINIO_ENDPOINT", None)

from fastapi.testclient import TestClient

from app.main import HITS, app

client = TestClient(app)


def reset(stock=None):
    HITS.clear()
    body = {"seed": 42}
    if stock is not None:
        body["stock"] = stock
    response = client.post("/audit/v1/reset", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_health_and_reset_are_stable():
    assert client.get("/health").json() == {"status": "ok"}
    first = reset()
    second = reset()
    assert first == second
    assert first["sports"] == ["football", "basketball", "cricket", "esports"]
    assert first["stock"]["var-jersey-001-M"] == 1


def test_mystery_sport_and_signed_score():
    reset()
    created = client.post(
        "/audit/v1/sports",
        json={
            "sport_id": "kabaddi",
            "name": "Kabaddi",
            "scoring_model": "event",
            "periods": {"count": 2, "duration_minutes": 20},
            "scoring_events": {"raid_point": {"points": 1}, "all_out": {"points": 2}},
            "draw_allowed": False,
            "overtime": {"policy": "golden_score", "duration_minutes": 5},
            "roster": {"min_squad_size": 7, "max_squad_size": 12, "required_positions": {"raider": 2}},
        },
    )
    assert created.status_code == 201, created.text
    again = client.post("/audit/v1/sports", json=created.json()["config"] | {"name": "Kabaddi"})
    # config does not include every request field in a replayable body; duplicate id is enough
    duplicate = client.post(
        "/audit/v1/sports",
        json={
            "sport_id": "kabaddi",
            "name": "Kabaddi",
            "scoring_model": "event",
            "periods": {"count": 2, "duration_minutes": 20},
            "scoring_events": {"raid_point": {"points": 1}},
            "draw_allowed": False,
            "overtime": {"policy": "none"},
            "roster": {"min_squad_size": 7, "max_squad_size": 12, "required_positions": {}},
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "SPORT_EXISTS"
    league = client.post(
        "/audit/v1/leagues",
        json={
            "league_id": "lg-1",
            "sport_id": "kabaddi",
            "teams": ["t1", "t2"],
            "fixtures": [{"match_id": "m1", "home": "t1", "away": "t2"}],
            "qualification": {"spots": 1},
            "lots_seed": 42,
        },
    )
    assert league.status_code == 201, league.text
    started = client.post("/audit/v1/events", json={"event_id": "evt-1", "match_id": "m1", "type": "match_start", "payload": {}})
    assert started.status_code == 201, started.text
    assert started.json()["signature"]
    scored = client.post(
        "/audit/v1/events",
        json={"event_id": "evt-2", "match_id": "m1", "type": "score", "payload": {"team_id": "t1", "event_type": "raid_point"}},
    )
    assert scored.status_code == 201, scored.text
    replay = client.post(
        "/audit/v1/events",
        json={"event_id": "evt-2", "match_id": "m1", "type": "score", "payload": {"team_id": "t1", "event_type": "raid_point"}},
    )
    assert replay.status_code == 200
    assert replay.json() == scored.json()
    ended = client.post("/audit/v1/events", json={"event_id": "evt-3", "match_id": "m1", "type": "match_end", "payload": {}})
    assert ended.status_code == 201, ended.text
    view = client.get("/audit/v1/score/m1").json()
    assert view["home"]["score"] == 1
    assert view["outcome"] == "home_win"
    table = client.get("/audit/v1/standings/lg-1").json()
    assert table["table"][0]["team_id"] == "t1"
    assert table["table"][0]["status"] == "clinched"


def test_checkout_burst_and_receipt():
    reset({"var-jersey-001-M": 1, "var-jersey-001-L": 5})
    codes = []

    def buy(index):
        response = client.post(
            "/audit/v1/checkout",
            json={
                "order_id": f"ord-{index}",
                "customer_id": "cust-001",
                "items": [{"variant_id": "var-jersey-001-M", "quantity": 1}],
                "discount_code": None,
                "payment": {"token": "tok_success"},
            },
        )
        codes.append(response.status_code)
        return response

    responses = [buy(i) for i in range(20)]
    assert codes.count(201) == 1
    assert codes.count(409) == 19
    stock = client.get("/audit/v1/inventory/var-jersey-001-M").json()
    assert stock == {"variant_id": "var-jersey-001-M", "stock": 0, "reserved": 0}
    winner = next(response for response in responses if response.status_code == 201).json()
    assert winner["receipt"]["signature"]
    import base64

    pdf = base64.b64decode(winner["receipt"]["pdf_base64"])
    assert pdf.startswith(b"%PDF")
    text = pdf.decode("latin1")
    assert winner["order_id"] in text
    assert "PKR 4500.00" in text


def test_payment_declined_restores_stock():
    reset({"var-jersey-001-M": 1, "var-jersey-001-L": 5})
    declined = client.post(
        "/audit/v1/checkout",
        json={
            "order_id": "ord-no",
            "customer_id": "cust<script>",
            "items": [{"variant_id": "var-jersey-001-M", "quantity": 1}],
            "discount_code": None,
            "payment": {"token": "tok_declined"},
        },
    )
    assert declined.status_code == 402
    assert declined.json()["error"]["code"] == "PAYMENT_DECLINED"
    bought = client.post(
        "/audit/v1/checkout",
        json={
            "order_id": "ord-yes",
            "customer_id": "cust<script>",
            "items": [{"variant_id": "var-jersey-001-M", "quantity": 1}],
            "discount_code": "WELCOME10",
            "payment": {"token": "tok_success"},
        },
    )
    assert bought.status_code == 201, bought.text
    assert bought.json()["discount_minor"] == 45000
    assert bought.json()["total_minor"] == 405000


def test_rbac_matrix_and_rate_limit():
    reset()
    matrix = client.post("/audit/v1/rbac-matrix").json()
    admin = matrix["tokens"]["super_admin"]
    viewer = matrix["tokens"]["viewer"]
    own = matrix["context"]["own_club_id"]
    foreign = matrix["context"]["foreign_club_id"]
    ok = client.get(f"/audit/v1/rbac/clubs/{own}", headers={"Authorization": f"Bearer {viewer}"})
    denied = client.get(f"/audit/v1/rbac/clubs/{foreign}", headers={"Authorization": f"Bearer {viewer}"})
    platform = client.get("/audit/v1/rbac/platform/config", headers={"Authorization": f"Bearer {admin}"})
    missing = client.get("/audit/v1/rbac/platform/config")
    assert ok.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    assert platform.status_code == 200
    assert missing.status_code == 401
    HITS.clear()
    statuses = [client.get("/api/v1/ping").status_code for _ in range(150)]
    assert statuses[0] == 200
    assert statuses.count(429) >= 40


def test_roster_sync_tokens_and_oversized_body():
    reset()
    setup = client.post(
        "/audit/v1/roster/setup",
        json={
            "league": {
                "league_id": "roster-lg",
                "tenant_id": "alpha",
                "sport_id": "football",
                "transfer_window": {"open": "2026-01-01", "close": "2026-12-31"},
                "max_squad_size": 12,
                "required_positions": {},
            },
            "squads": [
                {"squad_id": "sa", "club_id": "club-alpha-1", "tenant_id": "alpha", "name": "A"},
                {"squad_id": "sb", "club_id": "club-alpha-1", "tenant_id": "alpha", "name": "B"},
            ],
            "players": [
                {"player_id": "p1", "name": "P", "position": "df", "squad_id": "sa", "role": "starter"},
            ],
        },
    )
    assert setup.status_code == 201, setup.text
    moved = client.post(
        "/audit/v1/roster/transfer",
        json={
            "request_id": "tr-api-1",
            "league_id": "roster-lg",
            "player_id": "p1",
            "type": "transfer",
            "from_squad_id": "sa",
            "to_squad_id": "sb",
            "effective_date": "2026-10-17",
            "loan_end_date": None,
            "reason": "Depth",
            "requested_by": "user-1",
        },
    )
    assert moved.status_code == 201, moved.text
    history = client.get("/audit/v1/roster/history/p1")
    assert history.status_code == 200
    assert history.json()["history"][0]["action"] == "transfer"
    sync = client.post(
        "/audit/v1/offline-sync",
        json={
            "device_id": "phone-9",
            "actions": [
                {
                    "action_id": "act-1",
                    "type": "roster_transfer",
                    "queued_at": "2026-10-17T06:00:00Z",
                    "base_version": 1,
                    "payload": {
                        "request_id": "tr-api-2",
                        "league_id": "roster-lg",
                        "player_id": "p1",
                        "type": "transfer",
                        "from_squad_id": "sb",
                        "to_squad_id": "sa",
                        "effective_date": "2026-10-17",
                        "loan_end_date": None,
                        "reason": "Return",
                        "requested_by": "user-1",
                    },
                }
            ],
        },
    )
    assert sync.status_code == 200, sync.text
    assert sync.json()["summary"]["applied"] == 1
    again = client.post(
        "/audit/v1/offline-sync",
        json={
            "device_id": "phone-9",
            "actions": [
                {
                    "action_id": "act-1",
                    "type": "roster_transfer",
                    "queued_at": "2026-10-17T06:00:00Z",
                    "base_version": 1,
                    "payload": {"request_id": "tr-api-2"},
                }
            ],
        },
    )
    assert again.json()["results"][0]["status"] == "duplicate"
    stale = client.post(
        "/audit/v1/offline-sync",
        json={
            "device_id": "phone-9",
            "actions": [
                {
                    "action_id": "act-2",
                    "type": "score_event",
                    "queued_at": "2026-10-17T07:00:00Z",
                    "base_version": 99,
                    "payload": {"event_id": "missing", "match_id": "missing-match", "type": "match_start", "payload": {}},
                }
            ],
        },
    )
    assert stale.json()["results"][0]["error_code"] == "INVALID_BASE_VERSION"
    none_token = _unsigned({"alg": "none", "typ": "JWT"}, {"role": "super_admin"})
    rejected = client.get("/audit/v1/rbac/platform/config", headers={"Authorization": f"Bearer {none_token}"})
    assert rejected.status_code == 401
    huge = client.post("/api/v1/checkout", content=b"{" + b"x" * 1_000_001, headers={"content-type": "application/json"})
    assert huge.status_code == 413


def _unsigned(header: dict, payload: dict) -> str:
    import base64
    import json

    def part(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part(header)}.{part(payload)}."


def test_login_and_bad_sql_is_400():
    reset()
    login = client.post("/api/v1/auth/login", json={"email": "manager@nascon.test", "password": "Nascon@26"})
    assert login.status_code == 200, login.text
    bad = client.post("/audit/v1/sports", json={"sport_id": "x'; DROP TABLE sports;--"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"
