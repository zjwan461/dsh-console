from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..db import SessionLocal
from ..deps import current_user
from ..models import User
from ..services.instances import get_instance

router = APIRouter(tags=["events"])


@router.get("/events")
def stream(request: Request, user: User = Depends(current_user)):
    """SSE stream of instance state changes.

    Polling the DB inside the stream (rather than pub/sub) keeps M2 correct with
    multiple API workers and any number of restarts. The stream is deliberately
    short-lived; EventSource reconnects automatically.
    """
    user_id = user.id

    def generate():
        last = object()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                instance = get_instance(db, user_id)
                status = instance.status.value if instance else "none"
                payload = {
                    "instance_id": instance.id if instance else None,
                    "status": status,
                    "reason": instance.status_reason if instance else None,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            if status != last:
                last = status
                yield f"event: instance.status\ndata: {json.dumps(payload)}\n\n"
            else:
                yield ": keep-alive\n\n"
            time.sleep(2)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
