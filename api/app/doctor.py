"""Preflight checks for a real deployment (`python -m app.cli doctor`).

Every check encodes a failure we actually hit while bringing the console up on a
real host: Redis with a password, MySQL on a non-standard port, a host data path
that does not match the container's, an image missing from the local store, a
weak session secret, Caddy unreachable. Run it after `docker compose up` and
before handing the URL to users.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass

from sqlalchemy import text

from .config import settings
from .db import SessionLocal
from .drivers import DriverError, get_driver
from .models import DshImage, ImageStatus, User
from .redis import redis_ok

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Check:
    level: str
    name: str
    detail: str = ""


def _prod() -> bool:
    return settings.env not in {"dev", "test"}


def check_config() -> list[Check]:
    checks = [
        Check(
            PASS,
            "环境",
            f"ENV={settings.env}  COOKIE_SECURE={settings.cookie_secure}  "
            f"BASE_DOMAIN={settings.base_domain}",
        )
    ]
    if _prod() and not settings.cookie_secure:
        checks.append(
            Check(
                WARN,
                "Cookie 安全",
                "COOKIE_SECURE=false：仅纯 HTTP 验收可用；上 HTTPS 后必须改为 true",
            )
        )
    if settings.base_domain == "dsh.example.com":
        checks.append(Check(WARN, "域名", "仍是占位域名 dsh.example.com，上线前替换"))

    secret = settings.session_secret
    weak = len(secret) < 32 or "insecure" in secret or "change" in secret
    checks.append(
        Check(
            FAIL if weak and _prod() else WARN if weak else PASS,
            "SESSION_SECRET",
            "长度不足或仍是默认值，轮换会强制所有人重新登录" if weak else f"长度 {len(secret)}",
        )
    )
    key = settings.master_key
    weak_key = len(key) < 32
    checks.append(
        Check(
            FAIL if weak_key and _prod() else WARN if weak_key else PASS,
            "MASTER_KEY",
            "未设置或过短：用户模型 Key 将无法安全加密" if weak_key else "已设置",
        )
    )
    return checks


def check_storage() -> list[Check]:
    checks: list[Check] = []
    root = settings.data_root
    if not os.path.isdir(root):
        checks.append(Check(FAIL, "DATA_ROOT", f"{root} 不存在或不是目录"))
        return checks
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".doctor-", delete=True) as fh:
            fh.write(b"ok")
        checks.append(Check(PASS, "DATA_ROOT 可写", root))
    except OSError as exc:
        checks.append(Check(FAIL, "DATA_ROOT 可写", f"{root}: {exc}"))

    try:
        usage = shutil.disk_usage(root)
        free_gb = usage.free / 1024**3
        checks.append(
            Check(
                PASS if free_gb >= 10 else WARN,
                "磁盘余量",
                f"{free_gb:.1f} GB 可用（镜像与快照很占空间）",
            )
        )
    except OSError as exc:
        checks.append(Check(WARN, "磁盘余量", str(exc)))

    if not os.path.isabs(settings.host_data_root):
        checks.append(Check(FAIL, "HOST_DATA_ROOT", "必须是宿主机上的绝对路径"))
    elif settings.host_data_root == settings.data_root:
        checks.append(
            Check(
                WARN,
                "HOST_DATA_ROOT",
                f"与 DATA_ROOT 相同（{settings.data_root}）：只有项目确实位于该路径时才正确",
            )
        )
    else:
        checks.append(
            Check(
                PASS,
                "HOST_DATA_ROOT",
                f"{settings.host_data_root}（容器内无法验证宿主机路径，请确认它存在）",
            )
        )
    return checks


def check_database() -> list[Check]:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            users = db.query(User).count()
            try:
                revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:  # noqa: BLE001 - the table only exists after a migration
                revision = None
    except Exception as exc:  # noqa: BLE001
        return [Check(FAIL, "数据库", f"{type(exc).__name__}: {exc}"[:240])]
    return [
        Check(PASS, "数据库", f"{users} 个用户"),
        Check(
            PASS if revision else WARN,
            "迁移版本",
            str(revision) if revision else "未找到 alembic_version（ENV=dev 用 create_all 时正常）",
        ),
    ]


def check_redis() -> list[Check]:
    if settings.redis_fake:
        return [Check(WARN, "Redis", "REDIS_FAKE=1：进程内假 Redis，仅限本地开发")]
    if redis_ok():
        return [Check(PASS, "Redis", "PING 正常")]
    return [Check(FAIL, "Redis", f"无法连接 {settings.redis_url.split('@')[-1]}（检查密码/端口）")]


def check_docker() -> list[Check]:
    if settings.docker_fake:
        return [Check(WARN, "Docker", "DOCKER_FAKE=1：内存驱动，无法验证真实运行时")]
    try:
        info = get_driver().info()
    except DriverError as exc:
        return [Check(FAIL, "Docker", str(exc)[:240])]

    checks = [
        Check(
            PASS,
            "Docker",
            f"{info.docker_version} · {info.cpu_cores}C · "
            f"{info.mem_total_bytes // 1024**3} GB · {info.containers_running} 个容器运行中",
        )
    ]
    try:
        driver = get_driver()
        names = {net.name for net in driver.client.networks.list()}
        if settings.instance_network in names:
            checks.append(Check(PASS, "实例网络", settings.instance_network))
        else:
            checks.append(
                Check(
                    FAIL,
                    "实例网络",
                    f"缺少 {settings.instance_network}；"
                    "API 启动时会自动创建，若失败请手动 docker network create",
                )
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check(WARN, "实例网络", f"无法列出网络：{exc}"[:200]))

    try:
        with SessionLocal() as db:
            default = db.query(DshImage).filter(DshImage.is_default.is_(True)).first()
            any_ready = db.query(DshImage).filter(DshImage.status == ImageStatus.ready).count()
    except Exception:  # noqa: BLE001
        default, any_ready = None, 0
    if default is None:
        checks.append(Check(WARN, "默认版本", "还没有设为默认的 DSH 版本，用户无法创建实例"))
    else:
        try:
            get_driver().image_info(default.image_ref)
            checks.append(Check(PASS, "默认镜像", default.image_ref))
        except DriverError:
            checks.append(
                Check(
                    FAIL,
                    "默认镜像",
                    f"{default.image_ref} 不在本机镜像库；先 docker pull 或在「版本」页导入",
                )
            )
    if any_ready == 0:
        checks.append(Check(WARN, "可用版本", "没有 ready 状态的版本"))
    return checks


def check_caddy() -> list[Check]:
    if not settings.caddy_enabled:
        return [Check(WARN, "Caddy", "CADDY_ENABLED=0：反向代理需自行配置")]
    import httpx

    url = f"{settings.caddy_admin_url.rstrip('/')}/config/"
    try:
        # trust_env=False: ambient NO_PROXY entries can otherwise break the client
        # constructor (same failure that took down the registry probe).
        response = httpx.get(url, timeout=3, trust_env=False)
    except Exception as exc:  # noqa: BLE001
        return [Check(FAIL, "Caddy", f"{url} 不可达：{exc}"[:240])]
    if response.status_code < 400:
        return [Check(PASS, "Caddy", f"admin API 正常（{response.status_code}）")]
    return [Check(WARN, "Caddy", f"admin API 返回 {response.status_code}")]


def run_doctor() -> list[Check]:
    checks: list[Check] = []
    checks.extend(check_config())
    checks.extend(check_storage())
    checks.extend(check_database())
    checks.extend(check_redis())
    checks.extend(check_docker())
    checks.extend(check_caddy())
    return checks


def print_doctor(checks: list[Check]) -> int:
    width = max(len(item.name) for item in checks) if checks else 0
    for item in checks:
        print(f"[{item.level}] {item.name:<{width}}  {item.detail}")
    fails = sum(1 for item in checks if item.level == FAIL)
    warns = sum(1 for item in checks if item.level == WARN)
    print()
    if fails:
        print(f"{fails} 项失败，{warns} 项警告 —— 修好失败项再上线")
        return 1
    if warns:
        print(f"全部通过，{warns} 项警告（确认可接受即可）")
        return 0
    print("全部通过")
    return 0
