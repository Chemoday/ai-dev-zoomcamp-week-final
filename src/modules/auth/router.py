"""Thin HTTP layer of the auth module (docs/API.md section 7): sessions (A2), e-mail links (A3),
invitations, registration, residents and the chairman's administration (A4), the password (A5).

Every sign-in ends in AuthService.start_session. E-mails go out by detached work after the
request's commit, so the plain token lives only in memory and never in a stored row.
"""

import uuid

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from src.core.database import DbSession, get_event_outbox
from src.core.event_bus import EventOutbox
from src.core.exceptions import AUTH_ERRORS, DomainError, domain_error_handler, error_responses
from src.core.security import TokenClaims, get_bearer_user
from src.modules.auth.accounts import AccountService
from src.modules.auth.invitations import InvitationService
from src.modules.auth.links import LinkService, deliver_magic_link
from src.modules.auth.mail import AuthMail, send_auth_mails
from src.modules.auth.passwords import PasswordService
from src.modules.auth.policies import CSRF_HEADER, REFRESH_COOKIE
from src.modules.auth.schemas import (
    AcceptRequest,
    AcceptResponse,
    InvitationCreate,
    InvitationView,
    IssuedSession,
    MagicLinkAccepted,
    MagicLinkRequest,
    PasswordLoginRequest,
    PasswordRemove,
    PasswordStatus,
    PasswordUpdate,
    PeopleResponse,
    RedeemRequest,
    RegistrationRequest,
    ReplaceTenantRequest,
    ResidentsResponse,
    RoleUpdate,
    RoleUpdateResponse,
    SessionResponse,
    SessionView,
    SignInResponse,
    SignInStatus,
)
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


def get_link_service(request: Request, session: DbSession) -> LinkService:
    return LinkService(session, request.app.state.settings)


def client_address(request: Request) -> str:
    """The caller's address as uvicorn saw it (X-Forwarded-For from Caddy, --proxy-headers)."""
    return request.client.host if request.client else "unknown"


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


# --- Sign in with an e-mail link (A3) -------------------------------------------------------------


@router.post(
    "/magic-link",
    status_code=202,
    response_model=MagicLinkAccepted,
    responses=error_responses({422: ["VALIDATION_ERROR"]}),
)
async def magic_link(body: MagicLinkRequest, request: Request) -> MagicLinkAccepted:
    """Always 202: the link is sent by detached work, if the e-mail has an account and no silent
    limit applies, so the answer and its timing are the same for every e-mail."""
    request.app.state.auth_background.spawn(
        deliver_magic_link(request.app, body.email, client_address(request)), name="magic-link"
    )
    return MagicLinkAccepted()


@router.post(
    "/magic-link/redeem",
    response_model=SignInResponse,
    responses=error_responses({400: ["INVALID_LINK"], 429: ["RATE_LIMITED"], **CSRF_ERRORS}),
    dependencies=[Depends(require_csrf_header)],
)
async def redeem_link(
    body: RedeemRequest,
    request: Request,
    response: Response,
    links: LinkService = Depends(get_link_service),
) -> SignInResponse:
    """Redeem an e-mail link. A sign-in link signs in and sets the refresh cookie; an invitation
    link answers REGISTRATION_REQUIRED or SIGN_IN_TO_ACCEPT, without a session."""
    answer, issued = await links.redeem(
        body.token, client_address(request), request.headers.get("user-agent")
    )
    if issued is not None:
        _set_new_session_cookie(response, issued)
    return answer


def _set_new_session_cookie(response: Response, issued: IssuedSession) -> None:
    if issued.refresh_token is None:  # a new session always has one
        raise RuntimeError("a new session has no refresh token")
    set_refresh_cookie(response, issued.refresh_token, issued.expires_at)


# --- Invitations and registration (A4) -----------------------------------------------------------

SIGNED_IN = {401: AUTH_ERRORS, 422: ["VALIDATION_ERROR"]}
INVITE_ERRORS = {
    **SIGNED_IN,
    403: ["FORBIDDEN"],
    404: ["APARTMENT_NOT_FOUND"],
    409: ["TENANT_LIMIT_REACHED", "INVITATION_ALREADY_PENDING"],
    429: ["RATE_LIMITED"],
}


def get_invitation_service(request: Request, session: DbSession) -> InvitationService:
    return InvitationService(session, request.app.state.settings)


def get_account_service(
    request: Request, session: DbSession, outbox: EventOutbox = Depends(get_event_outbox)
) -> AccountService:
    return AccountService(session, request.app.state.settings, outbox)


async def _send_after_commit(request: Request, session: DbSession, mails: list[AuthMail]) -> None:
    """Commit first, so a link in an e-mail always exists; then send by detached work."""
    await session.commit()
    app = request.app
    app.state.auth_background.spawn(
        send_auth_mails(app.state.plugin_registry, app.state.settings, mails),
        name="auth-mail",
    )


@router.post(
    "/invitations",
    status_code=201,
    response_model=InvitationView,
    responses=error_responses(INVITE_ERRORS),
)
async def create_invitation(
    body: InvitationCreate,
    request: Request,
    session: DbSession,
    user: CurrentUserDTO = Depends(get_bearer_user),
    invitations: InvitationService = Depends(get_invitation_service),
) -> InvitationView:
    """Invite an owner or staff (the chairman) or a tenant (an owner of the apartment). The
    answer is the same whether the e-mail has an account."""
    view, mail = await invitations.create(user, body)
    await _send_after_commit(request, session, [mail])
    return view


@router.get(
    "/invitations", response_model=list[InvitationView], responses=error_responses(SIGNED_IN)
)
async def list_invitations(
    user: CurrentUserDTO = Depends(get_bearer_user),
    invitations: InvitationService = Depends(get_invitation_service),
) -> list[InvitationView]:
    """The chairman: the building's invitations; an owner: the TENANT invitations of their
    apartments. Newest first."""
    return await invitations.list_for(user)


@router.post(
    "/invitations/accept",
    response_model=AcceptResponse,
    responses=error_responses({**SIGNED_IN, 400: ["INVALID_LINK"]}),
)
async def accept_invitation(
    body: AcceptRequest,
    user: CurrentUserDTO = Depends(get_bearer_user),
    invitations: InvitationService = Depends(get_invitation_service),
) -> AcceptResponse:
    """An existing account accepts an invitation after SIGN_IN_TO_ACCEPT."""
    return AcceptResponse(applied=await invitations.accept(user, body.token))


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=InvitationView,
    responses=error_responses(
        {
            **SIGNED_IN,
            404: ["INVITATION_NOT_FOUND"],
            409: ["INVITATION_ACCEPTED", "INVITATION_NO_LONGER_VALID", "TENANT_LIMIT_REACHED"],
            429: ["RATE_LIMITED"],
        }
    ),
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: CurrentUserDTO = Depends(get_bearer_user),
    invitations: InvitationService = Depends(get_invitation_service),
) -> InvitationView:
    view, mail = await invitations.resend(user, invitation_id)
    await _send_after_commit(request, session, [mail])
    return view


@router.delete(
    "/invitations/{invitation_id}",
    status_code=204,
    responses=error_responses(
        {**SIGNED_IN, 404: ["INVITATION_NOT_FOUND"], 409: ["INVITATION_ACCEPTED"]}
    ),
)
async def delete_invitation(
    invitation_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_bearer_user),
    invitations: InvitationService = Depends(get_invitation_service),
) -> Response:
    await invitations.delete(user, invitation_id)
    return Response(status_code=204)


@router.post(
    "/registration",
    response_model=SignInResponse,
    responses=error_responses(
        {400: ["INVALID_LINK"], 409: ["EMAIL_ALREADY_REGISTERED"], **CSRF_ERRORS}
    ),
    dependencies=[Depends(require_csrf_header)],
)
async def register(
    body: RegistrationRequest,
    request: Request,
    response: Response,
    invitations: InvitationService = Depends(get_invitation_service),
) -> SignInResponse:
    """Complete the registration of an invited person and sign them in (sets the cookie)."""
    issued = await invitations.register(body, request.headers.get("user-agent"))
    _set_new_session_cookie(response, issued)
    return SignInResponse(status=SignInStatus.SIGNED_IN, session=issued.response)


# --- Residents, roles and closing accounts (A4) --------------------------------------------------


@router.get(
    "/apartments/{apartment_id}/residents",
    response_model=ResidentsResponse,
    responses=error_responses({**SIGNED_IN, 403: ["FORBIDDEN"]}),
)
async def apartment_residents(
    apartment_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_bearer_user),
    accounts: AccountService = Depends(get_account_service),
) -> ResidentsResponse:
    """For "My residents": an owner of the apartment, or the chairman."""
    return await accounts.residents(user, apartment_id)


@router.delete(
    "/apartments/{apartment_id}/tenants/{user_id}",
    status_code=204,
    responses=error_responses({**SIGNED_IN, 403: ["FORBIDDEN"], 404: ["MEMBERSHIP_NOT_FOUND"]}),
)
async def remove_tenant(
    apartment_id: uuid.UUID,
    user_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_bearer_user),
    accounts: AccountService = Depends(get_account_service),
) -> Response:
    await accounts.remove_tenant(user, apartment_id, user_id)
    return Response(status_code=204)


@router.post(
    "/apartments/{apartment_id}/tenants/{user_id}/replace",
    status_code=201,
    response_model=InvitationView,
    responses=error_responses(
        {
            **SIGNED_IN,
            403: ["FORBIDDEN"],
            404: ["MEMBERSHIP_NOT_FOUND"],
            409: ["TENANT_LIMIT_REACHED", "INVITATION_ALREADY_PENDING"],
            429: ["RATE_LIMITED"],
        }
    ),
)
async def replace_tenant(
    apartment_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ReplaceTenantRequest,
    request: Request,
    session: DbSession,
    user: CurrentUserDTO = Depends(get_bearer_user),
    accounts: AccountService = Depends(get_account_service),
) -> InvitationView:
    """End a tenant's membership and invite the new e-mail, in one transaction."""
    view, mail = await accounts.replace_tenant(user, apartment_id, user_id, body.email)
    await _send_after_commit(request, session, [mail])
    return view


@router.get(
    "/users",
    response_model=PeopleResponse,
    responses=error_responses({**SIGNED_IN, 403: ["FORBIDDEN"]}),
)
async def list_people(
    user: CurrentUserDTO = Depends(get_bearer_user),
    accounts: AccountService = Depends(get_account_service),
) -> PeopleResponse:
    """The chairman's list of people, under the badge rules (no name beyond consent)."""
    return await accounts.people(user)


@router.delete(
    "/users/{user_id}/memberships/{membership_id}",
    status_code=204,
    responses=error_responses(
        {**SIGNED_IN, 403: ["FORBIDDEN"], 404: ["MEMBERSHIP_NOT_FOUND", "USER_NOT_FOUND"]}
    ),
)
async def end_membership(
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_bearer_user),
    accounts: AccountService = Depends(get_account_service),
) -> Response:
    await accounts.end_membership(user, user_id, membership_id)
    return Response(status_code=204)


@router.put(
    "/users/{user_id}/role",
    response_model=RoleUpdateResponse,
    responses=error_responses(
        {**SIGNED_IN, 403: ["FORBIDDEN"], 404: ["USER_NOT_FOUND"], 409: ["LAST_CHAIRMAN"]}
    ),
)
async def set_role(
    user_id: uuid.UUID,
    body: RoleUpdate,
    user: CurrentUserDTO = Depends(get_bearer_user),
    accounts: AccountService = Depends(get_account_service),
) -> RoleUpdateResponse:
    return await accounts.set_role(user, user_id, body.building_role)


@router.delete(
    "/users/{user_id}",
    status_code=204,
    responses=error_responses(
        {**SIGNED_IN, 403: ["FORBIDDEN"], 404: ["USER_NOT_FOUND"], 409: ["LAST_CHAIRMAN"]}
    ),
)
async def close_account(
    user_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_bearer_user),
    accounts: AccountService = Depends(get_account_service),
) -> Response:
    """Close an account: signed out everywhere, personal data removed."""
    await accounts.close(user, user_id)
    return Response(status_code=204)


# --- Password (A5) -------------------------------------------------------------------------------


def get_password_service(request: Request, session: DbSession) -> PasswordService:
    return PasswordService(session, request.app.state.settings, request.app.state.auth_passwords)


REAUTH_ERRORS = {
    **SIGNED_IN,
    403: ["REAUTHENTICATION_REQUIRED"],
    422: ["VALIDATION_ERROR", "PASSWORD_TOO_WEAK"],
    429: ["RATE_LIMITED"],
}


@router.post(
    "/password/login",
    response_model=SessionResponse,
    responses=error_responses({401: ["INVALID_CREDENTIALS"], 429: ["RATE_LIMITED"], **CSRF_ERRORS}),
    dependencies=[Depends(require_csrf_header)],
)
async def password_login(
    body: PasswordLoginRequest,
    request: Request,
    response: Response,
    passwords: PasswordService = Depends(get_password_service),
) -> SessionResponse:
    """Sign in with the e-mail and the password; one generic 401 for every failure."""
    issued = await passwords.login(
        body.email, body.password, client_address(request), request.headers.get("user-agent")
    )
    _set_new_session_cookie(response, issued)
    return issued.response


@router.get("/password", response_model=PasswordStatus, responses=error_responses(SIGNED_IN))
async def password_status(
    user: CurrentUserDTO = Depends(get_bearer_user),
    passwords: PasswordService = Depends(get_password_service),
) -> PasswordStatus:
    """Whether the caller's own account has a password (the Security page)."""
    return PasswordStatus(is_set=await passwords.is_set(user.id))


@router.put("/password", status_code=204, responses=error_responses(REAUTH_ERRORS))
async def set_password(
    body: PasswordUpdate,
    request: Request,
    session: DbSession,
    user: CurrentUserDTO = Depends(get_bearer_user),
    passwords: PasswordService = Depends(get_password_service),
) -> Response:
    """Set or change the password: a recent sign-in or the current password. The other sessions
    end and a notice goes to the e-mail."""
    mail = await passwords.set_password(
        user, _claims(request), body.new_password, body.current_password, client_address(request)
    )
    await _send_after_commit(request, session, [mail] if mail else [])
    return Response(status_code=204)


@router.delete("/password", status_code=204, responses=error_responses(REAUTH_ERRORS))
async def remove_password(
    request: Request,
    session: DbSession,
    body: PasswordRemove | None = None,
    user: CurrentUserDTO = Depends(get_bearer_user),
    passwords: PasswordService = Depends(get_password_service),
) -> Response:
    """Remove the password, so only the e-mail link signs in."""
    mail = await passwords.remove_password(
        user,
        _claims(request),
        body.current_password if body else None,
        client_address(request),
    )
    await _send_after_commit(request, session, [mail] if mail else [])
    return Response(status_code=204)
