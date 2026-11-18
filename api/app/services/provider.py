"""Provider credentials: key + optional OpenAI-compatible base URL.

A console user does not have to use the official DeepSeek endpoint. The key and
the endpoint travel together, because pointing at another gateway without its key
is useless, and keeping them apart produces ambiguous "which one is active" UI.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import CredentialScope, ProviderCredential
from ..security import encrypt_secret, mask_key, utcnow

PROVIDER = "deepseek"
MAX_BASE_URL = 500
MAX_MODEL = 100
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")


def normalize_base_url(raw: str | None) -> str | None:
    """Validate an endpoint and strip the trailing slash.

    Empty means "official endpoint" -> None (no baseURL is written).
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > MAX_BASE_URL:
        raise ApiError("VALIDATION_ERROR", "Base URL 过长", 422)
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ApiError(
            "VALIDATION_ERROR",
            "Base URL 必须是 http(s):// 开头的完整地址，例如 https://api.deepseek.com/v1",
            422,
        )
    if parts.query or parts.fragment or " " in value:
        raise ApiError("VALIDATION_ERROR", "Base URL 不能带查询参数或空格", 422)
    return value.rstrip("/")


def normalize_model(raw: str | None) -> str | None:
    """Validate a model id (empty means "use the provider's catalogue")."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if not MODEL_RE.match(value):
        raise ApiError(
            "VALIDATION_ERROR",
            "模型名只能包含字母、数字、点、下划线、冒号、斜杠和连字符",
            422,
        )
    return value


def find(db: Session, scope: CredentialScope, user_id: int) -> ProviderCredential | None:
    return (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.scope == scope,
            ProviderCredential.user_id == user_id,
            ProviderCredential.provider == PROVIDER,
        )
        .first()
    )


def save(
    db: Session,
    *,
    scope: CredentialScope,
    user_id: int,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    actor_id: int | None,
    clear_key: bool = False,
) -> tuple[ProviderCredential | None, bool]:
    """Apply a credentials change.

    * `api_key` non-empty        -> upsert the key
    * `base_url` / `model` given -> set (empty clears) endpoint and model id
    * `clear_key=True`           -> delete the record (fall back to the platform key)
    * `api_key == ""` and neither field given -> legacy "clear" from the old UI

    Passing `base_url: ""` resets the endpoint to the official one while keeping
    the key, which is what the "恢复官方地址" button needs.
    """
    key = (api_key or "").strip()
    url_provided = base_url is not None
    model_provided = model is not None
    url = normalize_base_url(base_url)
    model_id = normalize_model(model)
    record = find(db, scope, user_id)

    if clear_key or (api_key == "" and not url_provided and not model_provided):
        if record is not None:
            db.delete(record)
        return None, True

    if key:
        if len(key) < 8:
            raise ApiError("VALIDATION_ERROR", "API Key 格式不正确", 422)
        if record is None:
            record = ProviderCredential(
                scope=scope,
                user_id=user_id,
                provider=PROVIDER,
                ciphertext=encrypt_secret(key),
                key_hint=mask_key(key),
                base_url=url,
                model=model_id,
                created_by=actor_id,
            )
            db.add(record)
        else:
            record.ciphertext = encrypt_secret(key)
            record.key_hint = mask_key(key)
            if url_provided:
                record.base_url = url
            if model_provided:
                record.model = model_id
            record.updated_at = utcnow()
        return record, False

    if url_provided or model_provided:
        if record is None:
            raise ApiError(
                "VALIDATION_ERROR",
                "还没有保存 Key：请先填写 API Key，再单独修改地址或模型",
                422,
            )
        if url_provided:
            record.base_url = url
        if model_provided:
            record.model = model_id
        record.updated_at = utcnow()
        return record, False

    # Nothing supplied: keep the record unchanged.
    return record, False


def view(record: ProviderCredential | None) -> dict:
    return {
        "configured": record is not None,
        "hint": record.key_hint if record else "",
        "base_url": record.base_url if record else None,
        "model": record.model if record else None,
        "updated_at": record.updated_at.isoformat() + "Z" if record else None,
    }


def effective(db: Session, user_id: int) -> tuple[str | None, str | None, str | None]:
    """Return (api_key, base_url, model) for a user: own record wins over platform."""
    own = find(db, CredentialScope.user, user_id)
    platform = find(db, CredentialScope.platform, 0)
    record = own or platform
    if record is None:
        return None, None, None
    from ..security import decrypt_secret

    try:
        return decrypt_secret(record.ciphertext), record.base_url, record.model
    except Exception:  # noqa: BLE001 - a rotated MASTER_KEY must not break provisioning
        import logging

        logging.getLogger("dsh.provider").exception(
            "cannot decrypt provider credential for user %s", user_id
        )
        return None, record.base_url, record.model
