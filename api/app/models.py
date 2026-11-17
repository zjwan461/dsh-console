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
    Text,
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


class ImageStatus(enum.StrEnum):
    importing = "importing"
    ready = "ready"
    disabled = "disabled"
    failed = "failed"


class InstanceStatus(enum.StrEnum):
    provisioning = "provisioning"
    starting = "starting"
    running = "running"
    sleeping = "sleeping"
    waking = "waking"
    stopping = "stopping"
    stopped = "stopped"
    error = "error"
    deleting = "deleting"
    deleted = "deleted"


class DshImage(Base):
    __tablename__ = "dsh_images"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    image_ref: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    digest: Mapped[str | None] = mapped_column(String(71), unique=True, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    arch: Mapped[str] = mapped_column(String(16), nullable=False, default="amd64")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="pull")
    status: Mapped[ImageStatus] = mapped_column(
        enum_col(ImageStatus, "image_status"), nullable=False, default=ImageStatus.importing
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[object | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    container_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    container_name: Mapped[str] = mapped_column(String(64), nullable=False)
    image_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("dsh_images.id", ondelete="RESTRICT"), nullable=False
    )
    dsh_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[InstanceStatus] = mapped_column(
        enum_col(InstanceStatus, "instance_status"),
        nullable=False,
        default=InstanceStatus.provisioning,
        index=True,
    )
    status_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subdomain: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    cpu_cores: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    data_path: Mapped[str] = mapped_column(String(512), nullable=False)
    workspace_path: Mapped[str] = mapped_column(String(512), nullable=False)
    wake_key: Mapped[str] = mapped_column(String(43), unique=True, nullable=False)
    idle_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    health: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_image_id: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    image: Mapped[DshImage] = relationship(lazy="joined")


class InstanceEvent(Base):
    __tablename__ = "instance_events"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    actor_id: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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
    # Optional OpenAI-compatible endpoint (LiteLLM / vLLM / a proxy). Empty means
    # the provider's official host, i.e. no baseURL is written into settings.yaml.
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
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


class SnapshotStatus(enum.StrEnum):
    creating = "creating"
    ready = "ready"
    restoring = "restoring"
    failed = "failed"


class UserImageStatus(enum.StrEnum):
    committing = "committing"
    ready = "ready"
    failed = "failed"


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


class UsageBucket(Base):
    __tablename__ = "usage_bucket"
    __table_args__ = (
        UniqueConstraint("instance_id", "bucket_start", name="uk_usage_instance_hour"),
    )

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInt, nullable=False, index=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cpu_seconds: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    mem_mb_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    disk_bytes_max: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    net_rx_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    net_tx_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instance_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[SnapshotStatus] = mapped_column(
        enum_col(SnapshotStatus, "snapshot_status"), nullable=False, default=SnapshotStatus.creating
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dsh_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    image_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    manifest: Mapped[object | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserImage(Base):
    __tablename__ = "user_images"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uk_uimages_user_name"),)

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instance_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    image_ref: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    base_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[UserImageStatus] = mapped_column(
        enum_col(UserImageStatus, "user_image_status"),
        nullable=False,
        default=UserImageStatus.committing,
    )
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NodeMetric(Base):
    __tablename__ = "node_metric"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    cpu_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    load1: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    load5: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    load15: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    mem_total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mem_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    swap_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    net_rx_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    net_tx_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    containers_running: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disks: Mapped[object | None] = mapped_column(JSON, nullable=True)
    gpu: Mapped[object | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    assignee_id: Mapped[int | None] = mapped_column(BigInt, nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInt, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(BigInt, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[object | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


__all__ = [
    "Announcement",
    "AuditLog",
    "CredentialScope",
    "DshImage",
    "ImageStatus",
    "Instance",
    "InstanceEvent",
    "InstanceStatus",
    "Invite",
    "LedgerReason",
    "NodeMetric",
    "Plan",
    "ProviderCredential",
    "QuotaLedger",
    "Role",
    "Setting",
    "Snapshot",
    "SnapshotStatus",
    "Ticket",
    "TicketMessage",
    "UsageBucket",
    "User",
    "UserImage",
    "UserImageStatus",
    "UserQuota",
    "UserSession",
    "UserStatus",
]
