"""Unified error payloads: {"error_code", "message", ...extra} for every error the API returns."""

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
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


# --- OpenAPI contract (docs/openapi.json) ---------------------------------------------------------


class ValidationErrorItem(BaseModel):
    loc: list[str | int]
    message: str
    type: str


class ErrorResponse(BaseModel):
    """Body of every error response. The frontend picks its text by error_code.

    Some errors add fields of their own (e.g. `required_confirmation_level`,
    `allowed_statuses`, `unknown_apartment_ids`); docs/API.md lists them per endpoint.
    """

    model_config = ConfigDict(extra="allow")

    error_code: str
    message: str
    errors: list[ValidationErrorItem] | None = None  # only for VALIDATION_ERROR
    trace_id: str | None = None  # only for INTERNAL_SERVER_ERROR


AUTH_ERRORS = ("NOT_AUTHENTICATED", "INVALID_TOKEN", "UNKNOWN_USER")

_STATUS_TITLES = {
    400: "Bad request",
    401: "Not authenticated",
    403: "Forbidden",
    404: "Not found (or not visible to the caller)",
    409: "Conflict with the current state",
    422: "Invalid input",
    429: "Too many requests (see retry_after_seconds and the Retry-After header)",
    500: "Internal server error",
    503: "Service unavailable",
}


def error_responses(codes: Mapping[int, Sequence[str]]) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` for the given status codes, each listing its possible error_codes.

    Every endpoint that takes parameters must list 422 (with VALIDATION_ERROR), otherwise FastAPI
    documents its own HTTPValidationError shape, which this API never returns.
    """
    return {
        status: {
            "model": ErrorResponse,
            "description": f"{_STATUS_TITLES[status]}. error_code: "
            + ", ".join(f"`{code}`" for code in error_codes),
        }
        for status, error_codes in sorted(codes.items())
    }
