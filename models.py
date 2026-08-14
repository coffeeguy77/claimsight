"""Database models for the claims assessment platform."""
from __future__ import annotations

import datetime as dt
import enum
import os
import secrets
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///./local.db")
    # Railway hands out postgres:// which SQLAlchemy 2.x no longer accepts.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


ENGINE = create_engine(_database_url(), pool_pre_ping=True, pool_recycle=1800)
SessionLocal = sessionmaker(bind=ENGINE, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class JobStatus(str, enum.Enum):
    draft = "draft"
    ingesting = "ingesting"
    ready = "ready"
    valuing = "valuing"
    valued = "valued"
    failed = "failed"


class ValuationStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    complete = "complete"
    failed = "failed"
    overridden = "overridden"


class Organisation(Base):
    __tablename__ = "organisations"
    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(200), nullable=False)
    abn = Column(String(50), default="")
    created_at = Column(DateTime(timezone=True), default=_now)

    users = relationship("User", back_populates="organisation")
    jobs = relationship("Job", back_populates="organisation")


class User(Base):
    __tablename__ = "users"
    id = Column(String(32), primary_key=True, default=_uuid)
    organisation_id = Column(String(32), ForeignKey("organisations.id"), nullable=False)
    email = Column(String(320), nullable=False, unique=True, index=True)
    name = Column(String(200), default="")
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="owner")  # owner | assessor | viewer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    organisation = relationship("Organisation", back_populates="users")


class SessionToken(Base):
    __tablename__ = "session_tokens"
    token = Column(String(64), primary_key=True, default=lambda: secrets.token_urlsafe(32))
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    expires_at = Column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(32), primary_key=True, default=_uuid)
    organisation_id = Column(String(32), ForeignKey("organisations.id"), nullable=False, index=True)
    created_by_id = Column(String(32), ForeignKey("users.id"))

    reference = Column(String(120), default="")        # assessor's own job number
    claim_reference = Column(String(120), default="")  # insurer's claim number
    insured_name = Column(String(200), default="")
    insurer = Column(String(200), default="")
    site_address = Column(Text, default="")
    peril = Column(String(100), default="Water")
    date_of_loss = Column(String(40), default="")
    policy_excess = Column(Float, default=0.0)
    notes = Column(Text, default="")

    # Settlement basis. When False the claim settles at full replacement cost
    # (new for old) and depreciation is ignored in all totals and reports.
    # Per-item indemnity figures are still stored, so this is reversible.
    apply_depreciation = Column(Boolean, default=True, nullable=False)

    status = Column(Enum(JobStatus), default=JobStatus.draft)
    status_detail = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    organisation = relationship("Organisation", back_populates="jobs")
    items = relationship(
        "Item", back_populates="job", cascade="all, delete-orphan", order_by="Item.sort_order"
    )
    photos = relationship("Photo", back_populates="job", cascade="all, delete-orphan")

    @property
    def basis_label(self) -> str:
        return "Indemnity (depreciated)" if self.apply_depreciation else "Replacement (new for old)"

    @property
    def totals(self) -> dict[str, float]:
        replacement = indemnity = 0.0
        priced = 0
        for item in self.items:
            if item.excluded:
                continue
            quantity = item.quantity or 1
            if item.replacement_value is not None:
                replacement += item.replacement_value * quantity
                priced += 1
            if item.indemnity_value is not None:
                indemnity += item.indemnity_value * quantity

        # The settlement basis decides which column the claim is paid on.
        gross = indemnity if self.apply_depreciation else replacement
        spend = sum(i.cost_usd or 0.0 for i in self.items)
        return {
            "replacement": round(replacement, 2),
            "indemnity": round(indemnity, 2),
            "gross": round(gross, 2),
            "priced": priced,
            "count": sum(1 for i in self.items if not i.excluded),
            "settlement": round(max(gross - (self.policy_excess or 0.0), 0.0), 2),
            "research_cost": round(spend, 4),
            "searches": sum(i.search_count or 0 for i in self.items),
        }


class Item(Base):
    __tablename__ = "items"
    id = Column(String(32), primary_key=True, default=_uuid)
    job_id = Column(String(32), ForeignKey("jobs.id"), nullable=False, index=True)
    sort_order = Column(Integer, default=0)

    description = Column(Text, nullable=False)
    quantity = Column(Integer, default=1)
    make = Column(String(200), default="")
    model = Column(String(200), default="")
    serial = Column(String(200), default="")
    location = Column(String(200), default="")
    cause_of_damage = Column(String(120), default="")
    photo_series = Column(String(40), default="")
    photo_refs = Column(String(200), default="")  # original "21, 22, 23" text
    assessor_note = Column(Text, default="")

    # Valuation output
    valuation_status = Column(Enum(ValuationStatus), default=ValuationStatus.pending)
    category = Column(String(120), default="")
    identified_as = Column(Text, default="")       # what the AI decided the item is
    replacement_value = Column(Float)              # AUD, new equivalent
    indemnity_value = Column(Float)                # AUD, after depreciation
    depreciation_rate = Column(Float)              # 0..1 applied
    estimated_age_years = Column(Float)
    effective_life_years = Column(Float)
    confidence = Column(String(20), default="")    # high | medium | low
    valuation_notes = Column(Text, default="")
    sources = Column(Text, default="")             # newline-separated URLs
    error = Column(Text, default="")

    # What this line cost to research, so spend is never a surprise.
    cost_usd = Column(Float, default=0.0)
    search_count = Column(Integer, default=0)
    valuation_model = Column(String(80), default="")

    # Assessor control
    excluded = Column(Boolean, default=False)
    manual_override = Column(Boolean, default=False)
    valued_at = Column(DateTime(timezone=True))

    job = relationship("Job", back_populates="items")

    @property
    def source_list(self) -> list[str]:
        return [s for s in (self.sources or "").splitlines() if s.strip()]

    @property
    def photo_key_list(self) -> list[str]:
        return [f"{self.photo_series}:{n}" for n in (self.photo_refs or "").split(",") if n.strip()]


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (UniqueConstraint("job_id", "series", "number", name="uq_photo_ref"),)

    id = Column(String(32), primary_key=True, default=_uuid)
    job_id = Column(String(32), ForeignKey("jobs.id"), nullable=False, index=True)
    series = Column(String(40), default="initial")
    number = Column(Integer, nullable=False)
    page = Column(Integer, default=0)
    caption = Column(Text, default="")
    content_type = Column(String(60), default="image/jpeg")
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    data = Column(LargeBinary, nullable=False)

    job = relationship("Job", back_populates="photos")

    @property
    def key(self) -> str:
        return f"{self.series}:{self.number}"


# Columns added after the first release. Applied on startup so an existing
# deployment picks them up without a separate migration step.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("jobs", "apply_depreciation", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("items", "cost_usd", "DOUBLE PRECISION DEFAULT 0"),
    ("items", "search_count", "INTEGER DEFAULT 0"),
    ("items", "valuation_model", "VARCHAR(80) DEFAULT ''"),
]


def _apply_additive_migrations() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(ENGINE)
    tables = set(inspector.get_table_names())
    with ENGINE.begin() as conn:
        for table, column, ddl in _ADDITIVE_COLUMNS:
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    Base.metadata.create_all(ENGINE)
    _apply_additive_migrations()


def get_session() -> Session:
    return SessionLocal()
