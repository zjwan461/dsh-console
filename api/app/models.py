from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.mysql import BIGINT as MySQLBigInt
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

BigInt = (
    BigInteger().with_variant(MySQLBigInt(unsigned=True), "mysql").with_variant(Integer, "sqlite")
)


def enum_col(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
    )


class Role(enum.StrEnum):
    user = "user"
    admin = "admin"


class UserStatus(enum.StrEnum):
    active = "active"
    disabled = "disabled"
    pending = "pending"


class LedgerReason(enum.StrEnum):
    grant = "grant"
    adjust = "adjust"
    reset = "reset"
    plan_change = "plan_change"
    refund = "refund"


class CredentialScope(enum.StrEnum):
    platform = "platform"
    user = "user"


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    cpu_cores: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=1)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    monthly_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    max_snapshots: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    max_user_images: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(190), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        enum_col(Role, "user_role"), nullable=False, default=Role.user
    )
    status: Mapped[UserStatus] = mapped_column(
        enum_col(UserStatus, "user_status"), nullable=False, default=UserStatus.active
    )
    subdomain: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    plan_id: Mapped[int | None] = mapped_column(
        BigInt, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    disabled_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    plan: Mapped[Plan | None] = relationship(lazy="joined")
    quota: Mapped[UserQuota | None] = relationship(
        back_populates="user", uselist=False, lazy="joined"
    )


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    role: Mapped[Role] = mapped_column(
        enum_col(Role, "invite_role"), nullable=False, default=Role.user
    )
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(43), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(43), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserQuota(Base):
    __tablename__ = "user_quotas"

    user_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        BigInt, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    cpu_cores_override: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    memory_mb_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_minutes_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    seconds_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cycle_start: Mapped[date] = mapped_column(Date, nullable=False)
    cycle_end: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="quota")


class QuotaLedger(Base):
    __tablename__ = "quota_ledger"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delta_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[LedgerReason] = mapped_column(
        enum_col(LedgerReason, "ledger_reason"), nullable=False
    )
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    actor_id: Mapped[int | None] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("scope", "user_id", "provider", name="uk_cred_scope"),)

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    scope: Mapped[CredentialScope] = mapped_column(
        enum_col(CredentialScope, "cred_scope"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInt, nullable=False, default=0)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="deepseek")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Setting(Base):
    __tablename__ = "settings"

    k: Mapped[str] = mapped_column(String(64), primary_key=True)
    v: Mapped[object] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(BigInt, nullable=True, index=True)
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[object | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


__all__ = [
    "AuditLog",
    "CredentialScope",
    "Invite",
    "LedgerReason",
    "Plan",
    "ProviderCredential",
    "QuotaLedger",
    "Role",
    "Setting",
    "User",
    "UserQuota",
    "UserSession",
    "UserStatus",
]
