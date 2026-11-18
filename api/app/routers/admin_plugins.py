from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..errors import ApiError, envelope
from ..models import Plugin, PluginStatus, User
from ..schemas import PluginGrantIn
from ..services import plugins

router = APIRouter(
    prefix="/admin/plugins", tags=["admin:plugins"], dependencies=[Depends(csrf_guard)]
)


def _get(db: Session, plugin_id: int) -> Plugin:
    plugin = db.get(Plugin, plugin_id)
    if plugin is None:
        raise ApiError("PLUGIN_NOT_FOUND", "插件不存在", 404)
    return plugin


@router.get("")
def list_plugins(
    request: Request,
    q: str = Query(default="", max_length=100),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Plugin)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Plugin.package_name.like(like)) | (Plugin.description.like(like))
        )
    rows = query.order_by(Plugin.updated_at.desc()).all()
    return envelope(
        request,
        {"items": [plugins.view_plugin(db, plugin) for plugin in rows], "total": len(rows)},
    )


@router.post("/import", status_code=201)
def import_plugin(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plugin = plugins.import_plugin(db, file.filename or "plugin.zip", file.file, admin.id)
    write_audit(
        db,
        request,
        admin,
        "admin.plugin_import",
        "plugin",
        plugin.id,
        {"package": plugin.package_name, "version": plugin.version},
    )
    db.commit()
    return envelope(request, plugins.view_plugin(db, plugin))


@router.get("/{plugin_id}")
def get_plugin(
    plugin_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return envelope(request, plugins.view_plugin(db, _get(db, plugin_id)))


@router.post("/{plugin_id}/enable")
def enable_plugin(
    plugin_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plugin = _get(db, plugin_id)
    plugins.set_status(db, plugin, PluginStatus.enabled)
    result = plugins.sync_all_instances(db)
    write_audit(
        db,
        request,
        admin,
        "admin.plugin_enable",
        "plugin",
        plugin.id,
        {"package": plugin.package_name, **result},
    )
    db.commit()
    return envelope(request, {"plugin": plugins.view_plugin(db, plugin), "sync": result})


@router.post("/{plugin_id}/disable")
def disable_plugin(
    plugin_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plugin = _get(db, plugin_id)
    plugins.set_status(db, plugin, PluginStatus.disabled)
    result = plugins.sync_all_instances(db)
    write_audit(
        db,
        request,
        admin,
        "admin.plugin_disable",
        "plugin",
        plugin.id,
        {"package": plugin.package_name, **result},
    )
    db.commit()
    return envelope(request, {"plugin": plugins.view_plugin(db, plugin), "sync": result})


@router.put("/{plugin_id}/grants")
def set_grants(
    plugin_id: int,
    payload: PluginGrantIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plugin = _get(db, plugin_id)
    plugins.set_grants(db, plugin, payload.grants)
    result = plugins.sync_all_instances(db)
    write_audit(
        db,
        request,
        admin,
        "admin.plugin_grant",
        "plugin",
        plugin.id,
        {"grants": payload.grants, **result},
    )
    db.commit()
    return envelope(
        request, {"grants": plugins.grants_of(db, plugin.id), "sync": result}
    )