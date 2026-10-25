"""Unified error payloads: {"error_code", "message", ...extra} for every error the API returns."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_HTTP_ERROR_CODES = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


class DomainError(Exception):
    """Business-rule violation that maps to a predictable JSON error payload."""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.extra = extra or {}
        self.headers = headers


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    payload = {
        "error_code": exc.error_code,
        "message": exc.message,
        **exc.extra,
    }
    return JSONResponse(status_code=exc.status_code, content=payload, headers=exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # The submitted values ("input") are deliberately not echoed back.
    errors = [
        {"loc": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": "The request is invalid.",
            "errors": errors,
        },
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": _HTTP_ERROR_CODES.get(exc.status_code, f"HTTP_{exc.status_code}"),
            "message": str(exc.detail),
        },
        headers=exc.headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
