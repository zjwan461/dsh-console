from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import csrf_guard, current_user
from ..errors import ApiError, envelope
from ..models import User
from ..schemas import FileMkdirIn, FileRenameIn
from ..services.instances import get_instance

router = APIRouter(prefix="/instances/me/files", tags=["files"], dependencies=[Depends(csrf_guard)])


def _root(db: Session, user: User) -> Path:
    instance = get_instance(db, user.id)
    if instance is None:
        raise ApiError("INSTANCE_NOT_FOUND", "还没有实例", 404)
    root = Path(instance.workspace_path).resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    return root


def _safe(root: Path, relative: str) -> Path:
    if "\x00" in relative:
        raise ApiError("FILE_PATH_INVALID", "路径非法", 400)
    # Treat every incoming path as POSIX; Path("\\docs") is absolute on Windows.
    normalized = relative.replace("\\", "/").lstrip("/")
    target = (root / normalized).resolve()
    if target != root and root not in target.parents:
        raise ApiError("FILE_PATH_INVALID", "路径越界", 400)
    return target


def _entry(path: Path, root: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": "/" + str(path.relative_to(root)),
        "type": "dir" if path.is_dir() else "file",
        "size": 0 if path.is_dir() else stat.st_size,
        "modified_at": __import__("datetime").datetime.utcfromtimestamp(stat.st_mtime).isoformat()
        + "Z",
    }


@router.get("")
def list_files(
    request: Request,
    path: str = Query(default="/"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    root = _root(db, user)
    target = _safe(root, path)
    if not target.exists():
        raise ApiError("FILE_NOT_FOUND", "目录不存在", 404)
    if not target.is_dir():
        raise ApiError("VALIDATION_ERROR", "不是目录", 400)
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        try:
            entries.append(_entry(child, root))
        except OSError:
            continue
    return envelope(request, {"path": "/" + str(target.relative_to(root)), "entries": entries})


@router.get("/download")
def download(
    path: str = Query(min_length=1),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    root = _root(db, user)
    target = _safe(root, path)
    if not target.is_file():
        raise ApiError("FILE_NOT_FOUND", "文件不存在", 404)
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/upload", status_code=201)
def upload(
    request: Request,
    path: str = Form(default="/"),
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    root = _root(db, user)
    folder = _safe(root, path)
    if not folder.is_dir():
        raise ApiError("FILE_PATH_INVALID", "目标目录不存在", 400)
    name = os.path.basename(file.filename or "upload.bin")
    if not name or name in {".", ".."}:
        raise ApiError("FILE_PATH_INVALID", "文件名非法", 400)
    target = _safe(root, str(Path(path) / name))
    limit = settings.upload_max_mb * 1024 * 1024
    temp = target.with_name(target.name + ".part")
    size = 0
    try:
        with open(temp, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ApiError("FILE_TOO_LARGE", "文件超过上传上限", 413)
                out.write(chunk)
        os.replace(temp, target)
    except ApiError:
        if temp.exists():
            temp.unlink()
        raise
    return envelope(request, _entry(target, root))


@router.post("/mkdir", status_code=201)
def mkdir(
    payload: FileMkdirIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    root = _root(db, user)
    parent = _safe(root, payload.path)
    if not parent.is_dir():
        raise ApiError("FILE_PATH_INVALID", "父目录不存在", 400)
    target = _safe(root, str(Path(payload.path) / payload.name))
    if target.exists():
        raise ApiError("FILE_EXISTS", "同名文件或目录已存在", 409)
    target.mkdir()
    return envelope(request, _entry(target, root))


@router.post("/rename")
def rename(
    payload: FileRenameIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    root = _root(db, user)
    source = _safe(root, payload.path)
    if not source.exists():
        raise ApiError("FILE_NOT_FOUND", "文件不存在", 404)
    target = _safe(root, str(source.parent.relative_to(root) / payload.new_name))
    if target.exists():
        raise ApiError("FILE_EXISTS", "目标已存在", 409)
    source.rename(target)
    return envelope(request, _entry(target, root))


@router.delete("", status_code=204)
def delete(
    request: Request,
    path: str = Query(min_length=1),
    recursive: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    root = _root(db, user)
    target = _safe(root, path)
    if target == root:
        raise ApiError("FILE_PATH_INVALID", "不能删除根目录", 400)
    if not target.exists():
        raise ApiError("FILE_NOT_FOUND", "文件不存在", 404)
    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            try:
                target.rmdir()
            except OSError:
                raise ApiError("FILE_NOT_EMPTY", "目录非空", 409) from None
    else:
        target.unlink()
    return None
