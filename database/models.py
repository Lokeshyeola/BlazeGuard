from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_url: Mapped[str] = mapped_column(String(500), nullable=False)
    request_method: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="GET",
        server_default="GET",
    )

    queue_position: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="WAITING"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )


WAITING_QUEUE_POSITION_INDEX = Index(
    "uq_requests_waiting_queue_position",
    Request.queue_position,
    unique=True,
    sqlite_where=text("status IN ('WAITING', 'PROCESSING')"),
)
REQUEST_IDEMPOTENCY_INDEX = Index(
    "uq_requests_user_idempotency_key",
    Request.user_id,
    Request.idempotency_key,
    unique=True,
    sqlite_where=text("idempotency_key IS NOT NULL"),
)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="ACTIVE",
        index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
