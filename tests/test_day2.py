import os

os.environ.setdefault("DAY2_SALT", "day2-test-salt")

from app.core.canonical import sign
from tests.test_api import client, reset

SALT = os.environ["DAY2_SALT"]
GUEST = {"Authorization": "Bearer guest_anon_tok_7721"}


def signed(entity_id, payload, body):
    assert body["signature"] == sign(SALT, entity_id, payload)


def test_stream_key_lifecycle_and_signature():
    reset()
    key = "sk_live_9f83a1b2c3d4e5f6"
    created = client.post(
        "/audit/v1/stream/key",
        json={"action": "generate", "player_id": "player-alpha-1", "label": "OBS-Main-Desk", "stream_key_override": key},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["stream_key"] == key
    assert body["ingest_endpoints"]["rtmp"] == f"rtmp://localhost:1935/live/{key}"
    assert body["playback_url"] == f"http://localhost:8888/live/{key}/index.m3u8"
    signed(key, {"action": "generate", "player_id": "player-alpha-1", "status": "active", "stream_key": key}, body)
    rotated = client.post("/audit/v1/stream/key", json={"action": "rotate", "player_id": "player-alpha-1", "stream_key_override": "sk_live_aabbccddeeff0011"})
    assert rotated.status_code == 200
    revoked = client.post("/audit/v1/stream/key", json={"action": "revoke", "player_id": "player-alpha-1"})
    assert revoked.status_code == 200
    assert revoked.json()["ingest_endpoints"] is None
    missing = client.post("/audit/v1/stream/key", json={"action": "revoke", "player_id": "player-alpha-1"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "STREAM_NOT_FOUND"
    reused = client.post(
        "/audit/v1/stream/key",
        json={"action": "generate", "player_id": "player-alpha-2", "stream_key_override": "sk_live_aabbccddeeff0011"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "KEY_REVOKED"
    bad = client.post("/audit/v1/stream/key", json={"action": "nope", "player_id": "player-alpha-1"})
    assert bad.status_code == 400


def test_costream_handoff_replay_and_limits():
    reset()
    payload = {
        "session_id": "cs-session-101",
        "host_player_id": "player-alpha-1",
        "participants": ["player-alpha-1", "player-alpha-2", "player-beta-1", "player-beta-2"],
        "layout": "grid_4",
        "simulate_host_drop": True,
    }
    first = client.post("/audit/v1/costream/session", json=payload)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["active_host"] == "player-alpha-2"
    assert body["handoff_occurred"] is True
    assert body["participant_count"] == 3
    assert body["active_participants"] == ["player-alpha-2", "player-beta-1", "player-beta-2"]
    signed(
        "cs-session-101",
        {
            "active_host": "player-alpha-2",
            "handoff_occurred": True,
            "layout": "grid_4",
            "participant_count": 3,
            "session_id": "cs-session-101",
            "status": "active",
        },
        body,
    )
    replay = client.post("/audit/v1/costream/session", json=payload)
    assert replay.status_code == 200
    assert replay.json()["participant_count"] == 3
    full = client.post(
        "/audit/v1/costream/session",
        json={
            "session_id": "cs-session-full",
            "host_player_id": "player-alpha-1",
            "participants": ["player-alpha-1", "player-alpha-2", "player-beta-1", "player-beta-2", "player-alpha-1"],
        },
    )
    assert full.status_code == 400
    solo = client.post(
        "/audit/v1/costream/session",
        json={"session_id": "cs-solo", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1", "player-alpha-2"], "layout": "solo"},
    )
    assert solo.status_code == 400
    assert solo.json()["error"]["code"] == "UNSUPPORTED_LAYOUT"
    alone = client.post(
        "/audit/v1/costream/session",
        json={"session_id": "cs-end", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1"], "simulate_host_drop": True},
    )
    assert alone.status_code == 201
    assert alone.json()["status"] == "terminated"
    assert alone.json()["active_host"] is None


def test_qr_routes_exports_and_redirect():
    reset()
    quiet = client.get("/audit/v1/qr/match-m1?inspect=true")
    assert quiet.status_code == 200
    assert quiet.json()["scan_count"] == 0
    assert quiet.json()["target_route"] == "/web/matches/match-m1/live"
    assert quiet.json()["entity_status"] == "in_progress"
    signed(
        "match-m1",
        {"entity_id": "match-m1", "short_url": "http://localhost:8080/q/m1-live", "target_route": "/web/matches/match-m1/live"},
        quiet.json(),
    )
    scanned = client.get("/audit/v1/qr/match-m1")
    assert scanned.json()["scan_count"] == 1
    jump = client.get("/q/m1-live", follow_redirects=False)
    assert jump.status_code == 302
    assert jump.headers["location"] == "/web/matches/match-m1/live"
    svg = client.get("/audit/v1/qr/match-m1/export.svg")
    assert svg.status_code == 200
    assert svg.content.startswith(b"<svg") or svg.content.startswith(b"<?xml")
    assert b"http://localhost:8080/q/m1-live" in svg.content
    png = client.get("/audit/v1/qr/match-m1/export.png")
    assert png.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"http://localhost:8080/q/m1-live" in png.content
    pdf = client.get("/audit/v1/qr/match-m1/export.pdf")
    assert pdf.content.startswith(b"%PDF")
    assert b"match-m1" in pdf.content
    ended = client.post("/audit/v1/events", json={"event_id": "m1-end", "match_id": "match-m1", "type": "match_end", "payload": {}})
    assert ended.status_code == 201, ended.text
    vod = client.get("/audit/v1/qr/match-m1?inspect=true")
    assert vod.json()["target_route"] == "/web/matches/match-m1/vod"
    reset()
    held = client.post("/audit/v1/events", json={"event_id": "m1-hold", "match_id": "match-m1", "type": "postponed", "payload": {}})
    assert held.status_code == 200, held.text
    notice = client.get("/audit/v1/qr/match-m1?inspect=true")
    assert notice.json()["target_route"] == "/web/matches/match-m1/notice?state=postponed"
    missing = client.get("/audit/v1/qr/missing-entity")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "QR_NOT_FOUND"


def test_cart_merge_is_idempotent_and_guest_writes_are_forbidden():
    reset()
    payload = {
        "guest_token": "guest_anon_tok_7721",
        "registered_user_id": "player-alpha-1",
        "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": 1}],
        "test_write_block": True,
    }
    merged = client.post("/audit/v1/guest/cart-merge", json=payload)
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["active_cart"]["total_items"] == 1
    assert body["active_cart"]["subtotal_minor"] == 450000
    assert body["write_rejection_code"] == 403
    signed(
        "guest_anon_tok_7721",
        {
            "cart_merged": True,
            "guest_token": "guest_anon_tok_7721",
            "registered_user_id": "player-alpha-1",
            "total_items": 1,
            "write_rejection_code": 403,
        },
        body,
    )
    replay = client.post("/audit/v1/guest/cart-merge", json=payload)
    assert replay.status_code == 200
    assert replay.json()["active_cart"]["total_items"] == 1
    clash = client.post("/audit/v1/guest/cart-merge", json={**payload, "registered_user_id": "player-alpha-2"})
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "GUEST_TOKEN_ALREADY_MERGED"
    for path in ("/api/v1/chat", "/audit/v1/checkout", "/audit/v1/events"):
        blocked = client.post(path, headers=GUEST, json={"event_id": "g1", "match_id": "match-m1", "type": "score", "payload": {}})
        assert blocked.status_code == 403, path
        assert blocked.json()["error"]["code"] == "GUEST_WRITE_FORBIDDEN"


def test_moderation_masks_kicks_and_mutes():
    reset()
    response = client.post(
        "/audit/v1/moderation/test",
        json={
            "chat_room_id": "chat-cs-session-101",
            "incoming_messages": [
                {"message_id": "msg-1", "sender": "user-guest-1", "text": "Clean sports commentary"},
                {"message_id": "msg-2", "sender": "user-spammer", "text": "Watch this illegal stream b@d_w0rd now!"},
                {"message_id": "msg-3", "sender": "user-flooder", "text": "GOAL! AAAA!!!"},
            ],
            "check_dlq": True,
            "simulate_failed_notifications": 0,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filtered_messages"][1]["text"] == "Watch this illegal stream *** now!"
    assert body["filtered_messages"][1]["reason"] == "profanity_leetspeak"
    assert [item["message_id"] for item in body["filtered_messages"]] == ["msg-1", "msg-2"]
    assert body["kicked_users"] == ["user-spammer"]
    assert body["muted_users"] == ["user-flooder"]
    assert body["dlq_status"]["queue_depth"] == 0
    signed(
        "chat-cs-session-101",
        {"chat_room_id": "chat-cs-session-101", "flagged_count": 2, "kicked_users": ["user-spammer"], "queue_depth": 0},
        body,
    )


def test_metrics_privacy_and_guest_page():
    reset()
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain; version=0.0.4")
    for name in (
        "nascon_active_ws_connections",
        "nascon_active_streams_total",
        "nascon_stream_bitrate_kbps",
        "nascon_dlq_depth",
        "nascon_qr_scans_total",
    ):
        assert name in metrics.text
    erased = client.delete("/audit/v1/privacy/user/player-alpha-1")
    assert erased.status_code == 200
    assert erased.json() == {"user_id": "player-alpha-1", "status": "anonymized", "pii_retained": False, "tokens_wiped": True}
    page = client.get("/web/matches/match-m1/live")
    assert page.status_code == 200
    for test_id in (
        "guest-video-player",
        "live-score-banner",
        "pinned-merch-card",
        "add-to-guest-cart-btn",
        "guest-cart-count",
        "chat-message-list",
        "chat-input-field",
        "chat-send-btn",
        "progressive-auth-modal",
    ):
        assert f'data-testid="{test_id}"' in page.text
    assert "1 - 0" in page.text
    assert 'data-testid="progressive-auth-modal" hidden' in page.text


def test_day2_validation_and_remaining_routes():
    reset()
    from app.db import SessionLocal
    from app.models import Player

    db = SessionLocal()
    db.add(Player(player_id="player-extra", name="Extra", position="df", club_id="club-alpha-1", league_id="league-alpha-1"))
    db.commit()
    db.close()
    assert client.post("/audit/v1/stream/key", json={"action": "generate"}).status_code == 400
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1", "label": ""}).status_code == 400
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1", "stream_key_override": 5}).status_code == 400
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "missing-player"}).status_code == 404
    assert client.post("/audit/v1/costream/session", json={}).status_code == 400
    assert client.post(
        "/audit/v1/costream/session",
        json={"session_id": "cs-bad", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1"], "simulate_host_drop": "yes"},
    ).status_code == 400
    assert client.post(
        "/audit/v1/costream/session",
        json={"session_id": "cs-who", "host_player_id": "player-alpha-1", "participants": ["missing-player"]},
    ).status_code == 404
    assert client.post(
        "/audit/v1/costream/session",
        json={"session_id": "cs-host", "host_player_id": "player-alpha-2", "participants": ["player-alpha-1"]},
    ).status_code == 400
    assert client.post(
        "/audit/v1/costream/session",
        json={
            "session_id": "cs-five",
            "host_player_id": "player-alpha-1",
            "participants": ["player-alpha-1", "player-alpha-2", "player-beta-1", "player-beta-2", "player-extra"],
        },
    ).status_code == 409
    assert client.post(
        "/audit/v1/costream/session",
        json={"session_id": "cs-grid", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1", "player-alpha-2", "player-beta-1"], "layout": "grid_2"},
    ).status_code == 400
    first = {
        "session_id": "cs-replay",
        "host_player_id": "player-alpha-1",
        "participants": ["player-alpha-1", "player-alpha-2"],
        "layout": "grid_2",
    }
    assert client.post("/audit/v1/costream/session", json=first).status_code == 201
    changed = client.post("/audit/v1/costream/session", json={**first, "simulate_host_drop": True})
    assert changed.status_code == 201
    assert changed.json()["handoff_occurred"] is True
    player = client.get("/audit/v1/qr/player-alpha-1?inspect=true")
    assert player.json()["target_route"] == "/web/players/player-alpha-1"
    club = client.get("/audit/v1/qr/club-alpha-1?inspect=true")
    assert club.json()["target_route"] == "/web/clubs/club-alpha-1"
    preview = client.get("/audit/v1/qr/demo-next?inspect=true")
    assert preview.json()["target_route"] == "/web/matches/demo-next/preview"
    room = client.get("/audit/v1/qr/cs-replay?inspect=true")
    assert room.json()["target_route"] == "/web/costream/cs-replay"
    assert client.get("/audit/v1/qr/match-missing").status_code == 404
    assert client.get("/audit/v1/qr/cs-session-missing").status_code == 404
    assert client.get("/q/missing-code").status_code == 404
    assert client.post("/audit/v1/events", json={"match_id": "missing-match", "type": "postponed"}).status_code == 404
    assert client.post("/audit/v1/guest/cart-merge", json={"guest_token": "nope"}).status_code == 400
    assert client.post(
        "/audit/v1/guest/cart-merge",
        json={"guest_token": "guest_new_1", "registered_user_id": "missing", "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": 1}]},
    ).status_code == 404
    assert client.post(
        "/audit/v1/guest/cart-merge",
        json={"guest_token": "guest_new_2", "registered_user_id": "player-alpha-2", "guest_cart": [{"variant_id": "missing-size", "quantity": 1}]},
    ).status_code == 404
    stock = client.post(
        "/audit/v1/guest/cart-merge",
        json={"guest_token": "guest_new_3", "registered_user_id": "player-alpha-2", "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": 9}], "test_write_block": False},
    )
    assert stock.status_code == 409
    assert stock.json()["error"]["code"] == "OUT_OF_STOCK"
    open_cart = client.post(
        "/audit/v1/guest/cart-merge",
        json={"guest_token": "guest_new_4", "registered_user_id": "player-alpha-2", "guest_cart": [{"variant_id": "var-jersey-001-M", "quantity": 1}], "test_write_block": False},
    )
    assert open_cart.status_code == 200
    assert open_cart.json()["write_rejection_code"] == 0
    assert client.post("/audit/v1/moderation/test", json={}).status_code == 400
    assert client.post("/audit/v1/moderation/test", json={"chat_room_id": "room", "incoming_messages": ["bad"], "simulate_failed_notifications": -1}).status_code == 400
    assert client.post("/audit/v1/moderation/test", json={"chat_room_id": "room", "incoming_messages": ["bad"]}).status_code == 400
    burst = client.post(
        "/audit/v1/moderation/test",
        json={
            "chat_room_id": "room-burst",
            "incoming_messages": [{"sender": "spammer", "text": "ok"} for _ in range(4)],
            "simulate_failed_notifications": 2,
        },
    )
    assert burst.status_code == 200
    assert burst.json()["dlq_status"]["queue_depth"] == 2
    assert "spammer" in burst.json()["kicked_users"]
    assert client.delete("/audit/v1/privacy/user/nobody").status_code == 404
    wiped = client.delete("/audit/v1/privacy/user/viewer@nascon.test")
    assert wiped.status_code == 200
    followed = client.post("/audit/v1/follow", json={"follower_id": "player-alpha-2", "athlete_id": "player-alpha-1", "action": "follow"})
    assert followed.status_code == 200, followed.text
    again = client.post("/audit/v1/follow", json={"follower_id": "player-alpha-2", "athlete_id": "player-alpha-1", "action": "follow"})
    assert again.json()["follower_count"] == followed.json()["follower_count"] == 1
    profile = client.get("/audit/v1/athletes/player-alpha-1")
    assert profile.status_code == 200
    assert profile.json()["follower_count"] == 1
    blocked = client.post("/audit/v1/follow", headers=GUEST, json={"follower_id": "player-alpha-2", "athlete_id": "player-alpha-1"})
    assert blocked.status_code == 403
    key = client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1", "stream_key_override": "sk_live_authcheck0001"})
    assert key.status_code == 201
    allowed = client.post("/internal/mediamtx/auth", json={"action": "publish", "path": "live/sk_live_authcheck0001"})
    assert allowed.status_code == 200
    client.post("/audit/v1/stream/key", json={"action": "revoke", "player_id": "player-alpha-1"})
    denied = client.post("/internal/mediamtx/auth", json={"action": "publish", "path": "live/sk_live_authcheck0001"})
    assert denied.status_code == 401
