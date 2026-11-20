from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import time
from datetime import datetime

import zstandard
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import ApiError
from ..models import Instance, Snapshot, SnapshotStatus
from ..security import utcnow
from .instances import start_instance, stop_instance

log = logging.getLogger("dsh.snapshots")

EXCLUDES = {".runtime", "node_modules/.cache", ".cache"}


def snapshot_root() -> str:
    return os.path.join(settings.data_root, "snapshots")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _add_tree(tar: tarfile.TarFile, source: str, arcname: str) -> int:
    count = 0
    if not os.path.isdir(source):
        return count
    for root, dirs, files in os.walk(source):
        # Compare relative paths, not bare names: `node_modules/.cache` is a path,
        # so matching it against a directory name never excluded anything.
        rel = os.path.relpath(root, source)
        kept = []
        for name in dirs:
            relative = name if rel == "." else os.path.join(rel, name)
            if name in EXCLUDES or relative.replace(os.sep, "/") in EXCLUDES:
                continue
            kept.append(name)
        dirs[:] = kept
        target = arcname if rel == "." else os.path.join(arcname, rel)
        tar.add(root, arcname=target, recursive=False)
        for name in files:
            full = os.path.join(root, name)
            try:
                tar.add(full, arcname=os.path.join(target, name), recursive=False)
                count += 1
            except OSError:
                log.warning("skip unreadable file %s", full)
    return count


def create(
    db: Session, instance: Instance, note: str = "", snapshot: Snapshot | None = None
) -> Snapshot:
    if snapshot is None:
        snapshot = Snapshot(
            user_id=instance.user_id,
            instance_id=instance.id,
            status=SnapshotStatus.creating,
            dsh_version=instance.dsh_version,
            note=note[:200],
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
    else:
        snapshot.status = SnapshotStatus.creating
        snapshot.note = note[:200]
        db.commit()

    folder = os.path.join(snapshot_root(), str(instance.user_id))
    _ensure_dir(folder)
    path = os.path.join(folder, f"{snapshot.id}.tar.zst")

    was_running = instance.status.value == "running"
    try:
        if was_running:
            stop_instance(instance.id, reason="snapshot")
        compressor = zstandard.ZstdCompressor(level=3)
        with open(path, "wb") as raw:
            with compressor.stream_writer(raw) as writer:
                with tarfile.open(fileobj=writer, mode="w|") as tar:
                    files = _add_tree(tar, instance.data_path, "data")
                    files += _add_tree(tar, instance.workspace_path, "workspace")
        size = os.path.getsize(path)
        manifest = {
            "version": 1,
            "instance_id": instance.id,
            "dsh_version": instance.dsh_version,
            "files": files,
            "created_at": utcnow().isoformat() + "Z",
            "image_digest": None,
        }
        snapshot.status = SnapshotStatus.ready
        snapshot.path = os.path.relpath(path, settings.data_root)
        snapshot.size_bytes = size
        snapshot.sha256 = _sha256(path)
        snapshot.manifest = manifest
        db.commit()
    except Exception as exc:
        log.exception("snapshot %s failed", snapshot.id)
        snapshot.status = SnapshotStatus.failed
        snapshot.error = str(exc)[:500]
        db.commit()
        if os.path.exists(path):
            os.remove(path)
        raise ApiError("SNAPSHOT_FAILED", f"快照失败：{exc}", 500) from exc
    finally:
        if was_running:
            start_instance(instance.id)
    db.refresh(snapshot)
    return snapshot


def restore(db: Session, snapshot: Snapshot) -> None:
    instance = db.get(Instance, snapshot.instance_id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "实例不存在", 404)
    if snapshot.status != SnapshotStatus.ready:
        raise ApiError("SNAPSHOT_FAILED", "该快照不可用", 409)
    path = os.path.join(settings.data_root, snapshot.path)
    if not os.path.exists(path):
        raise ApiError("SNAPSHOT_FAILED", "快照文件丢失", 404)

    was_running = instance.status.value == "running"
    if was_running:
        stop_instance(instance.id, reason="restore")
    root = os.path.dirname(instance.data_path)
    backup = os.path.join(root, f".restore-backup-{int(time.time())}")
    snapshot.status = SnapshotStatus.restoring
    db.commit()
    moved = False
    try:
        if os.path.isdir(root):
            os.makedirs(backup, exist_ok=True)
            for name in ("data", "workspace"):
                src = os.path.join(root, name)
                if os.path.isdir(src):
                    shutil.move(src, os.path.join(backup, name))
            moved = True
        with open(path, "rb") as raw:
            with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as tar:
                    _safe_extract(tar, root)
        _fix_owner(root)
        snapshot.status = SnapshotStatus.ready
        snapshot.error = None
        db.commit()
    except Exception as exc:
        log.exception("restore %s failed", snapshot.id)
        if moved:
            for name in ("data", "workspace"):
                src = os.path.join(backup, name)
                dst = os.path.join(root, name)
                if os.path.isdir(src):
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    shutil.move(src, dst)
        snapshot.status = SnapshotStatus.failed
        snapshot.error = str(exc)[:500]
        db.commit()
        start_instance(instance.id)
        raise ApiError("SNAPSHOT_RESTORE_FAILED", f"恢复失败：{exc}", 500) from exc
    finally:
        if os.path.isdir(backup):
            shutil.rmtree(backup, ignore_errors=True)
    start_instance(instance.id)


def _safe_extract(tar: tarfile.TarFile, root: str) -> None:
    """Extract while refusing anything that would escape `root`.

    Symlinks are validated on the *link itself*, not on its target: a snapshot
    taken after an image switch can legitimately contain links into the image
    (e.g. `profiles/node_modules/<plugin>`), and some of those targets may no
    longer exist. Resolving them here would both reject valid archives and raise
    on dangling links.
    """
    base = os.path.realpath(root)
    for member in tar:
        destination = os.path.join(root, member.name)
        if member.issym() or member.islnk():
            link = member.linkname
            if os.path.isabs(link):
                raise RuntimeError(f"absolute link in snapshot: {member.name}")
            # The link must stay inside the extraction root when resolved
            # relative to the directory that will contain it.
            resolved = os.path.realpath(os.path.join(os.path.dirname(destination), link))
            if not (resolved == base or resolved.startswith(base + os.sep)):
                raise RuntimeError(f"unsafe link in snapshot: {member.name} -> {link}")
            target = os.path.realpath(os.path.dirname(destination))
        else:
            target = os.path.realpath(destination)
        if not (target == base or target.startswith(base + os.sep)):
            raise RuntimeError(f"unsafe path in snapshot: {member.name}")
        tar.extract(member, root)


def _fix_owner(root: str) -> None:
    """Give the restored tree back to the instance uid.

    Uses `lchown` so a symlink is re-owned instead of its target, and skips any
    entry that vanished — a dangling link must not fail the whole restore.
    """
    chown = getattr(os, "lchown", None) or getattr(os, "chown", None)
    if chown is None:
        return
    for current, _dirs, files in os.walk(root):
        for path in (current, *(os.path.join(current, name) for name in files)):
            try:
                chown(path, settings.instance_uid, settings.instance_gid)
            except PermissionError:
                return
            except OSError:
                # Broken symlink / concurrently removed entry: harmless.
                log.debug("skip chown for %s", path)


def delete(db: Session, snapshot: Snapshot) -> None:
    path = os.path.join(settings.data_root, snapshot.path) if snapshot.path else ""
    if path and os.path.exists(path):
        os.remove(path)
    db.delete(snapshot)
    db.commit()


def prune_expired(db: Session, days: int) -> int:
    cutoff = datetime.utcnow().replace(microsecond=0)
    from datetime import timedelta

    cutoff = cutoff - timedelta(days=days)
    rows = db.query(Snapshot).filter(Snapshot.created_at < cutoff).all()
    for row in rows:
        try:
            delete(db, row)
        except OSError:
            log.warning("could not prune snapshot %s", row.id)
    return len(rows)
