"""Authentication (Bearer JWT, demo impersonation header) and building-role authorization."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import jwt
import structlog
from fastapi import Depends
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from opentelemetry import trace

from src.core.config import Settings, get_settings
from src.core.exceptions import DomainError
from src.modules.property.schemas import BuildingRole, CurrentUserDTO
from src.modules.property.service import PropertyService, get_property_service

DEMO_USER_HEADER = "X-Demo-User-Id"

# auto_error=False: missing credentials are reported as our own JSON 401, not FastAPI's 403.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token (sub = user id).")
demo_user_header = APIKeyHeader(
    name=DEMO_USER_HEADER,
    auto_error=False,
    description="Demo mode only: act as any seeded user by id.",
)


def _unauthorized(error_code: str, message: str) -> DomainError:
    return DomainError(
        status_code=401,
        error_code=error_code,
        message=message,
        headers={"WWW-Authenticate": "Bearer"},  # RFC 6750
    )


def create_access_token(
    user_id: uuid.UUID, settings: Settings, expires_in: timedelta | None = None
) -> str:
    if not settings.JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY is not configured")
    now = datetime.now(UTC)
    ttl = (
        expires_in
        if expires_in is not None
        else timedelta(minutes=settings.JWT_ACCESS_TOKEN_TTL_MINUTES)
    )
    claims = {"sub": str(user_id), "iat": now, "exp": now + ttl, "typ": "access"}
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    if not settings.JWT_SECRET_KEY:
        raise _unauthorized("INVALID_TOKEN", "Bearer authentication is not enabled.")
    try:
        claims = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        if claims.get("typ") != "access":
            raise jwt.InvalidTokenError("not an access token")
        return uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise _unauthorized("INVALID_TOKEN", "The access token is invalid or expired.") from exc


async def get_current_user(
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    demo_user_id: str | None = Depends(demo_user_header),
    settings: Settings = Depends(get_settings),
    property_service: PropertyService = Depends(get_property_service),
) -> CurrentUserDTO:
    """Resolve the caller. A Bearer token wins over the demo header if both are present."""
    if bearer is not None:
        user_id = decode_access_token(bearer.credentials, settings)
    elif demo_user_id is not None and settings.DEMO_MODE_ENABLED:
        try:
            user_id = uuid.UUID(demo_user_id)
        except ValueError as exc:
            raise _unauthorized("UNKNOWN_USER", f"{DEMO_USER_HEADER} is not a valid id.") from exc
    else:
        raise _unauthorized("NOT_AUTHENTICATED", "Authentication is required.")

    current_user = await property_service.get_current_user(user_id)
    if current_user is None:
        raise _unauthorized("UNKNOWN_USER", "The user does not exist or is deactivated.")

    # Pseudonymous id and role only: never names, emails or phones (see AGENTS.md guardrails).
    trace.get_current_span().set_attributes(
        {"enduser.id": str(current_user.id), "enduser.role": current_user.building_role.value}
    )
    structlog.contextvars.bind_contextvars(user_id=str(current_user.id))
    return current_user


def require_building_roles(
    *roles: BuildingRole,
) -> Callable[..., Awaitable[CurrentUserDTO]]:
    """Dependency factory: `Depends(require_building_roles(BuildingRole.CHAIRMAN, ...))`."""
    allowed = frozenset(roles)

    async def dependency(
        current_user: CurrentUserDTO = Depends(get_current_user),
    ) -> CurrentUserDTO:
        if current_user.building_role not in allowed:
            raise DomainError(
                status_code=403,
                error_code="FORBIDDEN",
                message="Your building role does not allow this action.",
                extra={"required_roles": sorted(role.value for role in allowed)},
            )
        return current_user

    return dependency
