from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, current_user, require_admin
from ..errors import ApiError, envelope
from ..models import Ticket, TicketMessage, User
from ..schemas import TicketCreateIn, TicketMessageIn, TicketPatchIn

router = APIRouter(tags=["tickets"], dependencies=[Depends(csrf_guard)])


def _ticket_view(db: Session, ticket: Ticket) -> dict:
    last = (
        db.query(TicketMessage)
        .filter(TicketMessage.ticket_id == ticket.id)
        .order_by(TicketMessage.id.desc())
        .first()
    )
    return {
        "id": ticket.id,
        "subject": ticket.subject,
        "category": ticket.category,
        "priority": ticket.priority,
        "status": ticket.status,
        "assignee_id": ticket.assignee_id,
        "created_at": ticket.created_at.isoformat() + "Z",
        "updated_at": ticket.updated_at.isoformat() + "Z",
        "last_message": last.body[:200] if last else "",
        "message_count": db.query(TicketMessage)
        .filter(TicketMessage.ticket_id == ticket.id)
        .count(),
    }


def _owned(db: Session, user: User, ticket_id: int) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or (ticket.user_id != user.id):
        raise ApiError("NOT_FOUND", "工单不存在", 404)
    return ticket


def _messages(db: Session, ticket: Ticket) -> list[dict]:
    rows = (
        db.query(TicketMessage)
        .filter(TicketMessage.ticket_id == ticket.id)
        .order_by(TicketMessage.id)
        .all()
    )
    authors = (
        {
            u.id: u.username
            for u in db.query(User).filter(User.id.in_([r.author_id for r in rows])).all()
        }
        if rows
        else {}
    )
    return [
        {
            "id": row.id,
            "author_id": row.author_id,
            "author": authors.get(row.author_id, "system"),
            "body": row.body,
            "created_at": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]


@router.get("/me/tickets")
def my_tickets(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(Ticket).filter(Ticket.user_id == user.id).order_by(Ticket.id.desc()).all()
    return envelope(request, {"items": [_ticket_view(db, t) for t in rows], "total": len(rows)})


@router.post("/me/tickets", status_code=201)
def create_ticket(
    payload: TicketCreateIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ticket = Ticket(
        user_id=user.id,
        subject=payload.subject,
        category=payload.category,
        priority=payload.priority,
        status="open",
    )
    db.add(ticket)
    db.flush()
    db.add(TicketMessage(ticket_id=ticket.id, author_id=user.id, body=payload.body))
    ticket.last_reply_at = ticket.created_at
    db.commit()
    db.refresh(ticket)
    return envelope(request, _ticket_view(db, ticket))


@router.get("/me/tickets/{ticket_id}")
def get_ticket(
    ticket_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ticket = _owned(db, user, ticket_id)
    data = _ticket_view(db, ticket)
    data["messages"] = _messages(db, ticket)
    return envelope(request, data)


@router.post("/me/tickets/{ticket_id}/messages", status_code=201)
def reply(
    ticket_id: int,
    payload: TicketMessageIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ticket = _owned(db, user, ticket_id)
    if ticket.status in {"resolved", "closed"}:
        raise ApiError("TICKET_INVALID_STATE", "工单已关闭", 409)
    message = TicketMessage(ticket_id=ticket.id, author_id=user.id, body=payload.body)
    db.add(message)
    ticket.last_reply_at = message.created_at
    ticket.status = "open"
    db.commit()
    return envelope(request, _messages(db, ticket)[-1])


@router.get("/admin/tickets")
def admin_tickets(
    request: Request,
    status: str | None = Query(default=None),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Ticket)
    if status:
        query = query.filter(Ticket.status == status)
    rows = query.order_by(Ticket.id.desc()).limit(200).all()
    items = []
    for ticket in rows:
        owner = db.get(User, ticket.user_id)
        data = _ticket_view(db, ticket)
        data["user"] = {"id": owner.id, "username": owner.username} if owner else None
        items.append(data)
    return envelope(request, {"items": items, "total": len(items)})


@router.get("/admin/tickets/{ticket_id}")
def admin_ticket(
    ticket_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise ApiError("NOT_FOUND", "工单不存在", 404)
    data = _ticket_view(db, ticket)
    data["messages"] = _messages(db, ticket)
    return envelope(request, data)


@router.patch("/admin/tickets/{ticket_id}")
def patch_ticket(
    ticket_id: int,
    payload: TicketPatchIn,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise ApiError("NOT_FOUND", "工单不存在", 404)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(ticket, key, value)
    db.commit()
    return envelope(request, _ticket_view(db, ticket))


@router.post("/admin/tickets/{ticket_id}/messages", status_code=201)
def admin_reply(
    ticket_id: int,
    payload: TicketMessageIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise ApiError("NOT_FOUND", "工单不存在", 404)
    db.add(TicketMessage(ticket_id=ticket.id, author_id=admin.id, body=payload.body))
    ticket.last_reply_at = __import__("datetime").datetime.utcnow()
    if ticket.status == "open":
        ticket.status = "processing"
    db.commit()
    return envelope(request, _messages(db, ticket)[-1])
