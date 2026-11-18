from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from .security import validate_username


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


class RegisterIn(BaseModel):
    code: str = Field(min_length=4, max_length=64)
    username: str = Field(min_length=3, max_length=31)
    password: str = Field(min_length=8, max_length=200)
    email: EmailStr | None = None
    display_name: str = Field(default="", max_length=64)

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        error = validate_username(value)
        if error:
            raise ValueError(error)
        return value


class PasswordChangeIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = None


class CredentialIn(BaseModel):
    api_key: str | None = Field(default=None, max_length=512)
    # Optional OpenAI-compatible endpoint; `""` resets to the provider default.
    base_url: str | None = Field(default=None, max_length=500)
    # Optional model id for non-DeepSeek catalogues; `""` clears it.
    model: str | None = Field(default=None, max_length=100)
    clear_key: bool = False


class SkillCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    description: str = Field(min_length=1, max_length=500)
    when_to_use: str = Field(default="", max_length=500)
    model_invocable: bool = True
    user_invocable: bool = True
    body: str = Field(default="", max_length=200_000)


class SkillUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    when_to_use: str = Field(default="", max_length=500)
    model_invocable: bool = True
    user_invocable: bool = True
    body: str = Field(default="", max_length=200_000)
    changelog: str = Field(default="", max_length=500)


class SkillGrantIn(BaseModel):
    grants: list[dict] = Field(default_factory=list)


class SkillPrefsIn(BaseModel):
    enabled_ids: list[int] = Field(default_factory=list)


class PluginGrantIn(BaseModel):
    grants: list[dict] = Field(default_factory=list)


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=31)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=64)
    email: EmailStr | None = None
    role: Literal["user", "admin"] = "user"
    plan_id: int | None = None
    must_change_password: bool = True

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        error = validate_username(value)
        if error:
            raise ValueError(error)
        return value


class UserPatchIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = None
    role: Literal["user", "admin"] | None = None
    status: Literal["active", "disabled", "pending"] | None = None
    plan_id: int | None = None
    reason: str | None = Field(default=None, max_length=255)


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


class PlanIn(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    cpu_cores: Decimal = Field(default=Decimal("1.00"), gt=0, le=64)
    memory_mb: int = Field(default=2048, ge=512, le=1048576)
    disk_gb: int = Field(default=20, ge=1, le=100000)
    monthly_minutes: int = Field(default=3600, ge=0, le=5256000)
    max_snapshots: int = Field(default=2, ge=0, le=1000)
    max_user_images: int = Field(default=2, ge=0, le=1000)
    gpu_count: int = Field(default=0, ge=0, le=16)
    is_default: bool = False
    enabled: bool = True
    sort_order: int = 0


class PlanPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    cpu_cores: Decimal | None = Field(default=None, gt=0, le=64)
    memory_mb: int | None = Field(default=None, ge=512, le=1048576)
    disk_gb: int | None = Field(default=None, ge=1, le=100000)
    monthly_minutes: int | None = Field(default=None, ge=0, le=5256000)
    max_snapshots: int | None = Field(default=None, ge=0, le=1000)
    max_user_images: int | None = Field(default=None, ge=0, le=1000)
    gpu_count: int | None = Field(default=None, ge=0, le=16)
    is_default: bool | None = None
    enabled: bool | None = None
    sort_order: int | None = None


class InviteIn(BaseModel):
    role: Literal["user", "admin"] = "user"
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_in_days: int = Field(default=7, ge=1, le=365)
    note: str = Field(default="", max_length=255)


class QuotaPatchIn(BaseModel):
    cpu_cores_override: Decimal | None = Field(default=None, gt=0, le=64)
    memory_mb_override: int | None = Field(default=None, ge=512, le=1048576)
    disk_gb_override: int | None = Field(default=None, ge=1, le=100000)
    monthly_minutes_override: int | None = Field(default=None, ge=0, le=5256000)


class GrantIn(BaseModel):
    minutes: int = Field(ge=-5256000, le=5256000)
    reason: str = Field(min_length=1, max_length=255)


class InstanceCreateIn(BaseModel):
    image_id: int | None = None
    idle_timeout_seconds: int | None = Field(default=None, ge=0, le=86400)


class InstancePatchIn(BaseModel):
    idle_timeout_seconds: int | None = Field(default=None, ge=0, le=86400)


class ImageRegisterIn(BaseModel):
    image_ref: str = Field(min_length=3, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    changelog: str = Field(default="", max_length=2000)
    is_default: bool = False


class ImagePatchIn(BaseModel):
    status: Literal["ready", "disabled"] | None = None
    is_default: bool | None = None
    changelog: str | None = Field(default=None, max_length=2000)


class RemoteImportIn(BaseModel):
    tag: str = Field(min_length=1, max_length=64)
    is_default: bool = False
    changelog: str = Field(default="", max_length=2000)


class SnapshotCreateIn(BaseModel):
    note: str = Field(default="", max_length=200)


class ImageCommitIn(BaseModel):
    name: str = Field(min_length=2, max_length=31)
    note: str = Field(default="", max_length=200)


class VersionSwitchIn(BaseModel):
    image_id: int


class FileMkdirIn(BaseModel):
    path: str = "/"
    name: str = Field(min_length=1, max_length=255)


class FileRenameIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    new_name: str = Field(min_length=1, max_length=255)


class TicketCreateIn(BaseModel):
    subject: str = Field(min_length=2, max_length=200)
    category: Literal["usage", "quota", "bug", "other"] = "other"
    priority: Literal["low", "normal", "high"] = "normal"
    body: str = Field(min_length=1, max_length=10000)


class TicketMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class TicketPatchIn(BaseModel):
    status: Literal["open", "processing", "resolved", "closed"] | None = None
    assignee_id: int | None = None


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body_md: str = Field(default="", max_length=20000)
    level: Literal["info", "warning", "maintenance"] = "info"
    status: Literal["draft", "published", "archived"] = "draft"
    pinned: bool = False
    publish_at: datetime | None = None
    expire_at: datetime | None = None


class SettingsIn(BaseModel):
    base_domain: str | None = Field(default=None, max_length=190)
    console_name: str | None = Field(default=None, max_length=64)
    default_plan_id: int | None = None
    default_idle_minutes: int | None = Field(default=None, ge=0, le=1440)
    allow_user_version_switch: bool | None = None
    allow_user_self_key: bool | None = None
    snapshot_retention_days: int | None = Field(default=None, ge=1, le=3650)
    deleted_user_retention_days: int | None = Field(default=None, ge=1, le=3650)
    upload_max_mb: int | None = Field(default=None, ge=1, le=10240)
    dsh_image_repository: str | None = Field(default=None, max_length=190)
    admin_terminal_enabled: bool | None = None
    maintenance_mode: bool | None = None
    maintenance_message: str | None = Field(default=None, max_length=500)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def user_brief(user: Any) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role.value,
        "status": user.status.value,
        "subdomain": user.subdomain,
        "must_change_password": user.must_change_password,
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
    }
