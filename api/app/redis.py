from __future__ import annotations

import logging

import redis
from redis.exceptions import RedisError

from .config import settings

log = logging.getLogger(__name__)
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        if settings.redis_fake:
            try:
                import fakeredis
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "REDIS_FAKE=1 requires the 'fakeredis' package: pip install fakeredis"
                ) from exc
            _client = fakeredis.FakeRedis(decode_responses=True)
        else:
            _client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
                health_check_interval=30,
            )
    return _client


def redis_ok() -> bool:
    try:
        return bool(get_redis().ping())
    except RedisError:
        return False


def rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Return True when the caller is allowed. Fails open when Redis is down."""
    try:
        client = get_redis()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = pipe.execute()
        return int(count) <= limit
    except RedisError as exc:  # pragma: no cover - exercised only when Redis is absent
        log.warning("rate limiter unavailable: %s", exc)
        return True


def reset_rate_limit(key: str) -> None:
    try:
        get_redis().delete(key)
    except RedisError:
        pass
