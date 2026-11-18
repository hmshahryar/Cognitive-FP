import secrets
import threading
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names so Alembic migrations stay portable (SQLite -> Postgres).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


_uuid7_lock = threading.Lock()
_uuid7_last = 0  # last (ms << 12 | counter) issued by this process


def uuid7() -> uuid.UUID:
    """Time-ordered UUID (RFC 9562 v7), strictly increasing within this process.

    Rows sort in creation order by id even when timestamps tie (Windows clocks can
    return identical times for writes microseconds apart). Layout: 48-bit unix ms,
    version 7, 12-bit sequence, RFC variant, 62 random bits.
    """
    global _uuid7_last
    with _uuid7_lock:
        stamp = (time.time_ns() // 1_000_000) << 12
        _uuid7_last = max(stamp, _uuid7_last + 1)  # bump the 12-bit sequence on ties
        ms, seq = _uuid7_last >> 12, _uuid7_last & 0xFFF
    rand = secrets.randbits(62)
    value = (ms << 80) | (0x7 << 76) | (seq << 64) | (0b10 << 62) | rand
    return uuid.UUID(int=value)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
