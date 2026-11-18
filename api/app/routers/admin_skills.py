from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..db import get_db
from ..deps import csrf_guard, require_admin
from ..errors import ApiError, envelope
from ..models import Skill, SkillStatus, User
from ..schemas import SkillCreateIn, SkillGrantIn, SkillUpdateIn
from ..services import skills

router = APIRouter(
    prefix="/admin/skills", tags=["admin:skills"], dependencies=[Depends(csrf_guard)]
)


def _get(db: Session, skill_id: int) -> Skill:
    skill = db.get(Skill, skill_id)
    if skill is None:
        raise ApiError("SKILL_NOT_FOUND", "技能不存在", 404)
    return skill


@router.get("")
def list_skills(
    request: Request,
    q: str = Query(default="", max_length=100),
    status: str = Query(default=""),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Skill)
    if q:
        like = f"%{q}%"
        query = query.filter((Skill.name.like(like)) | (Skill.description.like(like)))
    if status:
        try:
            query = query.filter(Skill.status == SkillStatus(status))
        except ValueError:
            raise ApiError(
                "SKILL_INVALID", "状态必须是 draft / published / disabled", 422
            ) from None
    rows = query.order_by(Skill.updated_at.desc()).all()
    items = []
    for skill in rows:
        revision = skills.current_revision(skill, db)
        item = skills.view_skill(skill, revision)
        item["grants"] = skills.grants_of(db, skill.id)
        items.append(item)
    return envelope(request, {"items": items, "total": len(items)})


@router.post("", status_code=201)
def create_skill(
    payload: SkillCreateIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    skill = skills.create_skill(db, payload.model_dump(), admin.id)
    write_audit(db, request, admin, "admin.skill_create", "skill", skill.id, {"name": skill.name})
    db.commit()
    return envelope(request, skills.detail_skill(db, skill))


@router.post("/import", status_code=201)
def import_skill(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    skill = skills.import_skill(
        db, file.filename or "skill.zip", file.file, admin.id, source="upload"
    )
    write_audit(
        db,
        request,
        admin,
        "admin.skill_import",
        "skill",
        skill.id,
        {"name": skill.name, "filename": file.filename},
    )
    db.commit()
    return envelope(request, skills.detail_skill(db, skill))


@router.get("/{skill_id}")
def get_skill(
    skill_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return envelope(request, skills.detail_skill(db, _get(db, skill_id)))


@router.put("/{skill_id}")
def update_skill(
    skill_id: int,
    payload: SkillUpdateIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    skill = _get(db, skill_id)
    data = payload.model_dump(exclude_unset=True)
    if "description" not in data:
        data["description"] = skill.description
    if "body" not in data:
        # A metadata-only save must not erase the existing body.
        data["body"] = skills.revision_content(skills.current_revision(skill, db))["body"]
    skills.update_skill(db, skill, data, admin.id)
    synced = skills.sync_skill_users(db, skill.id) if skill.status.value == "published" else 0
    write_audit(
        db,
        request,
        admin,
        "admin.skill_update",
        "skill",
        skill.id,
        {"name": skill.name, "synced_instances": synced},
    )
    db.commit()
    return envelope(request, {**skills.detail_skill(db, skill), "synced": synced})


@router.delete("/{skill_id}", status_code=204)
def disable_skill(
    skill_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    skill = _get(db, skill_id)
    skills.disable_skill(db, skill)
    skills.sync_skill_users(db, skill.id)
    write_audit(db, request, admin, "admin.skill_disable", "skill", skill.id, {"name": skill.name})
    db.commit()
    return None


@router.post("/{skill_id}/publish")
def publish_skill(
    skill_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    skill = _get(db, skill_id)
    skills.publish_skill(db, skill)
    changed = skills.sync_skill_users(db, skill.id)
    write_audit(
        db,
        request,
        admin,
        "admin.skill_publish",
        "skill",
        skill.id,
        {"name": skill.name, "synced_instances": changed},
    )
    db.commit()
    return envelope(request, {"skill": skills.detail_skill(db, skill), "synced": changed})


@router.post("/{skill_id}/rollback")
def rollback_skill(
    skill_id: int,
    request: Request,
    revision_id: int = Body(embed=True),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    skill = _get(db, skill_id)
    skills.rollback_skill(db, skill, revision_id)
    changed = skills.sync_skill_users(db, skill.id)
    write_audit(
        db,
        request,
        admin,
        "admin.skill_rollback",
        "skill",
        skill.id,
        {"revision_id": revision_id, "synced_instances": changed},
    )
    db.commit()
    return envelope(request, skills.detail_skill(db, skill))


@router.get("/{skill_id}/revisions")
def list_revisions(
    skill_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _get(db, skill_id)
    from ..models import SkillRevision

    rows = (
        db.query(SkillRevision)
        .filter(SkillRevision.skill_id == skill_id)
        .order_by(SkillRevision.rev.desc())
        .all()
    )
    return envelope(
        request,
        {
            "items": [
                {
                    "id": row.id,
                    "rev": row.rev,
                    "checksum": row.checksum,
                    "size_bytes": row.size_bytes,
                    "changelog": row.changelog,
                    "created_at": row.created_at.isoformat() + "Z",
                }
                for row in rows
            ]
        },
    )


@router.put("/{skill_id}/grants")
def set_grants(
    skill_id: int,
    payload: SkillGrantIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    skill = _get(db, skill_id)
    skills.set_grants(db, skill, payload.grants)
    changed = skills.sync_skill_users(db, skill.id)
    write_audit(
        db,
        request,
        admin,
        "admin.skill_grant",
        "skill",
        skill.id,
        {"grants": payload.grants, "synced_instances": changed},
    )
    db.commit()
    return envelope(request, {"grants": skills.grants_of(db, skill.id), "synced": changed})