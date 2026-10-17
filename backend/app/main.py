import os
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from time import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.errors import ApiError, validation
from app.core.security import read_token
from app.db import init_db, session
from app.domain.fixtures import generate
from app.models import Meta
from app.seed import seed
from app.services import ops, rest

LIMIT = 1_000_000
HITS: dict[str, deque] = defaultdict(deque)


def _ensure_seeded() -> None:
    init_db()
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        meta = db.get(Meta, 1)
        if meta is None:
            seed(db)
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ensure_seeded()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="NaSCon League Platform", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def on_api_error(_request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > LIMIT and request.method in ("POST", "PATCH", "PUT"):
            return JSONResponse(
                {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Body is larger than 1 MB.", "details": {}}},
                status_code=413,
            )
        path = request.url.path
        if path.startswith("/api/v1/"):
            ip = request.client.host if request.client else "unknown"
            now = time()
            bucket = HITS[ip]
            while bucket and now - bucket[0] > 10:
                bucket.popleft()
            if len(bucket) >= 100:
                retry = max(1, int(10 - (now - bucket[0])) + 1)
                response = JSONResponse(
                    {"error": {"code": "RATE_LIMITED", "message": "Too many requests.", "details": {}}},
                    status_code=429,
                )
                response.headers["Retry-After"] = str(retry)
                return response
            bucket.append(now)
        return await call_next(request)

    def db_call(work):
        _ensure_seeded()
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            result = work(db)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @app.get("/health")
    def health():
        def work(db):
            db.execute(text("SELECT 1"))
            meta = db.get(Meta, 1)
            if meta is None or not meta.seeded:
                raise ApiError(503, "UNAVAILABLE", "Baseline data is not seeded.")
            return {"status": "ok"}

        try:
            return db_call(work)
        except ApiError as exc:
            return JSONResponse(exc.body(), status_code=exc.status)

    @app.get("/api/v1/ping")
    def ping():
        return {"pong": True}

    @app.post("/audit/v1/reset")
    async def reset(request: Request):
        body = await _body(request, optional=True)
        status, _ignored = 200, None
        payload = db_call(lambda db: ops.do_reset(db, body))
        return JSONResponse(payload, status_code=status)

    @app.post("/audit/v1/sports")
    async def sports(request: Request):
        body = await _body(request)
        code, payload = db_call(lambda db: ops.create_sport(db, body))
        return JSONResponse(payload, status_code=code)

    @app.post("/audit/v1/fixtures")
    async def fixtures(request: Request):
        body = await _body(request)
        return JSONResponse(generate(body), status_code=200)

    @app.post("/audit/v1/leagues")
    async def leagues(request: Request):
        body = await _body(request)
        code, payload = db_call(lambda db: ops.create_league(db, body))
        return JSONResponse(payload, status_code=code)

    @app.post("/audit/v1/events")
    async def events(request: Request):
        body = await _body(request)
        code, payload = db_call(lambda db: ops.append_event(db, body))
        return JSONResponse(payload, status_code=code)

    @app.get("/audit/v1/score/{match_id}")
    def score(match_id: str):
        return db_call(lambda db: ops.score_view(db, match_id))

    @app.get("/audit/v1/standings/{league_id}")
    def standings(league_id: str):
        return db_call(lambda db: ops.standings_view(db, league_id))

    @app.post("/audit/v1/roster/setup")
    async def roster_setup(request: Request):
        body = await _body(request)
        code, payload = db_call(lambda db: rest.setup_roster(db, body))
        return JSONResponse(payload, status_code=code)

    @app.post("/audit/v1/roster/transfer")
    async def roster_transfer(request: Request):
        body = await _body(request)
        code, payload = db_call(lambda db: rest.do_transfer(db, body))
        return JSONResponse(payload, status_code=code)

    @app.get("/audit/v1/roster/history/{player_id}")
    def roster_history(player_id: str, as_of: str | None = None):
        return db_call(lambda db: rest.roster_history(db, player_id, as_of))

    @app.post("/audit/v1/checkout")
    async def checkout(request: Request):
        body = await _body(request)
        code, payload = db_call(lambda db: rest.checkout(db, body))
        return JSONResponse(payload, status_code=code)

    @app.get("/audit/v1/inventory/{variant_id}")
    def inventory(variant_id: str):
        return db_call(lambda db: rest.inventory(db, variant_id))

    @app.post("/audit/v1/rbac-matrix")
    def matrix():
        return db_call(rest.rbac_matrix)

    @app.api_route("/audit/v1/rbac/tenants", methods=["POST"])
    async def probe_tenants(request: Request):
        claims = read_token(request.headers.get("authorization"))
        db_call(lambda db: rest.authorize(db, claims, "tenants", None))
        return {"authorized": True}

    @app.api_route("/audit/v1/rbac/leagues/{league_id}/fixtures", methods=["POST"])
    async def probe_league(league_id: str, request: Request):
        claims = read_token(request.headers.get("authorization"))
        db_call(lambda db: rest.authorize(db, claims, "league", league_id))
        return {"authorized": True}

    @app.api_route("/audit/v1/rbac/clubs/{club_id}/roster", methods=["POST"])
    async def probe_roster(club_id: str, request: Request):
        claims = read_token(request.headers.get("authorization"))
        db_call(lambda db: rest.authorize(db, claims, "club_write", club_id))
        return {"authorized": True}

    @app.api_route("/audit/v1/rbac/clubs/{club_id}/products", methods=["POST"])
    async def probe_products(club_id: str, request: Request):
        claims = read_token(request.headers.get("authorization"))
        db_call(lambda db: rest.authorize(db, claims, "club_write", club_id))
        return {"authorized": True}

    @app.api_route("/audit/v1/rbac/players/{player_id}", methods=["PATCH"])
    async def probe_player(player_id: str, request: Request):
        claims = read_token(request.headers.get("authorization"))
        db_call(lambda db: rest.authorize(db, claims, "player", player_id))
        return {"authorized": True}

    @app.api_route("/audit/v1/rbac/clubs/{club_id}", methods=["GET"])
    def probe_club(club_id: str, request: Request):
        claims = read_token(request.headers.get("authorization"))
        db_call(lambda db: rest.authorize(db, claims, "club_read", club_id))
        return {"authorized": True}

    @app.api_route("/audit/v1/rbac/platform/config", methods=["GET"])
    def probe_platform(request: Request):
        claims = read_token(request.headers.get("authorization"))
        db_call(lambda db: rest.authorize(db, claims, "platform", None))
        return {"authorized": True}

    @app.post("/audit/v1/offline-sync")
    async def sync(request: Request):
        body = await _body(request)
        return db_call(lambda db: rest.offline_sync(db, body))

    @app.post("/api/v1/auth/login")
    async def login(request: Request):
        body = await _body(request)
        return db_call(lambda db: rest.login(db, body))

    @app.get("/api/v1/fixtures")
    def fixtures_list(request: Request):
        _user(request)
        return db_call(rest.list_fixtures)

    @app.get("/api/v1/matches/{match_id}")
    def match_detail(match_id: str, request: Request):
        _user(request)
        def work(db):
            view = ops.score_view(db, match_id)
            match = db.get(__import__("app.models", fromlist=["Match"]).Match, match_id)
            view["pinned_variant_id"] = match.pinned_variant_id
            view["starts_at"] = match.starts_at
            sport = db.get(__import__("app.models", fromlist=["Sport"]).Sport, match.sport_id)
            config = sport.config if sport is not None else {}
            events = config.get("scoring_events") if isinstance(config, dict) else None
            view["scoring_events"] = list(events) if isinstance(events, dict) else []
            return view
        return db_call(work)

    @app.get("/api/v1/standings/{league_id}")
    def api_standings(league_id: str, request: Request):
        _user(request)
        return db_call(lambda db: ops.standings_view(db, league_id))

    @app.get("/api/v1/clubs/{club_id}")
    def club(club_id: str, request: Request):
        _user(request)
        return db_call(lambda db: rest.club_profile(db, club_id))

    @app.get("/api/v1/products")
    def products(request: Request):
        _user(request)
        return db_call(rest.catalogue)

    @app.post("/api/v1/checkout")
    async def api_checkout(request: Request):
        _user(request)
        body = await _body(request)
        code, payload = db_call(lambda db: rest.checkout(db, body))
        return JSONResponse(payload, status_code=code)

    @app.post("/api/v1/events")
    async def api_events(request: Request):
        _user(request)
        body = await _body(request)
        code, payload = db_call(lambda db: ops.append_event(db, body))
        return JSONResponse(payload, status_code=code)

    @app.post("/api/v1/roster/transfer")
    async def api_transfer(request: Request):
        _user(request)
        body = await _body(request)
        code, payload = db_call(lambda db: rest.do_transfer(db, body))
        return JSONResponse(payload, status_code=code)

    @app.post("/api/v1/offline-sync")
    async def api_sync(request: Request):
        _user(request)
        body = await _body(request)
        return db_call(lambda db: rest.offline_sync(db, body))

    @app.post("/api/v1/orders/{order_id}/transition")
    async def order_transition(order_id: str, request: Request):
        _user(request)
        body = await _body(request)
        return db_call(lambda db: rest.transition_order(db, order_id, body.get("status")))

    @app.post("/api/v1/leagues/{league_id}/structure")
    async def structure(league_id: str, request: Request):
        _user(request)
        body = await _body(request)
        body["league_id"] = league_id
        return db_call(lambda db: rest.configure_structure(db, body))

    @app.post("/api/v1/matches/{match_id}/reschedule")
    async def reschedule(match_id: str, request: Request):
        _user(request)
        body = await _body(request)
        return db_call(lambda db: rest.reschedule(db, match_id, body))

    @app.post("/api/v1/matches/{match_id}/pin")
    async def pin(match_id: str, request: Request):
        _user(request)
        body = await _body(request)
        return db_call(lambda db: rest.pin_product(db, match_id, body.get("variant_id")))

    return app


def _user(request: Request) -> dict:
    return read_token(request.headers.get("authorization"))


async def _body(request: Request, optional: bool = False) -> dict:
    raw = await request.body()
    if len(raw) > LIMIT:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "Body is larger than 1 MB.")
    if not raw:
        if optional:
            return {}
        raise validation(["body"])
    try:
        import json

        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise validation(["body"], "Malformed JSON.") from exc
    if not isinstance(parsed, dict):
        raise validation(["body"])
    return parsed


app = create_app()
