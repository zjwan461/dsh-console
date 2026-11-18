"""Platform skill repository: validate, store, grant, materialise.

The console is the source of truth for *platform* skills only. Image built-ins
(`/opt/dsh-skills`) and skills a user drops into a workspace are deliberately out
of scope: we never read, rewrite or delete them.

Materialisation writes the user's enabled skills into
`<instance.data_path>/skills/<name>/` (container `/data/dsh/skills`), which DSH
watches and hot-reloads. A manifest records what we own so a user's own
directories are never touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import ApiError
from ..models import (
    GrantScope,
    Instance,
    InstanceStatus,
    Skill,
    SkillGrant,
    SkillRevision,
    SkillStatus,
    User,
    UserSkillPref,
)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_FILES = 300
MAX_BYTES = 20 * 1024 * 1024
MAX_SKILL_MD = 1024 * 1024
MANIFEST_NAME = "skills-manifest.json"
MANAGED_DIR = ".console"


# ── skill.md ────────────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ApiError("SKILL_INVALID", "SKILL.md 缺少 frontmatter（--- 开头）", 422)
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ApiError("SKILL_INVALID", "SKILL.md 的 frontmatter 未闭合", 422)
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise ApiError("SKILL_INVALID", f"frontmatter 不是合法 YAML：{exc}", 422) from exc
    if not isinstance(meta, dict):
        raise ApiError("SKILL_INVALID", "frontmatter 必须是映射", 422)
    return meta, parts[2].lstrip("\n")


def render_skill_md(
    *,
    name: str,
    description: str,
    when_to_use: str,
    model_invocable: bool,
    user_invocable: bool,
    body: str,
) -> str:
    meta = {
        "name": name,
        "description": description,
        "whenToUse": when_to_use or "",
        "invocation": {
            "modelInvocable": bool(model_invocable),
            "userInvocable": bool(user_invocable),
        },
    }
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{head}\n---\n\n{body.strip()}\n"


def validate_meta(meta: dict) -> dict:
    name = str(meta.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ApiError(
            "SKILL_INVALID",
            "技能名只能是小写字母、数字、连字符，且以字母或数字开头（≤64 字符）",
            422,
        )
    description = str(meta.get("description") or "").strip()
    if not description:
        raise ApiError("SKILL_INVALID", "description 不能为空", 422)
    if len(description) > 500:
        raise ApiError("SKILL_INVALID", "description 不能超过 500 字", 422)
    when = str(meta.get("whenToUse") or "").strip()
    if len(when) > 500:
        raise ApiError("SKILL_INVALID", "whenToUse 不能超过 500 字", 422)
    invocation = meta.get("invocation") if isinstance(meta.get("invocation"), dict) else {}
    return {
        "name": name,
        "description": description,
        "when_to_use": when,
        "model_invocable": bool(invocation.get("modelInvocable", True)),
        "user_invocable": bool(invocation.get("userInvocable", True)),
    }


# ── tree / archive ──────────────────────────────────────────────────────────


def _safe_rel(name: str) -> str:
    if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
        raise ApiError("SKILL_INVALID", f"非法路径：{name}", 422)
    return name


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_tree(root: Path) -> tuple[dict, list[dict], int]:
    """Validate a skill directory and return (meta, files, size)."""
    if not root.is_dir():
        raise ApiError("SKILL_INVALID", "技能必须是一个目录", 422)
    files: list[dict] = []
    total = 0
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if name.startswith("."):
                continue
            full = Path(current) / name
            rel = full.relative_to(root).as_posix()
            _safe_rel(rel)
            if full.is_symlink():
                raise ApiError("SKILL_INVALID", f"不允许符号链接：{rel}", 422)
            size = full.stat().st_size
            total += size
            if len(files) >= MAX_FILES:
                raise ApiError("SKILL_INVALID", f"文件数超过 {MAX_FILES}", 422)
            if total > MAX_BYTES:
                raise ApiError("SKILL_INVALID", "技能超过 20 MB", 422)
            files.append({"path": rel, "size": size, "sha256": _hash_file(full)})
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        raise ApiError("SKILL_INVALID", "目录里必须有 SKILL.md", 422)
    if skill_md.stat().st_size > MAX_SKILL_MD:
        raise ApiError("SKILL_INVALID", "SKILL.md 超过 1 MB", 422)
    meta, _body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    files.sort(key=lambda item: item["path"])
    return validate_meta(meta), files, total


def _extract_zip(archive: zipfile.ZipFile, dest: Path) -> None:
    total = 0
    for info in archive.infolist():
        name = _safe_rel(info.filename)
        target = dest / name
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        # Unix mode is in the high 16 bits; reject symlinks and devices.
        mode = (info.external_attr >> 16) & 0o170000
        if mode and mode not in (0o100000, 0o040000):
            raise ApiError("SKILL_INVALID", f"压缩包包含非普通文件：{name}", 422)
        total += info.file_size
        if total > MAX_BYTES:
            raise ApiError("SKILL_INVALID", "压缩包解压后超过 20 MB", 422)
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _extract_tar(archive: tarfile.TarFile, dest: Path) -> None:
    total = 0
    for member in archive.getmembers():
        name = _safe_rel(member.name)
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise ApiError("SKILL_INVALID", f"压缩包包含链接或特殊文件：{name}", 422)
        target = dest / name
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        total += member.size
        if total > MAX_BYTES:
            raise ApiError("SKILL_INVALID", "压缩包解压后超过 20 MB", 422)
        target.parent.mkdir(parents=True, exist_ok=True)
        src = archive.extractfile(member)
        if src is not None:
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def unpack_archive(filename: str, fileobj, dest: Path) -> None:
    lower = filename.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(fileobj) as archive:
            _extract_zip(archive, dest)
    elif lower.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(fileobj=fileobj, mode="r:*") as archive:
            _extract_tar(archive, dest)
    elif lower.endswith(".md"):
        text = fileobj.read(MAX_SKILL_MD + 1)
        if len(text) > MAX_SKILL_MD:
            raise ApiError("SKILL_INVALID", "SKILL.md 超过 1 MB", 422)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_bytes(text)
    else:
        raise ApiError("SKILL_INVALID", "只支持 .zip / .tar.gz / .md", 422)


def locate_skill_root(root: Path) -> Path:
    if (root / "SKILL.md").is_file():
        return root
    children = [p for p in root.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() and (children[0] / "SKILL.md").is_file():
        return children[0]
    if len(children) == 1 and children[0].suffix == ".md":
        # A single-file skill: wrap it so downstream code always sees a directory.
        wrapped = root / "_wrapped"
        wrapped.mkdir()
        shutil.move(str(children[0]), str(wrapped / "SKILL.md"))
        return wrapped
    raise ApiError("SKILL_INVALID", "找不到 SKILL.md（支持单目录或根目录直接放）", 422)


# ── storage ─────────────────────────────────────────────────────────────────


def skills_root() -> Path:
    path = Path(settings.data_root) / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _revision_dir(skill_id: int, rev: int) -> Path:
    return skills_root() / str(skill_id) / str(rev)


def next_revision(db: Session, skill_id: int) -> int:
    latest = (
        db.query(SkillRevision.rev)
        .filter(SkillRevision.skill_id == skill_id)
        .order_by(SkillRevision.rev.desc())
        .first()
    )
    return (latest[0] + 1) if latest else 1


def store_revision(
    db: Session,
    skill: Skill,
    source: Path,
    *,
    changelog: str = "",
    actor_id: int | None = None,
) -> SkillRevision:
    meta, files, size = scan_tree(source)
    rev = next_revision(db, skill.id)
    target = _revision_dir(skill.id, rev)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    for entry in files:
        full = target / entry["path"]
        full.chmod(0o644)
    checksum = hashlib.sha256(
        json.dumps(
            {"meta": meta, "files": files}, sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()
    revision = SkillRevision(
        skill_id=skill.id,
        rev=rev,
        storage_path=str(target),
        checksum=checksum,
        size_bytes=size,
        manifest={"meta": meta, "files": files},
        changelog=changelog[:500],
        created_by=actor_id,
    )
    db.add(revision)
    db.flush()
    skill.display_name = skill.display_name or meta["name"]
    skill.description = meta["description"]
    skill.when_to_use = meta["when_to_use"]
    skill.model_invocable = meta["model_invocable"]
    skill.user_invocable = meta["user_invocable"]
    skill.current_revision_id = revision.id
    db.commit()
    db.refresh(revision)
    return revision


def current_revision(skill: Skill, db: Session) -> SkillRevision | None:
    if not skill.current_revision_id:
        return None
    return db.get(SkillRevision, skill.current_revision_id)


# ── CRUD ────────────────────────────────────────────────────────────────────


def _ensure_name_free(db: Session, name: str, exclude_id: int | None = None) -> None:
    query = db.query(Skill.id).filter(Skill.name == name)
    if exclude_id:
        query = query.filter(Skill.id != exclude_id)
    if query.first():
        raise ApiError("SKILL_NAME_TAKEN", "技能名已被占用", 409)


def _write_md_tree(staging: Path, payload: dict) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "SKILL.md").write_text(
        render_skill_md(
            name=payload["name"],
            description=payload["description"],
            when_to_use=payload.get("when_to_use", ""),
            model_invocable=payload.get("model_invocable", True),
            user_invocable=payload.get("user_invocable", True),
            body=payload.get("body", ""),
        ),
        encoding="utf-8",
    )


def create_skill(db: Session, payload: dict, actor_id: int | None) -> Skill:
    _ensure_name_free(db, payload["name"])
    skill = Skill(
        name=payload["name"],
        display_name=payload.get("display_name") or payload["name"],
        description=payload["description"],
        when_to_use=payload.get("when_to_use", ""),
        model_invocable=payload.get("model_invocable", True),
        user_invocable=payload.get("user_invocable", True),
        status=SkillStatus.draft,
        source=payload.get("source", "create"),
        created_by=actor_id,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / skill.name
        _write_md_tree(staging, payload)
        store_revision(db, skill, staging, changelog="初始版本", actor_id=actor_id)
    return skill


def update_skill(db: Session, skill: Skill, payload: dict, actor_id: int | None) -> Skill:
    name = payload.get("name", skill.name)
    if name != skill.name:
        _ensure_name_free(db, name, exclude_id=skill.id)
    skill.name = name
    skill.display_name = payload.get("display_name") or skill.display_name
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / name
        _write_md_tree(
            staging,
            {
                "name": name,
                "description": payload["description"],
                "when_to_use": payload.get("when_to_use", ""),
                "model_invocable": payload.get("model_invocable", True),
                "user_invocable": payload.get("user_invocable", True),
                "body": payload.get("body", ""),
            },
        )
        store_revision(
            db,
            skill,
            staging,
            changelog=payload.get("changelog", "编辑"),
            actor_id=actor_id,
        )
    # An edit to a published skill stays published: the new revision is the
    # current one, so the next reconcile hot-reloads it. A draft stays a draft.
    db.commit()
    return skill


def import_skill(
    db: Session,
    filename: str,
    fileobj,
    actor_id: int | None,
    *,
    source: str = "upload",
) -> Skill:
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        unpack_archive(filename, fileobj, staging)
        root = locate_skill_root(staging)
        meta, _files, _size = scan_tree(root)
        _ensure_name_free(db, meta["name"])
        skill = Skill(
            name=meta["name"],
            display_name=meta["name"],
            description=meta["description"],
            when_to_use=meta["when_to_use"],
            model_invocable=meta["model_invocable"],
            user_invocable=meta["user_invocable"],
            status=SkillStatus.draft,
            source=source,
            created_by=actor_id,
        )
        db.add(skill)
        db.commit()
        db.refresh(skill)
        store_revision(db, skill, root, changelog="导入", actor_id=actor_id)
        return skill


def publish_skill(db: Session, skill: Skill) -> Skill:
    if not skill.current_revision_id:
        raise ApiError("SKILL_INVALID", "技能还没有内容", 422)
    skill.status = SkillStatus.published
    db.commit()
    return skill


def disable_skill(db: Session, skill: Skill) -> Skill:
    skill.status = SkillStatus.disabled
    db.commit()
    return skill


def rollback_skill(db: Session, skill: Skill, revision_id: int) -> Skill:
    revision = db.get(SkillRevision, revision_id)
    if revision is None or revision.skill_id != skill.id:
        raise ApiError("SKILL_NOT_FOUND", "版本不存在", 404)
    skill.current_revision_id = revision.id
    skill.description = (revision.manifest or {}).get("meta", {}).get("description", "")
    skill.status = SkillStatus.published
    db.commit()
    return skill


def set_grants(db: Session, skill: Skill, grants: list[dict]) -> None:
    db.query(SkillGrant).filter(SkillGrant.skill_id == skill.id).delete()
    seen: set[tuple[str, int]] = set()
    for grant in grants:
        try:
            scope = GrantScope(grant["scope"])
        except (KeyError, ValueError) as exc:
            raise ApiError("GRANT_INVALID", "开放范围必须是 all / plan / user", 422) from exc
        target = int(grant.get("target_id") or 0)
        if scope == GrantScope.all:
            target = 0
        if (scope, target) in seen:
            continue
        seen.add((scope, target))
        db.add(SkillGrant(skill_id=skill.id, scope=scope, target_id=target))
    db.commit()


# ── visibility / preferences ────────────────────────────────────────────────


def _granted_skill_ids(db: Session, user: User) -> set[int]:
    query = db.query(SkillGrant.skill_id).filter(
        or_(
            SkillGrant.scope == GrantScope.all,
            (SkillGrant.scope == GrantScope.plan) & (SkillGrant.target_id == (user.plan_id or 0)),
            (SkillGrant.scope == GrantScope.user) & (SkillGrant.target_id == user.id),
        )
    )
    return {row[0] for row in query.all()}


def allowed_skills(db: Session, user: User) -> list[Skill]:
    ids = _granted_skill_ids(db, user)
    if not ids:
        return []
    return (
        db.query(Skill)
        .filter(
            Skill.id.in_(ids),
            Skill.status == SkillStatus.published,
            Skill.current_revision_id.isnot(None),
        )
        .order_by(Skill.name)
        .all()
    )


def enabled_skill_ids(db: Session, user: User) -> set[int]:
    allowed = {skill.id for skill in allowed_skills(db, user)}
    if not allowed:
        return set()
    prefs = {
        row.skill_id: row.enabled
        for row in db.query(UserSkillPref).filter(UserSkillPref.user_id == user.id).all()
    }
    return {sid for sid in allowed if prefs.get(sid, True)}


def set_user_prefs(db: Session, user: User, enabled_ids: set[int]) -> None:
    allowed = {skill.id for skill in allowed_skills(db, user)}
    unknown = enabled_ids - allowed
    if unknown:
        raise ApiError("SKILL_NOT_ALLOWED", "包含未开放的技能", 403)
    for sid in allowed:
        row = (
            db.query(UserSkillPref)
            .filter(UserSkillPref.user_id == user.id, UserSkillPref.skill_id == sid)
            .first()
        )
        enabled = sid in enabled_ids
        if row is None:
            db.add(UserSkillPref(user_id=user.id, skill_id=sid, enabled=enabled))
        else:
            row.enabled = enabled
    db.commit()


def view_skill(skill: Skill, revision: SkillRevision | None = None) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "display_name": skill.display_name,
        "description": skill.description,
        "when_to_use": skill.when_to_use,
        "model_invocable": skill.model_invocable,
        "user_invocable": skill.user_invocable,
        "status": skill.status.value,
        "source": skill.source,
        "revision": revision.rev if revision else None,
        "revision_id": skill.current_revision_id,
        "size_bytes": revision.size_bytes if revision else 0,
        "created_at": skill.created_at.isoformat() + "Z",
        "updated_at": skill.updated_at.isoformat() + "Z",
    }


def revision_content(revision: SkillRevision | None) -> dict:
    if revision is None:
        return {"body": "", "files": []}
    root = Path(revision.storage_path)
    body = ""
    skill_md = root / "SKILL.md"
    if skill_md.is_file():
        try:
            _meta, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except ApiError:
            body = skill_md.read_text(encoding="utf-8")
    files = [
        {"path": entry["path"], "size": entry["size"]}
        for entry in (revision.manifest or {}).get("files", [])
    ]
    return {"body": body, "files": files}


def grants_of(db: Session, skill_id: int) -> list[dict]:
    rows = db.query(SkillGrant).filter(SkillGrant.skill_id == skill_id).all()
    return [{"scope": row.scope.value, "target_id": row.target_id} for row in rows]


def detail_skill(db: Session, skill: Skill) -> dict:
    revision = current_revision(skill, db)
    return {
        **view_skill(skill, revision),
        **revision_content(revision),
        "grants": grants_of(db, skill.id),
    }


# ── materialisation ─────────────────────────────────────────────────────────


def _manifest_path(instance: Instance) -> Path:
    return Path(instance.data_path) / MANAGED_DIR / MANIFEST_NAME


def _read_manifest(instance: Instance) -> dict:
    path = _manifest_path(instance)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_manifest(instance: Instance, manifest: dict) -> None:
    path = _manifest_path(instance)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    if hasattr(os, "chown"):
        try:
            os.chown(path.parent, settings.instance_uid, settings.instance_gid)
            os.chown(path, settings.instance_uid, settings.instance_gid)
        except PermissionError:
            pass


def desired_revision_map(db: Session, user: User) -> dict[str, dict]:
    """name -> {revision_id, checksum, path} for this user's enabled skills."""
    enabled = enabled_skill_ids(db, user)
    if not enabled:
        return {}
    rows = (
        db.query(Skill, SkillRevision)
        .join(SkillRevision, Skill.current_revision_id == SkillRevision.id)
        .filter(Skill.id.in_(enabled))
        .all()
    )
    return {
        skill.name: {
            "revision_id": revision.id,
            "checksum": revision.checksum,
            "path": revision.storage_path,
        }
        for skill, revision in rows
    }


def revision_hash(desired: dict[str, dict]) -> str:
    payload = {name: item["checksum"] for name, item in desired.items()}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _sync_dir(target: Path, source: Path) -> None:
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".tmp-{target.name}-{uuid.uuid4().hex[:8]}"
    shutil.copytree(source, staging)
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)


def sync_instance_skills(db: Session, instance: Instance) -> dict:
    """Write the desired skill set into the instance data dir (hot reload)."""
    user = db.get(User, instance.user_id)
    if user is None:
        return {"changed": False}
    desired = desired_revision_map(db, user)
    root = Path(instance.data_path) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    old = _read_manifest(instance)
    changed = False

    for name, item in desired.items():
        target = root / name
        if target.exists() and name not in old:
            # A directory we do not own (user-created). Never overwrite it.
            continue
        if old.get(name, {}).get("checksum") == item["checksum"] and target.exists():
            continue
        _sync_dir(target, Path(item["path"]))
        changed = True

    for name in set(old) - set(desired):
        target = root / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        changed = True

    for entry in root.iterdir():
        if entry.name.startswith("."):
            continue
        try:
            os.chmod(entry, 0o755)
        except OSError:
            pass

    manifest = {
        name: {"revision_id": item["revision_id"], "checksum": item["checksum"]}
        for name, item in desired.items()
        if (root / name).exists()
    }
    if manifest != {
        key: {"revision_id": val.get("revision_id"), "checksum": val.get("checksum")}
        for key, val in old.items()
    }:
        _write_manifest(instance, manifest)
    actual = {
        name: {"checksum": item["checksum"]} for name, item in manifest.items()
    }
    instance.skills_revision = revision_hash(actual)
    instance.config_synced_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return {"changed": changed, "skills": sorted(desired), "hash": instance.skills_revision}


def sync_user_skills(db: Session, user_id: int) -> dict:
    instance = (
        db.query(Instance)
        .filter(Instance.user_id == user_id, Instance.status != InstanceStatus.deleted)
        .first()
    )
    if instance is None:
        return {"changed": False}
    return sync_instance_skills(db, instance)


def sync_skill_users(db: Session, skill_id: int) -> int:
    """Re-sync every instance that could be affected by a skill change."""
    users = db.query(User.id).filter(User.deleted_at.is_(None)).all()
    count = 0
    for (user_id,) in users:
        try:
            result = sync_user_skills(db, user_id)
        except Exception:  # noqa: BLE001 - one broken instance must not block others
            import logging

            logging.getLogger("dsh.skills").exception(
                "skill sync failed for user %s (skill %s)", user_id, skill_id
            )
            continue
        if result.get("changed"):
            count += 1
    return count


def sync_all_instances(db: Session) -> dict:
    """Reconcile every live instance; used by the worker loop."""
    instances = db.query(Instance).filter(Instance.status != InstanceStatus.deleted).all()
    changed = 0
    errors = 0
    for instance in instances:
        try:
            before = instance.skills_revision
            result = sync_instance_skills(db, instance)
            if result.get("changed") or instance.skills_revision != before:
                changed += 1
        except Exception:  # noqa: BLE001
            errors += 1
            import logging

            logging.getLogger("dsh.skills").exception(
                "skill reconcile failed for instance %s", instance.id
            )
    return {"instances": len(instances), "changed": changed, "errors": errors}
