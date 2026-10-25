"""Unified domain error type and its JSON handler (registered in src/main.py)."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class DomainError(Exception):
    """Business-rule violation that maps to a predictable JSON error payload."""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.extra = extra or {}


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    payload = {
        "error_code": exc.error_code,
        "message": exc.message,
        **exc.extra,
    }
    return JSONResponse(status_code=exc.status_code, content=payload)
