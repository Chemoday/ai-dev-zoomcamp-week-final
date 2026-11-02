"""Errors shared by the flows of module auth (docs/API.md section 7, "Error codes")."""

from src.core.exceptions import DomainError


def invalid_link() -> DomainError:
    # One answer for unknown, used, expired or no longer applicable links: the cause is never told.
    return DomainError(
        status_code=400, error_code="INVALID_LINK", message="This link cannot be used."
    )


def rate_limited(wait: int) -> DomainError:
    return DomainError(
        status_code=429,
        error_code="RATE_LIMITED",
        message="Too many attempts; try again later.",
        extra={"retry_after_seconds": wait},
        headers={"Retry-After": str(wait)},
    )


def forbidden() -> DomainError:
    return DomainError(status_code=403, error_code="FORBIDDEN", message="You cannot do this.")


def not_found(error_code: str, message: str) -> DomainError:
    return DomainError(status_code=404, error_code=error_code, message=message)


def conflict(error_code: str, message: str) -> DomainError:
    return DomainError(status_code=409, error_code=error_code, message=message)


def invitation_not_found() -> DomainError:
    return not_found("INVITATION_NOT_FOUND", "No such invitation.")


def apartment_not_found() -> DomainError:
    return not_found("APARTMENT_NOT_FOUND", "No such apartment.")


def membership_not_found() -> DomainError:
    return not_found("MEMBERSHIP_NOT_FOUND", "No such membership.")


def user_not_found() -> DomainError:
    return not_found("USER_NOT_FOUND", "No such person.")
