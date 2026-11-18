from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..db import get_db
from ..deps import csrf_guard, current_user
from ..errors import envelope
from ..models import Instance, User, UserSkillPref
from ..schemas import SkillPrefsIn
from ..services import plugins, skills

router = APIRouter(prefix="/me", tags=["me:skills"], dependencies=[Depends(csrf_guard)])


def _payload(db: Session, user: User) -> dict:
    allowed = skills.allowed_skills(db, user)
    prefs = {
        row.skill_id: row.enabled
        for row in db.query(UserSkillPref).filter(UserSkillPref.user_id == user.id).all()
    }
    items = [
        {
            "id": skill.id,
            "name": skill.name,
            "display_name": skill.display_name,
            "description": skill.description,
            "when_to_use": skill.when_to_use,
            "enabled": prefs.get(skill.id, True),
        }
        for skill in allowed
    ]
    return {
        "items": items,
        "total": len(items),
        "enabled": sum(1 for item in items if item["enabled"]),
    }


@router.get("/skills")
def my_skills(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return envelope(request, _payload(db, user))


@router.put("/skills")
def update_my_skills(
    payload: SkillPrefsIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    skills.set_user_prefs(db, user, set(payload.enabled_ids))
    result = skills.sync_user_skills(db, user.id)
    write_audit(
        db,
        request,
        user,
        "me.skills_update",
        "user",
        user.id,
        {"enabled_ids": payload.enabled_ids, "synced": result.get("changed")},
    )
    db.commit()
    return envelope(request, _payload(db, user))


@router.get("/plugins")
def my_plugins(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Read-only: users cannot toggle plugins (server-side code, admin-owned)."""
    items = [
        {
            "package_name": plugin.package_name,
            "display_name": plugin.display_name,
            "version": revision.version,
            "description": plugin.description,
        }
        for plugin, revision in plugins.assigned_plugins(db, user)
    ]
    instance = db.query(Instance).filter(Instance.user_id == user.id).first()
    return envelope(
        request,
        {
            "items": items,
            "total": len(items),
            "needs_restart": bool(instance and instance.needs_restart),
        },
    )