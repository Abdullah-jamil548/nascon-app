from sqlalchemy import JSON, Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Meta(Base):
    __tablename__ = "meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    seeded: Mapped[bool] = mapped_column(Boolean, default=False)


class Sport(Base):
    __tablename__ = "sports"
    sport_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    config: Mapped[dict] = mapped_column(JSON)


class Tenant(Base):
    __tablename__ = "tenants"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    logo: Mapped[str] = mapped_column(String(200), default="")
    primary_color: Mapped[str] = mapped_column(String(20), default="#0B3D2E")
    secondary_color: Mapped[str] = mapped_column(String(20), default="#F5C518")


class User(Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(120), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    league_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    club_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    player_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Club(Base):
    __tablename__ = "clubs"
    club_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(80))
    venue_name: Mapped[str] = mapped_column(String(80), default="")
    latitude: Mapped[float] = mapped_column(default=33.6844)
    longitude: Mapped[float] = mapped_column(default=73.0479)
    socials: Mapped[dict] = mapped_column(JSON, default=dict)
    staff: Mapped[dict] = mapped_column(JSON, default=dict)


class League(Base):
    __tablename__ = "leagues"
    league_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sport_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    points: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tie_break: Mapped[list | None] = mapped_column(JSON, nullable=True)
    qualification_spots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lots_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfer_open: Mapped[str | None] = mapped_column(String(10), nullable=True)
    transfer_close: Mapped[str | None] = mapped_column(String(10), nullable=True)
    max_squad_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_positions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    structure: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    standings_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    team_order: Mapped[list | None] = mapped_column(JSON, nullable=True)


class LeagueTeam(Base):
    __tablename__ = "league_teams"
    league_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class Match(Base):
    __tablename__ = "matches"
    match_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    league_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sport_id: Mapped[str] = mapped_column(String(40))
    home_team_id: Mapped[str] = mapped_column(String(64))
    away_team_id: Mapped[str] = mapped_column(String(64))
    venue_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    starts_at: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pinned_variant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Event(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    sequence: Mapped[int] = mapped_column(Integer)
    signature: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)


class Squad(Base):
    __tablename__ = "squads"
    squad_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    league_id: Mapped[str] = mapped_column(String(64))
    club_id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(80))


class Player(Base):
    __tablename__ = "players"
    player_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[str] = mapped_column(String(40))
    league_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    squad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    club_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    on_loan: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_squad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    loan_end_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    registration_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RosterAudit(Base):
    __tablename__ = "roster_audits"
    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(64))
    league_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


class LiveRosterMatch(Base):
    __tablename__ = "live_roster_matches"
    match_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    league_id: Mapped[str] = mapped_column(String(64))
    home_squad_id: Mapped[str] = mapped_column(String(64))
    away_squad_id: Mapped[str] = mapped_column(String(64))


class Product(Base):
    __tablename__ = "products"
    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(200), default="")


class Variant(Base):
    __tablename__ = "variants"
    variant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(64))
    size: Mapped[str] = mapped_column(String(20), default="")
    color: Mapped[str] = mapped_column(String(20), default="")
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    stock: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class Discount(Base):
    __tablename__ = "discounts"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    percent: Mapped[int] = mapped_column(Integer)


class Order(Base):
    __tablename__ = "orders"
    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    subtotal_minor: Mapped[int] = mapped_column(Integer)
    discount_minor: Mapped[int] = mapped_column(Integer)
    total_minor: Mapped[int] = mapped_column(Integer)
    payment: Mapped[dict] = mapped_column(JSON)
    receipt: Mapped[dict] = mapped_column(JSON)
    body_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    paid_at: Mapped[str] = mapped_column(String(20))


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64))
    variant_id: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_minor: Mapped[int] = mapped_column(Integer)


class SyncAction(Base):
    __tablename__ = "sync_actions"
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    response: Mapped[dict] = mapped_column(JSON)


class Venue(Base):
    __tablename__ = "venues"
    venue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    latitude: Mapped[float] = mapped_column(default=33.6844)
    longitude: Mapped[float] = mapped_column(default=73.0479)
    windows: Mapped[list] = mapped_column(JSON, default=list)


class ScheduledMatch(Base):
    __tablename__ = "scheduled_matches"
    match_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    home_team_id: Mapped[str] = mapped_column(String(64))
    away_team_id: Mapped[str] = mapped_column(String(64))
    venue_id: Mapped[str] = mapped_column(String(64))
    starts_at: Mapped[str] = mapped_column(String(20))
    player_ids: Mapped[list] = mapped_column(JSON, default=list)


class DomainEvent(Base):
    __tablename__ = "domain_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)


class QrLink(Base):
    __tablename__ = "qr_links"
    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    short_code: Mapped[str] = mapped_column(String(64), unique=True)
    short_url: Mapped[str] = mapped_column(String(200))
    scan_count: Mapped[int] = mapped_column(Integer, default=0)


class QrNotice(Base):
    __tablename__ = "qr_notices"
    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))


class StreamKeyRow(Base):
    __tablename__ = "stream_keys"
    player_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stream_key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))


class RevokedStreamKey(Base):
    __tablename__ = "revoked_stream_keys"
    stream_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(64))


class MemberCart(Base):
    __tablename__ = "member_carts"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    items: Mapped[list] = mapped_column(JSON, default=list)


class GuestToken(Base):
    __tablename__ = "guest_tokens"
    token: Mapped[str] = mapped_column(String(80), primary_key=True)
    merged: Mapped[bool] = mapped_column(Boolean, default=False)
    body_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CostreamSave(Base):
    __tablename__ = "costream_saves"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    body_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)


class DlqItem(Base):
    __tablename__ = "dlq_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payload: Mapped[dict] = mapped_column(JSON)


class ClientRequest(Base):
    __tablename__ = "client_requests"
    __table_args__ = (UniqueConstraint("kind", "client_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40))
    client_id: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSON)
