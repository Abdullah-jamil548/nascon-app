"""Roster, store, RBAC, offline sync, and mobile reads."""

from __future__ import annotations

import base64
import io
from datetime import date, datetime, timezone

from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.canonical import sign
from app.core.errors import ApiError, validation
from app.core.security import issue_token, verify_password
from app.domain.roster import history_view, transfer
from app.domain.sports import is_int
from app.models import (
    ClientRequest,
    Club,
    Discount,
    Event,
    League,
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
from app.services.ops import SALT, append_event, digest, emit, ident, score_view, stamp

ORDER_FLOW = {
    "Pending": {"Paid", "Cancelled"},
    "Paid": {"Packed", "Cancelled", "Refunded"},
    "Packed": {"Shipped", "Cancelled"},
    "Shipped": {"Delivered"},
    "Delivered": {"Refunded"},
    "Cancelled": set(),
    "Refunded": set(),
}


def _roster_state(db: Session) -> dict:
    leagues = {}
    for league in db.scalars(select(League)):
        if not league.transfer_open:
            continue
        leagues[league.league_id] = {
            "league_id": league.league_id,
            "tenant_id": league.tenant_id,
            "transfer_window": {"open": league.transfer_open, "close": league.transfer_close},
            "max_squad_size": league.max_squad_size,
            "required_positions": league.required_positions or {},
        }
    squads = {
        row.squad_id: {
            "squad_id": row.squad_id,
            "league_id": row.league_id,
            "club_id": row.club_id,
            "tenant_id": row.tenant_id,
            "name": row.name,
        }
        for row in db.scalars(select(Squad))
    }
    players = {}
    for row in db.scalars(select(Player)):
        players[row.player_id] = {
            "player_id": row.player_id,
            "name": row.name,
            "position": row.position,
            "league_id": row.league_id,
            "squad_id": row.squad_id,
            "club_id": row.club_id,
            "role": row.role,
            "on_loan": row.on_loan,
            "parent_squad_id": row.parent_squad_id,
            "loan_end_date": row.loan_end_date,
            "registration_type": row.registration_type,
            "start_date": row.start_date,
            "pending": row.pending,
        }
    audits = [row.payload for row in db.scalars(select(RosterAudit).order_by(RosterAudit.audit_id))]
    live = [
        {"match_id": row.match_id, "home_squad_id": row.home_squad_id, "away_squad_id": row.away_squad_id, "league_id": row.league_id}
        for row in db.scalars(select(LiveRosterMatch))
    ]
    return {"leagues": leagues, "squads": squads, "players": players, "audits": audits, "matches_in_progress": live}


def _persist_roster(db: Session, state: dict) -> None:
    for player_id, data in state["players"].items():
        row = db.get(Player, player_id)
        if row is None:
            continue
        row.squad_id = data.get("squad_id")
        row.role = data.get("role")
        row.on_loan = bool(data.get("on_loan"))
        row.parent_squad_id = data.get("parent_squad_id")
        row.loan_end_date = data.get("loan_end_date")
        row.registration_type = data.get("registration_type")
        row.start_date = data.get("start_date")
        row.pending = data.get("pending")
    known = {row.audit_id for row in db.scalars(select(RosterAudit))}
    for entry in state["audits"]:
        if entry["audit_id"] in known:
            continue
        db.add(RosterAudit(audit_id=entry["audit_id"], player_id=entry["player_id"], league_id=entry["league_id"], payload=entry))
    db.flush()


def setup_roster(db: Session, body: dict) -> tuple[int, dict]:
    league = body.get("league")
    squads = body.get("squads")
    players = body.get("players")
    if not isinstance(league, dict) or not isinstance(squads, list) or not isinstance(players, list):
        raise validation(["league", "squads", "players"])
    league_id = ident(league.get("league_id"), "league_id")
    tenant_id = ident(league.get("tenant_id"), "tenant_id")
    sport_id = ident(league.get("sport_id"), "sport_id")
    sport = db.get(Sport, sport_id)
    if sport is None:
        raise ApiError(404, "SPORT_NOT_FOUND", "Sport does not exist.")
    window = league.get("transfer_window") or {}
    if not isinstance(window, dict) or "open" not in window or "close" not in window:
        raise validation(["transfer_window"])
    roster = sport.config["roster"]
    max_size = league.get("max_squad_size", roster["max_squad_size"])
    required = league.get("required_positions", roster["required_positions"])
    if not is_int(max_size) or max_size < 1 or not isinstance(required, dict):
        raise validation(["max_squad_size"])
    for row in list(db.scalars(select(Squad).where(Squad.league_id == league_id))):
        db.delete(row)
    for row in list(db.scalars(select(Player).where(Player.league_id == league_id))):
        db.delete(row)
    for row in list(db.scalars(select(RosterAudit).where(RosterAudit.league_id == league_id))):
        db.delete(row)
    for row in list(db.scalars(select(LiveRosterMatch).where(LiveRosterMatch.league_id == league_id))):
        db.delete(row)
    db.flush()
    record = db.get(League, league_id) or League(league_id=league_id)
    if db.get(League, league_id) is None:
        db.add(record)
    record.tenant_id = tenant_id
    record.sport_id = sport_id
    record.transfer_open = window["open"]
    record.transfer_close = window["close"]
    record.max_squad_size = max_size
    record.required_positions = required
    counts = {}
    squad_ids = set()
    for squad in squads:
        squad_id = ident(squad.get("squad_id"), "squad_id")
        squad_ids.add(squad_id)
        db.add(
            Squad(
                squad_id=squad_id,
                league_id=league_id,
                club_id=ident(squad.get("club_id"), "club_id"),
                tenant_id=ident(squad.get("tenant_id"), "tenant_id"),
                name=str(squad.get("name") or squad_id),
            )
        )
        counts[squad_id] = 0
    for player in players:
        squad_id = ident(player.get("squad_id"), "squad_id")
        if squad_id not in squad_ids:
            raise ApiError(404, "SQUAD_NOT_FOUND", "Squad does not exist.")
        role = player.get("role")
        if role not in ("captain", "vice_captain", "starter", "substitute"):
            raise validation(["role"])
        db.add(
            Player(
                player_id=ident(player.get("player_id"), "player_id"),
                name=str(player.get("name") or player.get("player_id")),
                position=str(player.get("position") or "player"),
                league_id=league_id,
                squad_id=squad_id,
                club_id=next(item.get("club_id") for item in squads if item.get("squad_id") == squad_id),
                role=role,
                on_loan=False,
            )
        )
        counts[squad_id] += 1
    for live in body.get("matches_in_progress") or []:
        db.add(
            LiveRosterMatch(
                match_id=ident(live.get("match_id"), "match_id"),
                league_id=league_id,
                home_squad_id=ident(live.get("home_squad_id"), "home_squad_id"),
                away_squad_id=ident(live.get("away_squad_id"), "away_squad_id"),
            )
        )
    db.flush()
    return 201, {"league_id": league_id, "status": "configured", "squad_counts": counts}


def do_transfer(db: Session, body: dict) -> tuple[int, dict]:
    request_id = ident(body.get("request_id"), "request_id")
    stored = db.scalar(select(ClientRequest).where(ClientRequest.kind == "transfer", ClientRequest.client_id == request_id))
    body_hash = digest(body)
    if stored is not None:
        if stored.body_hash == body_hash:
            return 200, stored.response
        raise ApiError(409, "DUPLICATE_EVENT_ID", "request_id was already used with a different body.")
    state = _roster_state(db)
    result = transfer(state, body, datetime.now(timezone.utc))
    _persist_roster(db, state)
    db.add(ClientRequest(kind="transfer", client_id=request_id, body_hash=body_hash, status_code=201, response=result))
    db.flush()
    emit(db, "roster.changed", result["audit_entry"])
    return 201, result


def roster_history(db: Session, player_id: str, as_of: str | None) -> dict:
    if db.get(Player, player_id) is None:
        raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
    day = date.today() if not as_of else date.fromisoformat(as_of)
    state = _roster_state(db)
    view = history_view(state, player_id, day)
    _persist_roster(db, state)
    public_history = []
    for entry in view["history"]:
        public_history.append({key: entry[key] for key in (
            "audit_id", "action", "player_id", "league_id", "from_squad_id", "to_squad_id",
            "effective_date", "state", "who", "when", "why",
        ) if key in entry})
    view["history"] = public_history
    return view


def _pdf(order_id: str, total_minor: int) -> str:
    major = f"{total_minor // 100}.{abs(total_minor) % 100:02d}"
    # Literal PDF text so extractors and a byte scan both see the receipt lines.
    stream = f"BT /F1 12 Tf 72 720 Td ({order_id}) Tj 0 -24 Td (PKR {major}) Tj ET".encode("latin1")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Count 1 /Kids [3 0 R] >>endobj\n",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n",
        b"4 0 obj<< /Length " + str(len(stream)).encode() + b" >>stream\n" + stream + b"\nendstream\nendobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    header = b"%PDF-1.4\n"
    body = b"".join(objects)
    xref_at = len(header) + len(body)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    cursor = len(header)
    for item in objects:
        xref += f"{cursor:010d} 00000 n \n".encode()
        cursor += len(item)
    trailer = f"trailer<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return base64.b64encode(header + body + xref + trailer).decode("ascii")


def checkout(db: Session, body: dict) -> tuple[int, dict]:
    fields = []
    if "order_id" not in body:
        fields.append("order_id")
    if "customer_id" not in body or not isinstance(body.get("customer_id"), str) or not body.get("customer_id"):
        fields.append("customer_id")
    items = body.get("items")
    if not isinstance(items, list) or not (1 <= len(items) <= 10):
        fields.append("items")
        items = []
    payment = body.get("payment")
    if not isinstance(payment, dict) or payment.get("token") not in ("tok_success", "tok_declined", "tok_capture_fail"):
        fields.append("payment")
    if fields:
        raise validation(fields)
    order_id = ident(body["order_id"], "order_id")
    seen = set()
    clean = []
    for item in items:
        if not isinstance(item, dict):
            raise validation(["items"])
        variant_id = ident(item.get("variant_id"), "variant_id")
        quantity = item.get("quantity")
        if not is_int(quantity) or not (1 <= quantity <= 10):
            raise validation(["quantity"])
        if variant_id in seen:
            raise validation(["items"])
        seen.add(variant_id)
        clean.append((variant_id, quantity))
    code = body.get("discount_code", None)
    if code is not None and not isinstance(code, str):
        raise validation(["discount_code"])
    body_hash = digest(body)
    existing = db.get(Order, order_id)
    if existing is not None:
        if existing.body_hash == body_hash:
            return 200, existing.response
        raise ApiError(409, "DUPLICATE_ORDER_ID", "order_id was already used with a different body.")
    from sqlalchemy import update as sql_update

    rows = []
    for variant_id, _quantity in sorted(clean):
        row = db.scalar(select(Variant).where(Variant.variant_id == variant_id).with_for_update())
        if row is None:
            raise ApiError(404, "VARIANT_NOT_FOUND", "Variant does not exist.")
        rows.append(row)
    by_id = {row.variant_id: row for row in rows}
    if code not in (None, "WELCOME10"):
        raise ApiError(400, "INVALID_DISCOUNT_CODE", "Unknown discount code.")
    for variant_id, quantity in clean:
        result = db.execute(
            sql_update(Variant)
            .where(Variant.variant_id == variant_id, Variant.stock >= quantity)
            .values(stock=Variant.stock - quantity, reserved=Variant.reserved + quantity)
        )
        if result.rowcount != 1:
            raise ApiError(409, "OUT_OF_STOCK", f"Variant {variant_id} has no stock left.", {"variant_id": variant_id})
    db.flush()
    db.expire_all()
    for row in rows:
        db.refresh(row)
    token = payment["token"]
    if token == "tok_declined":
        raise ApiError(402, "PAYMENT_DECLINED", "Mock payment was declined.")
    if token == "tok_capture_fail":
        raise ApiError(402, "PAYMENT_CAPTURE_FAILED", "Mock capture failed.")
    subtotal = sum(by_id[variant_id].price_minor * quantity for variant_id, quantity in clean)
    discount = (subtotal * 10) // 100 if code == "WELCOME10" else 0
    total = subtotal - discount
    paid_at = stamp()
    item_rows = [
        {"variant_id": variant_id, "quantity": quantity, "unit_price_minor": by_id[variant_id].price_minor}
        for variant_id, quantity in clean
    ]
    receipt_payload = {
        "order_id": order_id,
        "customer_id": body["customer_id"],
        "items": item_rows,
        "subtotal_minor": subtotal,
        "discount_minor": discount,
        "total_minor": total,
        "currency": "PKR",
        "paid_at": paid_at,
    }
    response = {
        "order_id": order_id,
        "status": "paid",
        "currency": "PKR",
        "items": item_rows,
        "subtotal_minor": subtotal,
        "discount_minor": discount,
        "total_minor": total,
        "payment": {"authorization_id": f"auth_{order_id}", "capture_id": f"cap_{order_id}", "state": "captured"},
        "receipt": {
            "payload": receipt_payload,
            "signature": sign(SALT(), order_id, receipt_payload),
            "pdf_base64": _pdf(order_id, total),
        },
    }
    for row in rows:
        row.reserved = 0
    db.add(
        Order(
            order_id=order_id,
            customer_id=body["customer_id"],
            status="Paid",
            currency="PKR",
            subtotal_minor=subtotal,
            discount_minor=discount,
            total_minor=total,
            payment=response["payment"],
            receipt=response["receipt"],
            body_hash=body_hash,
            response=response,
            paid_at=paid_at,
        )
    )
    for item in item_rows:
        db.add(OrderItem(order_id=order_id, variant_id=item["variant_id"], quantity=item["quantity"], unit_price_minor=item["unit_price_minor"]))
    emit(db, "order.paid", {"order_id": order_id, "status": "Paid"})
    _store_pdf(order_id, response["receipt"]["pdf_base64"])
    db.flush()
    return 201, response


def _store_pdf(order_id: str, pdf_base64: str) -> None:
    endpoint = __import__("os").environ.get("MINIO_ENDPOINT")
    if not endpoint:
        return
    try:
        import urllib3
        from minio import Minio

        http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=1.0, read=2.0))
        client = Minio(
            endpoint,
            access_key=__import__("os").environ.get("MINIO_ACCESS_KEY", "nascon"),
            secret_key=__import__("os").environ.get("MINIO_SECRET_KEY", "nasconnascon"),
            secure=False,
            http_client=http,
        )
        if not client.bucket_exists("receipts"):
            client.make_bucket("receipts")
        raw = base64.b64decode(pdf_base64)
        client.put_object("receipts", f"{order_id}.pdf", io.BytesIO(raw), len(raw), content_type="application/pdf")
    except Exception:
        return


def inventory(db: Session, variant_id: str) -> dict:
    row = db.get(Variant, variant_id)
    if row is None:
        raise ApiError(404, "VARIANT_NOT_FOUND", "Variant does not exist.")
    return {"variant_id": variant_id, "stock": row.stock, "reserved": row.reserved}


def rbac_matrix(db: Session) -> dict:
    users = {row.role: row for row in db.scalars(select(User))}
    tokens = {}
    for role, claim in (
        ("super_admin", "super_admin"),
        ("league_organizer", "league_organizer"),
        ("club_manager", "club_manager"),
        ("player", "player"),
        ("viewer", "viewer"),
    ):
        user = users[role]
        tokens[claim] = issue_token(
            {
                "role": role,
                "sub": user.email,
                "tenant_id": user.tenant_id,
                "league_id": user.league_id,
                "club_id": user.club_id,
                "player_id": user.player_id,
            }
        )
    return {
        "tokens": tokens,
        "context": {
            "tenant_id": "tenant-alpha",
            "foreign_tenant_id": "tenant-beta",
            "own_league_id": "league-alpha-1",
            "other_league_id": "league-alpha-2",
            "foreign_league_id": "league-beta-1",
            "own_club_id": "club-alpha-1",
            "other_club_id": "club-alpha-2",
            "foreign_club_id": "club-beta-1",
            "own_player_id": "player-alpha-1",
            "other_player_id": "player-alpha-2",
            "foreign_player_id": "player-beta-1",
        },
    }


def authorize(db: Session, claims: dict, probe: str, target: str | None) -> None:
    role = claims.get("role")
    if probe == "platform":
        if role != "super_admin":
            raise ApiError(403, "FORBIDDEN", "Platform config is restricted.")
        return
    if probe == "tenants":
        if role != "super_admin":
            raise ApiError(403, "FORBIDDEN", "Tenant creation is restricted.")
        return
    if probe == "league":
        league = db.get(League, target)
        if league is None:
            raise ApiError(404, "LEAGUE_NOT_FOUND", "League does not exist.")
        if role == "super_admin":
            return
        if role == "league_organizer" and claims.get("league_id") == target and claims.get("tenant_id") == league.tenant_id:
            return
        raise ApiError(403, "FORBIDDEN", "League is outside your scope.")
    if probe == "club_write":
        club = db.get(Club, target)
        if club is None:
            raise ApiError(404, "SQUAD_NOT_FOUND", "Club does not exist.")
        if role == "super_admin":
            return
        if role == "club_manager" and claims.get("club_id") == target and claims.get("tenant_id") == club.tenant_id:
            return
        raise ApiError(403, "FORBIDDEN", "Club is outside your scope.")
    if probe == "club_read":
        club = db.get(Club, target)
        if club is None:
            raise ApiError(404, "SQUAD_NOT_FOUND", "Club does not exist.")
        if role == "super_admin":
            return
        if claims.get("tenant_id") == club.tenant_id and role in ("league_organizer", "club_manager", "player", "viewer"):
            return
        raise ApiError(403, "FORBIDDEN", "Club is outside your tenant.")
    if probe == "player":
        player = db.get(Player, target)
        if player is None:
            raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
        club = db.get(Club, player.club_id) if player.club_id else None
        if role == "super_admin":
            return
        if club is not None and claims.get("tenant_id") != club.tenant_id:
            raise ApiError(403, "FORBIDDEN", "Player is outside your tenant.")
        if role == "player" and claims.get("player_id") == target:
            return
        if role == "club_manager" and claims.get("club_id") and claims.get("club_id") == player.club_id:
            return
        raise ApiError(403, "FORBIDDEN", "Player is outside your scope.")
    raise ApiError(403, "FORBIDDEN", "Not allowed.")


def login(db: Session, body: dict) -> dict:
    email = body.get("email")
    password = body.get("password")
    user = db.get(User, email) if isinstance(email, str) else None
    if user is None or not isinstance(password, str) or not verify_password(password, user.password_hash):
        raise ApiError(401, "UNAUTHENTICATED", "Email or password is incorrect.")
    token = issue_token(
        {
            "role": user.role,
            "sub": user.email,
            "tenant_id": user.tenant_id,
            "league_id": user.league_id,
            "club_id": user.club_id,
            "player_id": user.player_id,
        }
    )
    tenant = db.get(Tenant, user.tenant_id) if user.tenant_id else None
    return {
        "token": token,
        "role": user.role,
        "name": user.name,
        "email": user.email,
        "tenant": None
        if tenant is None
        else {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "logo": tenant.logo,
            "primary_color": tenant.primary_color,
            "secondary_color": tenant.secondary_color,
        },
    }


def offline_sync(db: Session, body: dict) -> dict:
    actions = body.get("actions")
    device_id = body.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        raise validation(["device_id"])
    if not isinstance(actions, list) or not (1 <= len(actions) <= 100):
        raise validation(["actions"])
    normalized = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise validation(["actions"])
        if action.get("type") not in ("score_event", "roster_transfer"):
            raise validation(["type"])
        if not isinstance(action.get("action_id"), str) or not isinstance(action.get("queued_at"), str):
            raise validation(["action_id"])
        if not is_int(action.get("base_version")):
            raise validation(["base_version"])
        if not isinstance(action.get("payload"), dict):
            raise validation(["payload"])
        normalized.append((index, action))
    order = sorted(normalized, key=lambda item: (item[1]["queued_at"], item[0]))
    results = [None] * len(actions)
    for position, (index, action) in enumerate(order, start=1):
        results[index] = _sync_one(db, device_id, action, position)
    summary = {"applied": 0, "rejected": 0, "duplicate": 0}
    for item in results:
        summary[item["status"]] += 1
    return {"device_id": device_id, "results": results, "summary": summary}


def _sync_one(db: Session, device_id: str, action: dict, position: int) -> dict:
    action_id = action["action_id"]
    previous = db.scalar(
        select(SyncAction).where(SyncAction.device_id == device_id, SyncAction.action_id == action_id)
    )
    if previous is not None:
        version = _sync_version(db, action)
        return _sync_result(action_id, "duplicate", "skipped_duplicate", None, position, version, None)
    if action["type"] == "score_event":
        payload = action["payload"]
        event = db.get(Event, payload.get("event_id")) if isinstance(payload, dict) else None
        if event is not None and event.body_hash == digest(payload):
            saved = _sync_result(action_id, "duplicate", "skipped_duplicate", None, position, _sync_version(db, action), None)
            db.add(SyncAction(device_id=device_id, action_id=action_id, response=saved))
            return saved
    current = _sync_version(db, action)
    if action["base_version"] > current:
        saved = _sync_result(action_id, "rejected", "server_wins", "INVALID_BASE_VERSION", position, current, None)
        db.add(SyncAction(device_id=device_id, action_id=action_id, response=saved))
        db.flush()
        return saved
    try:
        if action["type"] == "score_event":
            _status, response = append_event(db, action["payload"])
            signature = response.get("signature")
            version = response.get("match_version", current)
        else:
            _status, response = do_transfer(db, action["payload"])
            signature = None
            version = _sync_version(db, action)
        resolution = "appended" if action["base_version"] == current else "rebased_append"
        saved = _sync_result(action_id, "applied", resolution, None, position, version, signature)
    except ApiError as exc:
        saved = _sync_result(action_id, "rejected", "server_wins", exc.code, position, _sync_version(db, action), None)
    db.add(SyncAction(device_id=device_id, action_id=action_id, response=saved))
    db.flush()
    return saved


def _sync_version(db: Session, action: dict) -> int:
    if action["type"] == "score_event":
        match_id = action["payload"].get("match_id") if isinstance(action.get("payload"), dict) else None
        if not match_id or db.get(Match, match_id) is None:
            return 0
        return len(list(db.scalars(select(Event).where(Event.match_id == match_id))))
    player_id = action["payload"].get("player_id") if isinstance(action.get("payload"), dict) else None
    if not player_id:
        return 0
    return len(list(db.scalars(select(RosterAudit).where(RosterAudit.player_id == player_id))))


def _sync_result(action_id, status, resolution, error_code, position, version, signature) -> dict:
    return {
        "action_id": action_id,
        "status": status,
        "resolution": resolution,
        "error_code": error_code,
        "processed_order": position,
        "server_version": version,
        "signature": signature,
    }


def transition_order(db: Session, order_id: str, status: str) -> dict:
    row = db.get(Order, order_id)
    if row is None:
        raise ApiError(404, "VARIANT_NOT_FOUND", "Order does not exist.")
    current = row.status
    if status not in ORDER_FLOW.get(current, set()):
        raise ApiError(409, "ILLEGAL_TRANSITION", f"Cannot move an order from {current} to {status}.")
    row.status = status
    if status == "Refunded":
        for item in db.scalars(select(OrderItem).where(OrderItem.order_id == order_id)):
            variant = db.get(Variant, item.variant_id)
            if variant is not None:
                variant.stock += item.quantity
    emit(db, "order.transition", {"order_id": order_id, "from": current, "to": status})
    db.flush()
    return {"order_id": order_id, "status": status}


def configure_structure(db: Session, body: dict) -> dict:
    league_id = ident(body.get("league_id"), "league_id")
    league = db.get(League, league_id)
    if league is None:
        raise ApiError(404, "LEAGUE_NOT_FOUND", "League does not exist.")
    kind = body.get("format")
    if kind not in ("single", "multi_division", "knockout", "hybrid"):
        raise validation(["format"])
    league.structure = {
        "format": kind,
        "divisions": body.get("divisions") or [],
        "promotion_slots": body.get("promotion_slots") or 0,
        "relegation_slots": body.get("relegation_slots") or 0,
        "stages": body.get("stages") or [],
    }
    db.flush()
    return {"league_id": league_id, "status": "configured", "structure": league.structure}


def reschedule(db: Session, match_id: str, body: dict) -> dict:
    match = db.get(Match, match_id)
    if match is None:
        raise ApiError(404, "MATCH_NOT_FOUND", "Match does not exist.")
    venue_id = ident(body.get("venue_id"), "venue_id")
    starts = body.get("starts_at")
    if not isinstance(starts, str):
        raise validation(["starts_at"])
    venue = db.get(Venue, venue_id)
    conflicts = []
    if venue is None:
        conflicts.append({"type": "venue", "id": venue_id})
    else:
        clock = starts[11:16] if len(starts) >= 16 else ""
        windows = venue.windows or []
        if windows and not any(item.get("open", "00:00") <= clock <= item.get("close", "23:59") for item in windows):
            conflicts.append({"type": "venue", "id": venue_id})
    for other in db.scalars(select(Match).where(Match.match_id != match_id)):
        if other.starts_at == starts and other.venue_id == venue_id:
            conflicts.append({"type": "venue", "id": venue_id})
        if other.starts_at == starts and (other.home_team_id in (match.home_team_id, match.away_team_id) or other.away_team_id in (match.home_team_id, match.away_team_id)):
            team = other.home_team_id if other.home_team_id in (match.home_team_id, match.away_team_id) else other.away_team_id
            conflicts.append({"type": "team", "id": team})
    if conflicts:
        raise ApiError(409, "ILLEGAL_TRANSITION", "Schedule conflict.", {"conflicts": conflicts})
    match.venue_id = venue_id
    match.starts_at = starts
    emit(db, "match.rescheduled", {"match_id": match_id, "venue_id": venue_id, "starts_at": starts})
    db.flush()
    return {"match_id": match_id, "status": "rescheduled", "venue_id": venue_id, "starts_at": starts}


def pin_product(db: Session, match_id: str, variant_id: str) -> dict:
    match = db.get(Match, match_id)
    variant = db.get(Variant, variant_id)
    if match is None:
        raise ApiError(404, "MATCH_NOT_FOUND", "Match does not exist.")
    if variant is None:
        raise ApiError(404, "VARIANT_NOT_FOUND", "Variant does not exist.")
    match.pinned_variant_id = variant_id
    emit(db, "match.pin", {"match_id": match_id, "variant_id": variant_id})
    db.flush()
    return {"match_id": match_id, "variant_id": variant_id}


def list_fixtures(db: Session) -> dict:
    rows = []
    for match in db.scalars(select(Match)):
        view = score_view(db, match.match_id)
        rows.append(
            {
                "match_id": match.match_id,
                "league_id": match.league_id,
                "sport_id": match.sport_id,
                "home": match.home_team_id,
                "away": match.away_team_id,
                "status": view["status"],
                "starts_at": match.starts_at,
                "score": {"home": view["home"]["score"], "away": view["away"]["score"]},
            }
        )
    return {"fixtures": rows}


def club_profile(db: Session, club_id: str) -> dict:
    club = db.get(Club, club_id)
    if club is None:
        raise ApiError(404, "SQUAD_NOT_FOUND", "Club does not exist.")
    tenant = db.get(Tenant, club.tenant_id)
    roster = [
        {"player_id": row.player_id, "name": row.name, "position": row.position, "role": row.role, "squad_id": row.squad_id, "league_id": row.league_id}
        for row in db.scalars(select(Player).where(Player.club_id == club_id))
    ]
    return {
        "club_id": club.club_id,
        "name": club.name,
        "tenant": {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "logo": tenant.logo,
            "primary_color": tenant.primary_color,
            "secondary_color": tenant.secondary_color,
        },
        "venue": {"name": club.venue_name, "latitude": club.latitude, "longitude": club.longitude},
        "socials": club.socials,
        "staff": club.staff,
        "roster": roster,
    }


def catalogue(db: Session) -> dict:
    products = []
    for product in db.scalars(select(Product)):
        variants = [
            {
                "variant_id": row.variant_id,
                "size": row.size,
                "color": row.color,
                "price_minor": row.price_minor,
                "currency": row.currency,
                "stock": row.stock,
            }
            for row in db.scalars(select(Variant).where(Variant.product_id == product.product_id))
        ]
        products.append(
            {
                "product_id": product.product_id,
                "name": product.name,
                "category": product.category,
                "description": product.description,
                "tenant_id": product.tenant_id,
                "variants": variants,
            }
        )
    return {"products": products, "discount_codes": [row.code for row in db.scalars(select(Discount))]}
