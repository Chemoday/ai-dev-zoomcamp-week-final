"""Errors shared by the flows of module auth (docs/API.md section 7, "Error codes")."""

from src.core.exceptions import DomainError
from src.modules.auth.schemas import PasswordWeakness


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


def invalid_credentials() -> DomainError:
    # One answer for every failed password login: the cause is never told.
    return DomainError(
        status_code=401,
        error_code="INVALID_CREDENTIALS",
        message="The e-mail or the password is wrong.",
    )


def reauthentication_required() -> DomainError:
    return DomainError(
        status_code=403,
        error_code="REAUTHENTICATION_REQUIRED",
        message="Enter the current password, or sign in again with an e-mail link.",
    )


def password_too_weak(reason: PasswordWeakness) -> DomainError:
    return DomainError(
        status_code=422,
        error_code="PASSWORD_TOO_WEAK",
        message="Choose a longer or less common password; a phrase of a few words works well.",
        extra={"reason": reason.value},
    )


def unknown_user() -> DomainError:
    # The same answer get_current_user gives for a closed account.
    return DomainError(
        status_code=401,
        error_code="UNKNOWN_USER",
        message="The user does not exist or is deactivated.",
        headers={"WWW-Authenticate": "Bearer"},
    )
