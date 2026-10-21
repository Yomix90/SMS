"""Types et mixins communs aux modèles."""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# JSONB sous PostgreSQL, JSON (texte) ailleurs
JSONType = sa.JSON().with_variant(JSONB(), "postgresql")
# BIGINT sous PostgreSQL ; INTEGER sous SQLite (seul type autorisé pour l'auto-incrément)
BigIntPK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Datetime toujours stocké et relu en UTC « aware ».

    SQLite ne conserve pas le fuseau : on normalise à l'écriture et on
    réattache UTC à la lecture pour éviter les comparaisons naive/aware.
    """

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PublicIdMixin:
    """UUID public exposé par l'API (l'id entier reste interne)."""

    uuid: Mapped[uuid.UUID] = mapped_column(sa.Uuid, default=uuid.uuid4, unique=True, nullable=False)
