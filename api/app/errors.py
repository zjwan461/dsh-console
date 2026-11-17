from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def envelope(request: Request, data: Any) -> dict[str, Any]:
    return {"data": data, "request_id": request_id(request)}


def error_body(request: Request, code: str, message: str, details: dict | None = None) -> dict:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message},
        "request_id": request_id(request),
    }
    if details:
        body["error"]["details"] = details
    return body


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(request, exc.code, exc.message, exc.details),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_body(request, "INTERNAL_ERROR", "服务内部错误，请稍后重试"),
    )
