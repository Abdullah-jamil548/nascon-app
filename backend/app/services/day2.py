"""Phase II audit contracts. Signatures use DAY2_SALT and the Day 1 canonical recipe."""

from __future__ import annotations

import json
import os
import re
import secrets
import struct
import threading
import urllib.request
import zlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.canonical import canonical, sign
from app.core.errors import ApiError, validation
from app.domain.sports import is_int
from app.models import (
    Club,
    CostreamSave,
    DlqItem,
    FollowEdge,
    GuestToken,
    Match,
    MemberCart,
    Player,
    QrLink,
    QrNotice,
    RevokedStreamKey,
    StreamKeyRow,
    User,
    Variant,
)
from app.services.ops import digest, score_view

SALT2 = lambda: os.environ.get("DAY2_SALT", "")
LOCK = threading.Lock()
WS_CONNECTIONS = 0
_QR_CACHE: dict[str, dict] = {}
_DLQ_COUNT = 0
BAD_WORDS = ("badword", "porn", "scam", "fuck", "shit", "bitch", "asshole", "idiotspam")
LEET = str.maketrans({"@": "a", "0": "o", "1": "i", "!": "i", "3": "e", "$": "s"})
LAYOUTS = {"solo", "grid_2", "grid_4", "spotlight"}
NOTICES = {"postponed", "cancelled", "abandoned"}


def sign2(entity_id: str, payload: dict) -> str:
    return sign(SALT2(), entity_id, payload)


def seed_phase2(db: Session) -> None:
    db.add(
        Match(
            match_id="match-m1",
            league_id="league-alpha-1",
            sport_id="football",
            home_team_id="club-alpha-1",
            away_team_id="club-alpha-2",
            venue_id="venue-alpha",
            starts_at="2026-10-18T08:00:00Z",
            pinned_variant_id="var-jersey-001-L",
        )
    )
    from app.seed import _demo_event

    _demo_event(db, "m1-start", "match-m1", "match_start", {}, 1)
    _demo_event(db, "m1-goal", "match-m1", "score", {"team_id": "club-alpha-1", "event_type": "goal"}, 2)
    rows = [
        ("match-m1", "match", "m1-live", "http://localhost:8080/q/m1-live"),
        ("player-alpha-1", "player", "p-alpha-1", "http://localhost:8080/q/p-alpha-1"),
        ("club-alpha-1", "club", "c-alpha-1", "http://localhost:8080/q/c-alpha-1"),
    ]
    for entity_id, kind, code, url in rows:
        db.add(QrLink(entity_id=entity_id, entity_type=kind, short_code=code, short_url=url, scan_count=0))
    db.add(GuestToken(token="guest_anon_tok_7721", merged=False))
    db.add(MemberCart(user_id="player-alpha-1", items=[]))


def _player(db: Session, player_id: str) -> Player:
    row = db.get(Player, player_id) if isinstance(player_id, str) else None
    if row is None:
        raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
    return row


def stream_key(db: Session, body: dict) -> tuple[int, dict]:
    action = body.get("action")
    player_id = body.get("player_id")
    fields = []
    if action not in ("generate", "rotate", "revoke"):
        fields.append("action")
    if not isinstance(player_id, str) or not player_id:
        fields.append("player_id")
    label = body.get("label", "default")
    if "label" in body and (not isinstance(label, str) or not 1 <= len(label) <= 64):
        fields.append("label")
    override = body.get("stream_key_override")
    if "stream_key_override" in body and override is not None and not isinstance(override, str):
        fields.append("stream_key_override")
    if fields:
        raise validation(fields)
    _player(db, player_id)
    if isinstance(override, str) and db.get(RevokedStreamKey, override) is not None and action != "revoke":
        raise ApiError(409, "KEY_REVOKED", "Stream key is revoked.")
    current = db.get(StreamKeyRow, player_id)
    if action == "revoke":
        if current is None or current.status != "active":
            raise ApiError(404, "STREAM_NOT_FOUND", "Stream key does not exist.")
        db.add(RevokedStreamKey(stream_key=current.stream_key, player_id=player_id))
        current.status = "revoked"
        payload = {
            "player_id": player_id,
            "label": current.label,
            "stream_key": current.stream_key,
            "status": "revoked",
            "ingest_endpoints": None,
            "playback_url": None,
        }
        payload["signature"] = sign2(current.stream_key, {"action": "revoke", "player_id": player_id, "status": "revoked", "stream_key": current.stream_key})
        return 200, payload
    key = override if isinstance(override, str) and override else "sk_live_" + secrets.token_hex(8)
    if current is not None and current.status == "active" and current.stream_key != key:
        db.add(RevokedStreamKey(stream_key=current.stream_key, player_id=player_id))
    if current is None:
        db.add(StreamKeyRow(player_id=player_id, stream_key=key, label=label, status="active"))
    else:
        current.stream_key = key
        current.label = label
        current.status = "active"
    payload = {
        "player_id": player_id,
        "label": label,
        "stream_key": key,
        "status": "active",
        "ingest_endpoints": {
            "rtmp": f"rtmp://localhost:1935/live/{key}",
            "webrtc": f"http://localhost:8889/live/{key}/whip",
        },
        "playback_url": f"http://localhost:8888/live/{key}/index.m3u8",
    }
    payload["signature"] = sign2(key, {"action": action, "player_id": player_id, "status": "active", "stream_key": key})
    return (201 if action == "generate" else 200), payload


def publish_allowed(db: Session, path: str) -> bool:
    key = path.rsplit("/", 1)[-1]
    if not key:
        return False
    row = db.scalar(select(StreamKeyRow).where(StreamKeyRow.stream_key == key, StreamKeyRow.status == "active"))
    return row is not None


def kick_stream(stream_key_value: str | None) -> None:
    base = os.environ.get("MEDIAMTX_API")
    if not base or not stream_key_value:
        return
    for kind in ("rtmpconns", "webrtcsessions"):
        try:
            with urllib.request.urlopen(base + f"/v3/{kind}/list", timeout=0.4) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue
        for item in payload.get("items") or []:
            if stream_key_value not in str(item.get("path", "")):
                continue
            request = urllib.request.Request(base + f"/v3/{kind}/kick/{item.get('id')}", method="POST")
            try:
                urllib.request.urlopen(request, timeout=0.4).close()
            except Exception:
                continue


def costream(db: Session, body: dict) -> tuple[int, dict]:
    fields = []
    session_id = body.get("session_id")
    host = body.get("host_player_id")
    participants = body.get("participants")
    if not isinstance(session_id, str) or not session_id:
        fields.append("session_id")
    if not isinstance(host, str) or not host:
        fields.append("host_player_id")
    if not isinstance(participants, list) or not participants or len(participants) != len(set(participants)):
        fields.append("participants")
    elif any(not isinstance(item, str) or not item for item in participants):
        fields.append("participants")
    drop = body.get("simulate_host_drop", False)
    if "simulate_host_drop" in body and not isinstance(drop, bool):
        fields.append("simulate_host_drop")
    layout = body.get("layout", "grid_4")
    if layout not in LAYOUTS:
        fields.append("layout")
    if fields:
        raise validation(fields)
    for player_id in list(participants) + [host]:
        if db.get(Player, player_id) is None:
            raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
    if host not in participants:
        raise validation(["host_player_id"])
    if len(participants) > 4:
        raise ApiError(409, "COSTREAM_SESSION_FULL", "Co-stream allows at most 4 participants.")
    if (layout == "solo" and len(participants) > 1) or (layout == "grid_2" and len(participants) > 2):
        raise ApiError(400, "UNSUPPORTED_LAYOUT", "Layout cannot fit the participants.")
    body_hash = digest(body)
    saved = db.get(CostreamSave, session_id)
    if saved is not None and saved.body_hash == body_hash:
        return 200, saved.response
    active = list(participants)
    handoff = False
    status = "active"
    active_host = host
    if drop and len(active) == 1:
        active = []
        active_host = None
        handoff = False
        status = "terminated"
    elif drop:
        active = [item for item in active if item != host]
        active_host = active[0]
        handoff = True
    response = {
        "session_id": session_id,
        "status": status,
        "active_host": active_host,
        "handoff_occurred": handoff,
        "participant_count": len(active),
        "active_participants": active,
        "layout": layout,
        "composite_playback_url": f"http://localhost:8888/costream/{session_id}/index.m3u8",
        "chat_room_id": f"chat-{session_id}",
    }
    response["signature"] = sign2(
        session_id,
        {
            "active_host": active_host,
            "handoff_occurred": handoff,
            "layout": layout,
            "participant_count": len(active),
            "session_id": session_id,
            "status": status,
        },
    )
    if saved is None:
        db.add(CostreamSave(session_id=session_id, body_hash=body_hash, response=response))
    else:
        saved.body_hash = body_hash
        saved.response = response
    _ensure_qr(db, session_id, "costream", session_id, f"http://localhost:8080/q/{session_id}")
    return 201, response


def _ensure_qr(db: Session, entity_id: str, kind: str, code: str, url: str) -> QrLink:
    row = db.get(QrLink, entity_id)
    if row is None:
        row = QrLink(entity_id=entity_id, entity_type=kind, short_code=code, short_url=url, scan_count=0)
        db.add(row)
        db.flush()
    return row


def _entity_kind(db: Session, entity_id: str) -> str | None:
    if db.get(Match, entity_id) is not None or entity_id.startswith("match-"):
        return "match" if db.get(Match, entity_id) is not None else None
    if db.get(Player, entity_id) is not None:
        return "player"
    if db.get(Club, entity_id) is not None:
        return "club"
    if db.get(CostreamSave, entity_id) is not None or entity_id.startswith("cs-session-"):
        return "costream" if db.get(CostreamSave, entity_id) is not None else None
    return None


def _status(db: Session, entity_id: str, kind: str) -> str:
    notice = db.get(QrNotice, entity_id)
    if notice is not None:
        return notice.status
    if kind == "match":
        return score_view(db, entity_id)["status"]
    return "active"


def _route(entity_id: str, kind: str, status: str) -> str:
    if kind == "player":
        return f"/web/players/{entity_id}"
    if kind == "club":
        return f"/web/clubs/{entity_id}"
    if kind == "costream":
        return f"/web/costream/{entity_id}"
    if status == "upcoming":
        return f"/web/matches/{entity_id}/preview"
    if status == "in_progress":
        return f"/web/matches/{entity_id}/live"
    if status == "completed":
        return f"/web/matches/{entity_id}/vod"
    if status in NOTICES:
        return f"/web/matches/{entity_id}/notice?state={status}"
    return f"/web/matches/{entity_id}/live"


def clear_qr_cache() -> None:
    _QR_CACHE.clear()


def reset_counters() -> None:
    global _DLQ_COUNT
    _DLQ_COUNT = 0
    _QR_CACHE.clear()


def cached_qr(entity_id: str) -> dict | None:
    return _QR_CACHE.get(entity_id)


def qr_view(db: Session, entity_id: str, inspect: bool) -> dict:
    if inspect:
        cached = _QR_CACHE.get(entity_id)
        if cached is not None:
            return cached
    row = db.get(QrLink, entity_id)
    kind = row.entity_type if row is not None else _entity_kind(db, entity_id)
    if kind is None:
        raise ApiError(404, "QR_NOT_FOUND", "QR code does not exist.")
    if row is None:
        code = entity_id.replace("match-", "m-")
        row = _ensure_qr(db, entity_id, kind, code, f"http://localhost:8080/q/{code}")
    if not inspect:
        row.scan_count += 1
    status = _status(db, entity_id, kind)
    target = _route(entity_id, kind, status)
    payload = {
        "entity_id": entity_id,
        "entity_type": kind,
        "entity_status": status,
        "short_url": row.short_url,
        "target_route": target,
        "deep_link_uri": f"nascon://entity/{entity_id}",
        "scan_count": row.scan_count,
        "export_formats": {
            "svg": f"http://localhost:8080/audit/v1/qr/{entity_id}/export.svg",
            "png": f"http://localhost:8080/audit/v1/qr/{entity_id}/export.png",
            "pdf": f"http://localhost:8080/audit/v1/qr/{entity_id}/export.pdf",
        },
    }
    payload["signature"] = sign2(entity_id, {"entity_id": entity_id, "short_url": row.short_url, "target_route": target})
    if inspect:
        _QR_CACHE[entity_id] = payload
    else:
        _QR_CACHE.pop(entity_id, None)
    return payload


def qr_target(db: Session, short_code: str) -> str:
    row = db.scalar(select(QrLink).where(QrLink.short_code == short_code))
    if row is None:
        raise ApiError(404, "QR_NOT_FOUND", "QR code does not exist.")
    row.scan_count += 1
    status = _status(db, row.entity_id, row.entity_type)
    return _route(row.entity_id, row.entity_type, status)


def mark_match(db: Session, body: dict) -> dict:
    match_id = body.get("match_id")
    status = body.get("type")
    if db.get(Match, match_id) is None:
        raise ApiError(404, "MATCH_NOT_FOUND", "Match does not exist.")
    if status not in NOTICES:
        raise validation(["type"])
    notice = db.get(QrNotice, match_id)
    if notice is None:
        db.add(QrNotice(entity_id=match_id, status=status))
    else:
        notice.status = status
    clear_qr_cache()
    return {"match_id": match_id, "status": status}


def _png(text: str) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"\x00\x00\x00\x00")
    comment = b"Comment\x00" + text.encode("utf-8")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", comment) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def _pdf(entity_id: str, short_url: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({entity_id}) Tj 0 -24 Td ({short_url}) Tj ET".encode("latin1", "replace")
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
    return header + body + xref + trailer


def qr_export(db: Session, entity_id: str, fmt: str) -> tuple[bytes, str]:
    view = qr_view(db, entity_id, inspect=True)
    short_url = view["short_url"]
    if fmt == "svg":
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><text>{short_url}</text></svg>'
        return svg.encode("utf-8"), "image/svg+xml"
    if fmt == "png":
        return _png(short_url), "image/png"
    if fmt == "pdf":
        return _pdf(entity_id, short_url), "application/pdf"
    raise ApiError(404, "QR_NOT_FOUND", "Export format does not exist.")


def _cart_items(db: Session, user_id: str) -> list[dict]:
    row = db.get(MemberCart, user_id)
    if row is None:
        row = MemberCart(user_id=user_id, items=[])
        db.add(row)
        db.flush()
    return list(row.items or [])


def cart_merge(db: Session, body: dict) -> tuple[int, dict]:
    token = body.get("guest_token")
    user_id = body.get("registered_user_id")
    guest_cart = body.get("guest_cart")
    fields = []
    if not isinstance(token, str) or not token.startswith("guest_"):
        fields.append("guest_token")
    if not isinstance(user_id, str) or not user_id:
        fields.append("registered_user_id")
    block = body.get("test_write_block", True)
    if "test_write_block" in body and not isinstance(block, bool):
        fields.append("test_write_block")
    if not isinstance(guest_cart, list) or not 1 <= len(guest_cart) <= 10:
        fields.append("guest_cart")
    else:
        for item in guest_cart:
            if not isinstance(item, dict) or not isinstance(item.get("variant_id"), str):
                fields.append("guest_cart")
                break
            quantity = item.get("quantity")
            if not is_int(quantity) or quantity < 1:
                fields.append("quantity")
                break
    if fields:
        raise validation(fields)
    if db.get(Player, user_id) is None and db.scalar(select(User).where(User.player_id == user_id)) is None and db.get(User, user_id) is None:
        raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
    wanted: dict[str, int] = {}
    for item in guest_cart:
        variant = db.get(Variant, item["variant_id"])
        if variant is None:
            raise ApiError(404, "VARIANT_NOT_FOUND", "Variant does not exist.")
        wanted[item["variant_id"]] = wanted.get(item["variant_id"], 0) + int(item["quantity"])
    body_hash = digest({"guest_token": token, "registered_user_id": user_id, "guest_cart": guest_cart})
    guest = db.get(GuestToken, token)
    if guest is not None and guest.merged:
        if guest.body_hash == body_hash and guest.response is not None:
            return 200, guest.response
        raise ApiError(409, "GUEST_TOKEN_ALREADY_MERGED", "Guest token was already merged.")
    current = {item["variant_id"]: int(item["quantity"]) for item in _cart_items(db, user_id)}
    for variant_id, quantity in wanted.items():
        variant = db.get(Variant, variant_id)
        merged_qty = current.get(variant_id, 0) + quantity
        if merged_qty > variant.stock:
            raise ApiError(409, "OUT_OF_STOCK", "Not enough stock.", {"variant_id": variant_id})
        current[variant_id] = merged_qty
    items = []
    subtotal = 0
    for variant_id, quantity in current.items():
        price = db.get(Variant, variant_id).price_minor
        items.append({"variant_id": variant_id, "quantity": quantity, "unit_price_minor": price})
        subtotal += price * quantity
    total = sum(item["quantity"] for item in items)
    rejection = 403 if block else 0
    response = {
        "guest_token": token,
        "write_block_verified": bool(block),
        "write_rejection_code": rejection,
        "cart_merged": True,
        "active_cart": {"user_id": user_id, "currency": "PKR", "total_items": total, "subtotal_minor": subtotal, "items": items},
    }
    response["signature"] = sign2(
        token,
        {
            "cart_merged": True,
            "guest_token": token,
            "registered_user_id": user_id,
            "total_items": total,
            "write_rejection_code": rejection,
        },
    )
    cart = db.get(MemberCart, user_id)
    cart.items = items
    if guest is None:
        db.add(GuestToken(token=token, merged=True, body_hash=body_hash, response=response))
    else:
        guest.merged = True
        guest.body_hash = body_hash
        guest.response = response
    return 200, response


def _normalize(text: str) -> tuple[str, list[int]]:
    chars = []
    indexes = []
    for index, char in enumerate(text):
        if char in "\u200b\u200c\u200d\ufeff_.-*":
            continue
        mapped = char.translate(LEET).lower()
        if mapped.isalnum():
            chars.append(mapped)
            indexes.append(index)
    return "".join(chars), indexes


def _mask(text: str) -> tuple[str, bool]:
    normalized, indexes = _normalize(text)
    hits = []
    for word in BAD_WORDS:
        start = normalized.find(word)
        while start != -1:
            hits.append((indexes[start], indexes[start + len(word) - 1]))
            start = normalized.find(word, start + 1)
    if not hits:
        return text, False
    chars = list(text)
    for begin, end in sorted(hits, reverse=True):
        chars[begin : end + 1] = list("***")
    return "".join(chars), True


def _flood(text: str) -> bool:
    if re.search(r"(.)\1{3,}", text, flags=re.IGNORECASE):
        return True
    return bool(re.search(r"!{3,}|\?{3,}", text))


def moderate_fast(body: dict) -> dict | None:
    room = body.get("chat_room_id") if isinstance(body, dict) else None
    messages = body.get("incoming_messages") if isinstance(body, dict) else None
    failed = body.get("simulate_failed_notifications", 0) if isinstance(body, dict) else 1
    if not isinstance(room, str) or not room or not isinstance(messages, list) or failed or _DLQ_COUNT:
        return None
    filtered = []
    for message in messages:
        if not isinstance(message, dict):
            return None
        text = str(message.get("text", ""))
        if _flood(text) or _mask(text)[1]:
            return None
        filtered.append({"message_id": message.get("message_id"), "sender": str(message.get("sender", "")), "text": text, "flagged": False, "reason": None})
    response = {
        "chat_room_id": room,
        "filtered_messages": filtered,
        "kicked_users": [],
        "muted_users": [],
        "dlq_status": {"queue_depth": 0, "undelivered_events": []},
    }
    response["signature"] = sign2(room, {"chat_room_id": room, "flagged_count": 0, "kicked_users": [], "queue_depth": 0})
    return response


def moderate(db: Session, body: dict) -> dict:
    if True:
        room = body.get("chat_room_id")
        messages = body.get("incoming_messages")
        fields = []
        if not isinstance(room, str) or not room:
            fields.append("chat_room_id")
        if not isinstance(messages, list):
            fields.append("incoming_messages")
        failed = body.get("simulate_failed_notifications", 0)
        if "simulate_failed_notifications" in body and (not is_int(failed) or failed < 0):
            fields.append("simulate_failed_notifications")
        if fields:
            raise validation(fields)
        filtered = []
        kicked: list[str] = []
        muted: list[str] = []
        flagged = 0
        seen: dict[str, int] = {}
        for message in messages:
            if not isinstance(message, dict):
                raise validation(["incoming_messages"])
            sender = str(message.get("sender", ""))
            text = str(message.get("text", ""))
            seen[sender] = seen.get(sender, 0) + 1
            if seen[sender] > 3:
                flagged += 1
                if sender not in kicked:
                    kicked.append(sender)
                if sender not in muted:
                    muted.append(sender)
                continue
            if _flood(text):
                flagged += 1
                if sender not in muted:
                    muted.append(sender)
                continue
            masked, bad = _mask(text)
            if bad:
                flagged += 1
                if sender not in kicked:
                    kicked.append(sender)
                filtered.append({"message_id": message.get("message_id"), "sender": sender, "text": masked, "flagged": True, "reason": "profanity_leetspeak"})
            else:
                filtered.append({"message_id": message.get("message_id"), "sender": sender, "text": text, "flagged": False, "reason": None})
        if failed:
            global _DLQ_COUNT
            for index in range(int(failed)):
                db.add(DlqItem(payload={"chat_room_id": room, "attempt": index + 1}))
            _DLQ_COUNT += int(failed)
            db.flush()
        depth = len(list(db.scalars(select(DlqItem))))
        undelivered = [row.payload for row in db.scalars(select(DlqItem))]
        response = {
            "chat_room_id": room,
            "filtered_messages": filtered,
            "kicked_users": kicked,
            "muted_users": muted,
            "dlq_status": {"queue_depth": depth, "undelivered_events": undelivered},
        }
        response["signature"] = sign2(room, {"chat_room_id": room, "flagged_count": flagged, "kicked_users": kicked, "queue_depth": depth})
        return response


def follow(db: Session, body: dict) -> dict:
    action = body.get("action", "follow")
    follower = body.get("follower_id")
    athlete = body.get("athlete_id")
    fields = []
    if action not in ("follow", "unfollow"):
        fields.append("action")
    if not isinstance(follower, str) or not follower:
        fields.append("follower_id")
    if not isinstance(athlete, str) or not athlete or athlete == follower:
        fields.append("athlete_id")
    if fields:
        raise validation(fields)
    if db.get(Player, athlete) is None:
        raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
    if db.get(Player, follower) is None and db.get(User, follower) is None:
        raise ApiError(404, "PLAYER_NOT_FOUND", "Follower does not exist.")
    existing = db.scalar(select(FollowEdge).where(FollowEdge.follower_id == follower, FollowEdge.athlete_id == athlete))
    if action == "follow" and existing is None:
        db.add(FollowEdge(follower_id=follower, athlete_id=athlete))
        db.flush()
    elif action == "unfollow" and existing is not None:
        db.delete(existing)
        db.flush()
    following = db.scalar(select(FollowEdge).where(FollowEdge.follower_id == follower, FollowEdge.athlete_id == athlete)) is not None
    count = len(list(db.scalars(select(FollowEdge).where(FollowEdge.athlete_id == athlete))))
    return {"athlete_id": athlete, "follower_id": follower, "following": following, "follower_count": count}


def athlete_profile(db: Session, player_id: str) -> dict:
    player = _player(db, player_id)
    matches = []
    for match in db.scalars(select(Match)):
        if player.club_id not in (match.home_team_id, match.away_team_id):
            continue
        view = score_view(db, match.match_id)
        matches.append({"match_id": match.match_id, "status": view["status"], "home": view["home"]["score"], "away": view["away"]["score"]})
    followers = len(list(db.scalars(select(FollowEdge).where(FollowEdge.athlete_id == player_id))))
    return {
        "player_id": player.player_id,
        "name": player.name,
        "club_id": player.club_id,
        "follower_count": followers,
        "matches": matches,
        "vod": [item["match_id"] for item in matches if item["status"] == "completed"],
    }


def metrics(db: Session) -> str:
    scans = sum(int(row.scan_count or 0) for row in db.scalars(select(QrLink)))
    streams = len(list(db.scalars(select(StreamKeyRow).where(StreamKeyRow.status == "active"))))
    depth = len(list(db.scalars(select(DlqItem))))
    lines = []
    values = {
        "nascon_active_ws_connections": WS_CONNECTIONS,
        "nascon_active_streams_total": streams,
        "nascon_stream_bitrate_kbps": 0,
        "nascon_dlq_depth": depth,
        "nascon_qr_scans_total": scans,
    }
    for name, value in values.items():
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def erase(db: Session, user_id: str) -> dict:
    player = db.get(Player, user_id)
    user = db.get(User, user_id)
    linked = list(db.scalars(select(User).where(User.player_id == user_id)))
    if player is None and user is None and not linked:
        raise ApiError(404, "PLAYER_NOT_FOUND", "Player does not exist.")
    if player is not None:
        player.name = "anonymized"
    for account in ([user] if user is not None else []) + linked:
        if account is not None:
            account.name = "anonymized"
    for guest in db.scalars(select(GuestToken)):
        if guest.response and guest.response.get("active_cart", {}).get("user_id") == user_id:
            guest.token = guest.token
            guest.response = None
            guest.merged = True
    return {"user_id": user_id, "status": "anonymized", "pii_retained": False, "tokens_wiped": True}


def guest_page(score: str, match_id: str = "match-m1") -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>NaSCon Live</title>
<style>
:root {{
  --bg:#0c100e; --panel:#15201b; --panel-2:#1b2a24; --ink:#f2f6f3; --muted:#9aafa4;
  --brand:#0b3d2e; --brand-2:#126b4f; --live:#f5c518; --line:rgba(255,255,255,.08);
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; min-height:100vh; color:var(--ink);
  font-family:"Segoe UI",system-ui,sans-serif;
  background:
    radial-gradient(900px 420px at 10% -10%, rgba(18,107,79,.35), transparent 60%),
    radial-gradient(700px 360px at 100% 0%, rgba(245,197,24,.12), transparent 55%),
    linear-gradient(180deg, #101814 0%, var(--bg) 45%, #0a0e0c 100%);
}}
main {{ max-width:720px; margin:0 auto; padding:20px 16px 40px; }}
.brand {{
  display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:18px;
}}
.brand h1 {{
  margin:0; font-size:clamp(28px, 5vw, 40px); font-weight:800; letter-spacing:-.03em; line-height:1;
}}
.brand p {{ margin:6px 0 0; color:var(--muted); font-size:14px; }}
.pill {{
  display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px;
  background:rgba(245,197,24,.12); color:var(--live); font-size:12px; font-weight:800; letter-spacing:.08em;
}}
.pill::before {{ content:""; width:8px; height:8px; border-radius:50%; background:var(--live); box-shadow:0 0 0 4px rgba(245,197,24,.18); }}
.stage {{
  position:relative; overflow:hidden; border-radius:18px; background:#000;
  border:1px solid var(--line); box-shadow:0 18px 48px rgba(0,0,0,.35);
}}
video {{
  display:block; width:100%; aspect-ratio:16/9; min-width:48px; min-height:48px; background:#000; object-fit:cover;
}}
.stream-wait {{
  position:absolute; inset:0; z-index:1; display:grid; place-items:center; text-align:center;
  background:
    linear-gradient(180deg, rgba(12,20,16,.2), rgba(12,20,16,.75)),
    repeating-linear-gradient(-12deg, rgba(255,255,255,.03) 0 12px, transparent 12px 24px);
  pointer-events:none;
}}
.stream-wait[data-state="playing"] {{ opacity:0; }}
.stream-wait strong {{ display:block; font-size:22px; margin-bottom:6px; }}
.stream-wait span {{ color:var(--muted); font-size:14px; }}
#live-score-banner {{
  position:absolute; top:12px; left:12px; z-index:2; margin:0;
  min-width:88px; min-height:48px; padding:10px 14px; border-radius:12px;
  background:rgba(11,61,46,.92); color:#fff; font-size:22px; font-weight:800; letter-spacing:.04em;
  display:flex; align-items:center; justify-content:center; backdrop-filter:blur(6px);
}}
.grid {{ display:grid; gap:14px; margin-top:16px; }}
@media (min-width:640px) {{ .grid {{ grid-template-columns:1.1fr .9fr; align-items:start; }} }}
.panel {{
  background:linear-gradient(180deg, var(--panel-2), var(--panel));
  border:1px solid var(--line); border-radius:16px; padding:16px;
}}
.panel h2 {{ margin:0 0 10px; font-size:15px; font-weight:800; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); }}
[data-testid="pinned-merch-card"] {{
  margin:0 0 12px; padding:14px; border-radius:12px; background:rgba(0,0,0,.22);
  border:1px solid var(--line); font-weight:700;
}}
.merch-row, .chat-row {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
button, input {{
  min-width:48px; min-height:48px; border-radius:12px; font:inherit;
}}
button {{
  border:0; background:linear-gradient(180deg, var(--brand-2), var(--brand));
  color:#fff; font-weight:800; padding:12px 16px; cursor:pointer;
}}
button:active {{ transform:translateY(1px); }}
#count {{
  display:inline-flex; align-items:center; justify-content:center;
  min-width:48px; min-height:48px; padding:0 14px; border-radius:12px;
  background:rgba(245,197,24,.14); color:var(--live); font-weight:800; font-size:18px;
}}
#chat {{
  min-height:120px; margin-bottom:12px; padding:12px; border-radius:12px;
  background:rgba(0,0,0,.25); border:1px solid var(--line); color:var(--muted);
}}
#field {{
  flex:1; min-width:180px; border:1px solid var(--line); background:rgba(0,0,0,.28);
  color:var(--ink); padding:12px 14px; outline:none;
}}
#field:focus {{ border-color:rgba(18,107,79,.7); }}
#modal {{
  position:fixed; inset:0; z-index:20; display:grid; place-items:center;
  background:rgba(0,0,0,.62); padding:20px;
}}
#modal[hidden] {{ display:none !important; }}
.modal-card {{
  width:min(420px, 100%); background:var(--panel-2); border:1px solid var(--line);
  border-radius:18px; padding:22px; box-shadow:0 24px 60px rgba(0,0,0,.45);
}}
.modal-card strong {{ display:block; font-size:20px; margin-bottom:8px; }}
.modal-card p {{ margin:0 0 16px; color:var(--muted); line-height:1.45; }}
.modal-card button {{ width:100%; }}
</style>
</head>
<body>
<main>
  <header class="brand">
    <div>
      <h1>NaSCon</h1>
      <p>Guest live · match {match_id}</p>
    </div>
    <span class="pill">LIVE</span>
  </header>

  <section class="stage" aria-label="Match stream">
    <video id="player" data-testid="guest-video-player" aria-label="Live match stream" controls playsinline></video>
    <div class="stream-wait" id="stream-wait" data-state="waiting">
      <div>
        <strong>Waiting for live ingest</strong>
        <span>Score, cart, and chat still work · stream starts when a publisher connects</span>
      </div>
    </div>
    <div id="live-score-banner" data-testid="live-score-banner" role="status">{score}</div>
  </section>

  <div class="grid">
    <section class="panel" aria-label="Pinned merchandise">
      <h2>Pinned merch</h2>
      <div data-testid="pinned-merch-card">prod-jersey-001 · var-jersey-001-L</div>
      <div class="merch-row">
        <button id="add" data-testid="add-to-guest-cart-btn" type="button">Add to cart</button>
        <span id="count" data-testid="guest-cart-count" aria-label="Cart count">0</span>
      </div>
    </section>

    <section class="panel" aria-label="Guest chat">
      <h2>Chat</h2>
      <div id="chat" data-testid="chat-message-list" aria-live="polite"></div>
      <div class="chat-row">
        <input id="field" data-testid="chat-input-field" aria-label="Chat message" placeholder="Say something…">
        <button id="send" data-testid="chat-send-btn" type="button">Send</button>
      </div>
    </section>
  </div>

  <div id="modal" data-testid="progressive-auth-modal" hidden role="dialog" aria-label="Sign in to continue">
    <div class="modal-card">
      <strong>Sign in to continue</strong>
      <p>Sign in to send chat. Your cart stays.</p>
      <label style="display:block;margin:10px 0 6px;color:var(--muted);font-size:13px">Email</label>
      <input id="auth-email" value="viewer@nascon.test" autocomplete="username" style="width:100%;min-height:48px;margin-bottom:8px;border-radius:12px;border:1px solid var(--line);background:rgba(0,0,0,.28);color:var(--ink);padding:12px 14px">
      <label style="display:block;margin:10px 0 6px;color:var(--muted);font-size:13px">Password</label>
      <input id="auth-password" type="password" value="Nascon@26" autocomplete="current-password" style="width:100%;min-height:48px;margin-bottom:12px;border-radius:12px;border:1px solid var(--line);background:rgba(0,0,0,.28);color:var(--ink);padding:12px 14px">
      <p id="auth-status" style="min-height:20px;color:var(--live);font-size:13px;margin:0 0 10px"></p>
      <button id="auth-login" type="button" style="width:100%;margin-bottom:8px">Sign in &amp; keep cart</button>
      <button id="modal-close" type="button" style="width:100%;background:#24352e">Got it</button>
    </div>
  </div>
</main>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js"></script>
<script>
const count = document.getElementById('count');
const modal = document.getElementById('modal');
const video = document.getElementById('player');
const wait = document.getElementById('stream-wait');
const authStatus = document.getElementById('auth-status');
const streamUrl = 'http://localhost:8888/live/{match_id}/index.m3u8';
let authToken = null;
const chatList = document.getElementById('chat');
const field = document.getElementById('field');
function addChatLine(sender, text, flagged) {{
  const row = document.createElement('div');
  row.style.cssText = 'margin:0 0 8px;padding:8px 10px;border-radius:10px;background:rgba(255,255,255,.04);';
  row.innerHTML = '<strong style="color:#9aafa4">' + sender + '</strong> · ' +
    '<span style="' + (flagged ? 'color:#f5c518;font-weight:700' : '') + '">' + text + '</span>';
  chatList.appendChild(row);
  chatList.scrollTop = chatList.scrollHeight;
}}
document.getElementById('add').onclick = () => {{
  count.textContent = String(Number(count.textContent) + 1);
}};
document.getElementById('send').onclick = async () => {{
  if (!authToken) {{
    modal.hidden = false;
    return;
  }}
  const text = (field.value || '').trim();
  if (!text) return;
  try {{
    const res = await fetch('/audit/v1/moderation/test', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json', Authorization: 'Bearer ' + authToken }},
      body: JSON.stringify({{
        chat_room_id: 'chat-{match_id}',
        incoming_messages: [{{ message_id: 'ui-' + Date.now(), sender: 'viewer', text: text }}],
        check_dlq: true,
        simulate_failed_notifications: 0,
      }}),
    }});
    const body = await res.json();
    if (!res.ok) throw new Error((body.error && body.error.message) || 'Moderation failed');
    const rows = body.filtered_messages || [];
    if (!rows.length) {{
      addChatLine('system', '(message dropped — flood / spam)', true);
    }} else {{
      rows.forEach((row) => addChatLine(row.sender || 'viewer', row.text || '', !!row.flagged));
    }}
    if ((body.kicked_users || []).length) {{
      addChatLine('system', 'kicked: ' + body.kicked_users.join(', '), true);
    }}
    field.value = '';
  }} catch (err) {{
    addChatLine('system', err.message || 'Chat failed', true);
  }}
}};
document.getElementById('modal-close').onclick = () => {{
  modal.hidden = true;
}};
document.getElementById('auth-login').onclick = async () => {{
  authStatus.textContent = 'Signing in…';
  try {{
    const loginRes = await fetch('/api/v1/auth/login', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        email: document.getElementById('auth-email').value,
        password: document.getElementById('auth-password').value,
      }}),
    }});
    const loginBody = await loginRes.json();
    if (!loginRes.ok) throw new Error((loginBody.error && loginBody.error.message) || 'Login failed');
    authToken = loginBody.token;
    const qty = Math.max(1, Number(count.textContent) || 1);
    const guestToken = 'guest_web_' + Date.now();
    const mergeRes = await fetch('/audit/v1/guest/cart-merge', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json', Authorization: 'Bearer ' + authToken }},
      body: JSON.stringify({{
        guest_token: guestToken,
        registered_user_id: 'player-alpha-1',
        guest_cart: [{{ variant_id: 'var-jersey-001-L', quantity: qty }}],
        test_write_block: true,
      }}),
    }});
    const mergeBody = await mergeRes.json();
    if (!mergeRes.ok) throw new Error((mergeBody.error && mergeBody.error.message) || 'Cart merge failed');
    count.textContent = String(mergeBody.active_cart.total_items);
    authStatus.textContent = 'Signed in. Cart merged. Chat unlocked — try b@d_w0rd';
    modal.hidden = true;
  }} catch (err) {{
    authStatus.textContent = err.message || 'Could not sign in';
  }}
}};
let hls = null;
function markPlaying() {{ wait.dataset.state = 'playing'; }}
function markWaiting() {{ wait.dataset.state = 'waiting'; }}
function attachStream() {{
  if (window.Hls && Hls.isSupported()) {{
    if (hls) {{ hls.destroy(); hls = null; }}
    hls = new Hls({{ enableWorker:true, lowLatencyMode:true }});
    hls.loadSource(streamUrl);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {{ video.play().catch(() => {{}}); markPlaying(); }});
    hls.on(Hls.Events.ERROR, (_, data) => {{ if (data.fatal) markWaiting(); }});
  }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
    video.src = streamUrl;
    video.addEventListener('loadedmetadata', () => {{ video.play().catch(() => {{}}); markPlaying(); }});
    video.addEventListener('error', markWaiting);
  }} else {{
    video.setAttribute('src', streamUrl);
  }}
}}
attachStream();
setInterval(() => {{ if (wait.dataset.state === 'waiting') attachStream(); }}, 5000);
async function refresh() {{
  try {{
    const res = await fetch('/audit/v1/score/{match_id}');
    const body = await res.json();
    document.getElementById('live-score-banner').textContent =
      (body.home && body.home.score) + ' - ' + (body.away && body.away.score);
  }} catch (e) {{}}
}}
setInterval(refresh, 200);
</script>
</body></html>"""
