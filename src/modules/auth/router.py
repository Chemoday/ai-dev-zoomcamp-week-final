"""Thin HTTP layer of the auth module: sessions (docs/API.md section 7, task E7/A2).

Sign-in endpoints (e-mail link, registration, password) come with A3-A5 and end in
AuthService.start_session.
"""

import uuid

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from src.core.database import DbSession
from src.core.exceptions import AUTH_ERRORS, DomainError, domain_error_handler, error_responses
from src.core.security import TokenClaims, get_bearer_user
from src.modules.auth.policies import CSRF_HEADER, REFRESH_COOKIE
from src.modules.auth.schemas import SessionResponse, SessionView
from src.modules.auth.service import AuthService, clear_refresh_cookie, set_refresh_cookie
from src.modules.property.schemas import CurrentUserDTO

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["auth"],
    responses=error_responses({500: ["INTERNAL_SERVER_ERROR"]}),
)
CSRF_ERRORS = {403: ["CSRF_CHECK_FAILED"], 422: ["VALIDATION_ERROR"]}


def get_auth_service(request: Request, session: DbSession) -> AuthService:
    return AuthService(session, request.app.state.settings)


def require_csrf_header(
    x_requested_with: str | None = Header(default=None, alias=CSRF_HEADER),
) -> None:
    """Calls that use or set the refresh cookie must come from our own scripts: another site can
    send the cookie, but not a custom header without CORS, which this API never allows."""
    if not x_requested_with:
        raise DomainError(
            status_code=403,
            error_code="CSRF_CHECK_FAILED",
            message=f"The {CSRF_HEADER} header is required.",
        )


def _claims(request: Request) -> TokenClaims | None:
    return getattr(request.state, "auth_claims", None)


async def _error_clearing_cookie(request: Request, exc: DomainError) -> JSONResponse:
    response = await domain_error_handler(request, exc)
    clear_refresh_cookie(response)
    return response


@router.post(
    "/refresh",
    response_model=SessionResponse,
    responses=error_responses({401: ["SESSION_EXPIRED"], **CSRF_ERRORS}),
    dependencies=[Depends(require_csrf_header)],
)
async def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    auth: AuthService = Depends(get_auth_service),
) -> SessionResponse | JSONResponse:
    """A new access token for the refresh cookie, which is rotated (docs/API.md 7)."""
    try:
        issued = await auth.refresh(refresh_token)
    except DomainError as exc:
        if exc.error_code != "SESSION_EXPIRED":
            raise
        return await _error_clearing_cookie(request, exc)
    if issued.refresh_token is not None:  # None within the grace window: the cookie is current
        set_refresh_cookie(response, issued.refresh_token, issued.expires_at)
    return issued.response


@router.post("/logout", status_code=204, responses=error_responses(CSRF_ERRORS))
async def logout(
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    auth: AuthService = Depends(get_auth_service),
    _: None = Depends(require_csrf_header),
) -> Response:
    """End this session and clear the cookie; always 204."""
    await auth.logout(refresh_token)
    response = Response(status_code=204)
    clear_refresh_cookie(response)
    return response


@router.get(
    "/sessions",
    response_model=list[SessionView],
    responses=error_responses({401: AUTH_ERRORS, 422: ["VALIDATION_ERROR"]}),
)
async def list_sessions(
    request: Request,
    user: CurrentUserDTO = Depends(get_bearer_user),
    auth: AuthService = Depends(get_auth_service),
) -> list[SessionView]:
    """The caller's active sessions, newest first."""
    claims = _claims(request)
    return await auth.list_sessions(user.id, claims.session_id if claims else None)


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    responses=error_responses(
        {401: AUTH_ERRORS, 404: ["SESSION_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}
    ),
)
async def end_session(
    session_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_bearer_user),
    auth: AuthService = Depends(get_auth_service),
) -> Response:
    await auth.revoke_session(user.id, session_id)
    return Response(status_code=204)


@router.delete(
    "/sessions",
    status_code=204,
    responses=error_responses({401: AUTH_ERRORS, **CSRF_ERRORS}),
)
async def end_all_sessions(
    user: CurrentUserDTO = Depends(get_bearer_user),
    auth: AuthService = Depends(get_auth_service),
    _: None = Depends(require_csrf_header),
) -> Response:
    """Sign out everywhere, this device included."""
    await auth.revoke_all(user.id)
    response = Response(status_code=204)
    clear_refresh_cookie(response)
    return response
