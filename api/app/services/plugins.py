"""Admin-managed plugin catalog and per-instance materialisation.

Image built-ins stay where they are: a platform plugin is added as a read-only
bind mount at `/opt/dsh-plugins/<flat>` so the image's entrypoint discovers it
together with its own plugins. That avoids replacing `DSH_PLUGIN_ROOT` (which
would hide every built-in).

Disabling is the hard part: the entrypoint seeds a patch layer into
`$DSH_HOME/cordis.patch.yml` **once** and never removes it. Removing the mount
without removing that block makes the next boot fail with
`plugin tree failed to load`, so `remove_plugin_block()` runs before the
container is recreated.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import ApiError
from ..models import (
    GrantScope,
    Instance,
    InstanceStatus,
    Plugin,
    PluginGrant,
    PluginRevision,
    PluginStatus,
    User,
)

MAX_BYTES = 50 * 1024 * 1024
MAX_FILES = 2000
PACKAGE_RE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]{0,127}$")
PATCH_START = "# bundled plugin: "


def flat_dir_name(package: str) -> str:
    """A single directory name, because the entrypoint only scans one level."""
    return package.replace("@", "").replace("/", "__")


def _safe_rel(name: str) -> str:
    if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
        raise ApiError("PLUGIN_INVALID", f"非法路径：{name}", 422)
    return name


def _hash_tree(root: Path) -> tuple[str, int, list[dict]]:
    digest = hashlib.sha256()
    files: list[dict] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        _safe_rel(rel)
        if path.is_symlink():
            raise ApiError("PLUGIN_INVALID", f"不允许符号链接：{rel}", 422)
        size = path.stat().st_size
        total += size
        if total > MAX_BYTES:
            raise ApiError("PLUGIN_INVALID", "插件超过 50 MB", 422)
        if len(files) >= MAX_FILES:
            raise ApiError("PLUGIN_INVALID", f"文件数超过 {MAX_FILES}", 422)
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": rel, "size": size, "sha256": file_hash})
        digest.update(rel.encode())
        digest.update(file_hash.encode())
    return digest.hexdigest(), total, files


def _extract(filename: str, fileobj, dest: Path) -> None:
    lower = filename.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(fileobj) as archive:
            for info in archive.infolist():
                name = _safe_rel(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode and mode not in (0o100000, 0o040000):
                    raise ApiError("PLUGIN_INVALID", f"含非普通文件：{name}", 422)
                target = dest / name
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    elif lower.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(fileobj=fileobj, mode="r:*") as archive:
            for member in archive.getmembers():
                name = _safe_rel(member.name)
                if member.issym() or member.islnk() or not (
                    member.isfile() or member.isdir()
                ):
                    raise ApiError("PLUGIN_INVALID", f"含链接/特殊文件：{name}", 422)
                target = dest / name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                src = archive.extractfile(member)
                if src is not None:
                    with src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
    else:
        raise ApiError("PLUGIN_INVALID", "只支持 .zip / .tar.gz", 422)


def locate_bundle(root: Path) -> Path:
    if (root / "package.json").is_file():
        return root
    children = [p for p in root.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() and (children[0] / "package.json").is_file():
        return children[0]
    raise ApiError("PLUGIN_INVALID", "找不到 package.json", 422)


def validate_bundle(root: Path) -> dict:
    manifest_path = root / "package.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApiError("PLUGIN_INVALID", f"package.json 不是合法 JSON：{exc}", 422) from exc
    name = str(manifest.get("name") or "")
    if not PACKAGE_RE.match(name):
        raise ApiError("PLUGIN_INVALID", f"包名不合法：{name!r}", 422)
    version = str(manifest.get("version") or "0.0.0")
    exports = manifest.get("exports")
    entry = manifest.get("main")
    if isinstance(exports, dict):
        entry = exports.get(".") or entry
    elif isinstance(exports, str):
        entry = exports
    if not entry:
        raise ApiError("PLUGIN_INVALID", "package.json 没有入口（exports['.'] 或 main）", 422)
    if not (root / Path(str(entry)).as_posix().lstrip("./")).exists():
        raise ApiError("PLUGIN_INVALID", f"入口文件不存在：{entry}", 422)

    patch = (manifest.get("dsh") or {}).get("bundle", {}).get("patch")
    patches = patch if isinstance(patch, list) else ([patch] if patch else [])
    for one in patches:
        if not (root / Path(str(one)).as_posix().lstrip("./")).exists():
            raise ApiError("PLUGIN_INVALID", f"patch 文件不存在：{one}", 422)

    peers = manifest.get("peerDependencies") or {}
    dsh_versions = str((manifest.get("dsh") or {}).get("versions") or "")
    if not dsh_versions and peers:
        dsh_versions = ", ".join(f"{k}@{v}" for k, v in peers.items())
    return {
        "name": name,
        "version": version,
        "description": str(manifest.get("description") or ""),
        "entry": str(entry),
        "patch_files": [str(item) for item in patches],
        "dsh_versions": dsh_versions[:200],
        "manifest": manifest,
    }


def plugins_root() -> Path:
    path = Path(settings.data_root) / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def import_plugin(db: Session, filename: str, fileobj, actor_id: int | None) -> Plugin:
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        _extract(filename, fileobj, staging)
        root = locate_bundle(staging)
        meta = validate_bundle(root)
        plugin = (
            db.query(Plugin).filter(Plugin.package_name == meta["name"]).first()
        )
        if plugin is None:
            plugin = Plugin(
                package_name=meta["name"],
                display_name=meta["name"],
                version=meta["version"],
                description=meta["description"][:500],
                dsh_versions=meta["dsh_versions"],
                manifest=meta["manifest"],
                status=PluginStatus.disabled,
                created_by=actor_id,
            )
            db.add(plugin)
            db.commit()
            db.refresh(plugin)
        if (
            db.query(PluginRevision)
            .filter(
                PluginRevision.plugin_id == plugin.id,
                PluginRevision.version == meta["version"],
            )
            .first()
        ):
            raise ApiError("PLUGIN_DUPLICATE", "该插件版本已存在", 409)

        notes, size, _files = _hash_tree(root)
        target = plugins_root() / str(plugin.id) / meta["version"]
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, target)
        revision = PluginRevision(
            plugin_id=plugin.id,
            version=meta["version"],
            storage_path=str(target),
            checksum=notes,
            size_bytes=size,
            manifest=meta["manifest"],
            patch_files=meta["patch_files"],
            created_by=actor_id,
        )
        db.add(revision)
        db.flush()
        plugin.current_revision_id = revision.id
        plugin.version = meta["version"]
        plugin.description = meta["description"][:500]
        plugin.dsh_versions = meta["dsh_versions"]
        plugin.manifest = meta["manifest"]
        db.commit()
        db.refresh(revision)
        return plugin


def current_revision(db: Session, plugin: Plugin) -> PluginRevision | None:
    if not plugin.current_revision_id:
        return None
    return db.get(PluginRevision, plugin.current_revision_id)


def set_status(db: Session, plugin: Plugin, status: PluginStatus) -> Plugin:
    plugin.status = status
    db.commit()
    return plugin


def set_grants(db: Session, plugin: Plugin, grants: list[dict]) -> None:
    db.query(PluginGrant).filter(PluginGrant.plugin_id == plugin.id).delete()
    seen: set[tuple[str, int]] = set()
    for grant in grants:
        try:
            scope = GrantScope(grant["scope"])
        except (KeyError, ValueError) as exc:
            raise ApiError("GRANT_INVALID", "分配范围必须是 all / plan / user", 422) from exc
        target = 0 if scope == GrantScope.all else int(grant.get("target_id") or 0)
        if (scope, target) in seen:
            continue
        seen.add((scope, target))
        db.add(PluginGrant(plugin_id=plugin.id, scope=scope, target_id=target))
    db.commit()


def grants_of(db: Session, plugin_id: int) -> list[dict]:
    rows = db.query(PluginGrant).filter(PluginGrant.plugin_id == plugin_id).all()
    return [{"scope": row.scope.value, "target_id": row.target_id} for row in rows]


def _granted_plugin_ids(db: Session, user: User) -> set[int]:
    rows = (
        db.query(PluginGrant.plugin_id)
        .filter(
            or_(
                PluginGrant.scope == GrantScope.all,
                (PluginGrant.scope == GrantScope.plan)
                & (PluginGrant.target_id == (user.plan_id or 0)),
                (PluginGrant.scope == GrantScope.user) & (PluginGrant.target_id == user.id),
            )
        )
        .all()
    )
    return {row[0] for row in rows}


def assigned_plugins(db: Session, user: User) -> list[tuple[Plugin, PluginRevision]]:
    ids = _granted_plugin_ids(db, user)
    if not ids:
        return []
    return (
        db.query(Plugin, PluginRevision)
        .join(PluginRevision, Plugin.current_revision_id == PluginRevision.id)
        .filter(
            Plugin.id.in_(ids),
            Plugin.status == PluginStatus.enabled,
            Plugin.current_revision_id.isnot(None),
        )
        .order_by(Plugin.package_name)
        .all()
    )


def view_plugin(db: Session, plugin: Plugin) -> dict:
    revision = current_revision(db, plugin)
    return {
        "id": plugin.id,
        "package_name": plugin.package_name,
        "display_name": plugin.display_name,
        "version": plugin.version,
        "description": plugin.description,
        "dsh_versions": plugin.dsh_versions,
        "status": plugin.status.value,
        "revision_id": plugin.current_revision_id,
        "size_bytes": revision.size_bytes if revision else 0,
        "grants": grants_of(db, plugin.id),
        "created_at": plugin.created_at.isoformat() + "Z",
        "updated_at": plugin.updated_at.isoformat() + "Z",
    }


def instance_plugin_dir(instance: Instance) -> Path:
    return Path(instance.data_path) / "plugins"


def remove_plugin_block(text: str, package: str) -> tuple[str, bool]:
    """Remove the entrypoint-seeded patch block for one plugin.

    The entrypoint writes:

        # --- bundled plugins (seeded once by entrypoint.sh; edit freely) ---
        # bundled plugin: @local/foo
        - insert: ...

    A block runs until the next `# bundled plugin:` / separator line, or EOF.
    """
    lines = text.splitlines(keepends=True)
    start = None
    marker = f"{PATCH_START}{package}"
    for index, line in enumerate(lines):
        if line.strip() == marker:
            start = index
            break
    if start is None:
        return text, False
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith(PATCH_START) or stripped.startswith("# --- bundled plugins"):
            end = index
            break
    return "".join(lines[:start] + lines[end:]), True


def sync_instance_plugins(db: Session, instance: Instance) -> dict:
    """Copy assigned plugins into the data dir and drop disabled ones.

    The container is re-created based on `_spec()` mounts; the patch block for a
    removed plugin must be gone before that happens, so it is cleaned here.
    """
    user = db.get(User, instance.user_id)
    if user is None:
        return {"changed": False}
    desired = assigned_plugins(db, user)
    root = instance_plugin_dir(instance)
    root.mkdir(parents=True, exist_ok=True)
    keep: set[str] = set()
    changed = False

    for plugin, revision in desired:
        name = flat_dir_name(plugin.package_name)
        keep.add(name)
        target = root / name
        expected = revision.checksum
        stamp = root / f".{name}.checksum"
        if target.exists() and stamp.is_file() and stamp.read_text() == expected:
            continue
        staging = root / f".tmp-{name}"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(revision.storage_path, staging)
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
        stamp.write_text(expected, encoding="utf-8")
        changed = True

    for entry in list(root.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.name not in keep:
            shutil.rmtree(entry, ignore_errors=True)
            (root / f".{entry.name}.checksum").unlink(missing_ok=True)
            changed = True

    # Remove patch blocks for platform plugins that are no longer assigned.
    # Image built-ins are never in the plugins table, so their blocks are safe.
    patch_path = Path(instance.data_path) / "cordis.patch.yml"
    if patch_path.is_file():
        text = patch_path.read_text(encoding="utf-8")
        assigned_names = {plugin.package_name for plugin, _ in desired}
        known = {row[0] for row in db.query(Plugin.package_name).all()}
        for stale in known - assigned_names:
            text, removed = remove_plugin_block(text, stale)
            changed = changed or removed
        patch_path.write_text(text, encoding="utf-8")

    desired_hash = hashlib.sha256(
        json.dumps(
            sorted((plugin.package_name, revision.checksum) for plugin, revision in desired)
        ).encode()
    ).hexdigest()
    if instance.plugins_revision != desired_hash:
        instance.needs_restart = bool(desired) or bool(instance.plugins_revision)
        changed = True
    instance.plugins_revision = desired_hash
    db.commit()
    return {"changed": changed, "plugins": sorted(keep), "needs_restart": instance.needs_restart}


def plugin_mounts(db: Session, instance: Instance) -> list[tuple[str, str, bool]]:
    """Read-only mounts that add platform plugins beside the image's own."""
    user = db.get(User, instance.user_id)
    if user is None:
        return []
    mounts = []
    for plugin, _revision in assigned_plugins(db, user):
        name = flat_dir_name(plugin.package_name)
        host = instance_plugin_dir(instance) / name
        if host.exists():
            mounts.append((str(host), f"/opt/dsh-plugins/{name}", True))
    return mounts


def sync_all_instances(db: Session) -> dict:
    instances = db.query(Instance).filter(Instance.status != InstanceStatus.deleted).all()
    changed = 0
    for instance in instances:
        try:
            if sync_instance_plugins(db, instance).get("changed"):
                changed += 1
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("dsh.plugins").exception(
                "plugin sync failed for instance %s", instance.id
            )
    return {"instances": len(instances), "changed": changed}