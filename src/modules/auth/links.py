"""Sign-in links: issue after the response, redeem for a session (task E7/A3, docs/API.md 7).

`POST /auth/magic-link` answers 202 at once and does the work in a background task, so neither the
answer nor its timing tells whether an e-mail has an account. The limits are silent: over a limit
nothing is sent. Counting the e-mail together with the IP address means someone else's requests
cannot use up a person's links; the daily cap per e-mail stops mail bombing.

Tokens are 32 random bytes; only their keyed hash is stored. Nothing personal is logged.
"""

import secrets
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import FastAPI
from opentelemetry import metrics, trace
from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.exceptions import DomainError
from src.modules.auth.mail import mail_allowed, send_auth_email
from src.modules.auth.models import AuthAttempt, AuthToken
from src.modules.auth.policies import (
    ATTEMPT_RETENTION,
    LINKS_PER_EMAIL_AND_IP_PER_HOUR,
    LINKS_PER_EMAIL_PER_DAY,
    LINKS_PER_IP_PER_HOUR,
    MAGIC_LINK_TTL,
    REDEEMS_PER_IP_PER_HOUR,
    email_subject,
    ip_subject,
    keyed_hash,
    pair_subject,
    sign_in_link,
)
from src.modules.auth.schemas import AttemptKind, AuthMethod, IssuedSession, TokenPurpose
from src.modules.auth.service import AuthService
from src.modules.property.service import PropertyService
from src.plugins.registry import PluginRegistry

logger = structlog.get_logger(__name__)
_meter = metrics.get_meter(__name__)
_sign_ins = _meter.create_counter(
    "auth.sign_ins", description="Sign-in attempts by method and outcome (signed_in, failed)"
)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(UTC)


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


class LinkService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _hash(self, value: str) -> str:
        return keyed_hash(self.settings.AUTH_HASH_SECRET.get_secret_value(), value)

    async def _attempts_since(
        self, kind: AttemptKind, subject: str, since: datetime
    ) -> list[datetime]:
        rows = await self.session.scalars(
            select(AuthAttempt.attempted_at)
            .where(
                AuthAttempt.kind == kind,
                AuthAttempt.subject_hash == self._hash(subject),
                AuthAttempt.attempted_at > since,
            )
            .order_by(AuthAttempt.attempted_at)
        )
        return list(rows)

    def _record(self, kind: AttemptKind, subject: str, succeeded: bool, now: datetime) -> None:
        self.session.add(
            AuthAttempt(
                kind=kind, subject_hash=self._hash(subject), succeeded=succeeded, attempted_at=now
            )
        )

    # --- Issuing --------------------------------------------------------------------------------

    async def issue_magic_link(self, email: str, address: str) -> tuple[str, str] | None:
        """A new sign-in token and the language for its e-mail, or None (nothing to send).

        Runs after the response, so the cost of each branch is invisible to the caller.
        """
        now = _now()
        # One request per address and per e-mail at a time (always the address first, so two
        # requests never wait for each other in opposite orders): counts and inserts cannot
        # interleave, also for many e-mails from one address.
        for subject in (ip_subject(address), email_subject(email)):
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"auth:magic-link:{self._hash(subject)}"},
            )
        limits = (
            (pair_subject(email, address), now - HOUR, LINKS_PER_EMAIL_AND_IP_PER_HOUR),
            (email_subject(email), now - DAY, LINKS_PER_EMAIL_PER_DAY),
            (ip_subject(address), now - HOUR, LINKS_PER_IP_PER_HOUR),
        )
        for subject, since, limit in limits:
            if len(await self._attempts_since(AttemptKind.MAGIC_LINK, subject, since)) >= limit:
                return None  # silent: still 202, nothing sent
        user = await PropertyService(self.session).find_active_user_by_email(email)
        for subject, _, _ in limits:
            self._record(AttemptKind.MAGIC_LINK, subject, user is not None, now)
        if user is None:
            return None
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self.session.add(
            AuthToken(
                purpose=TokenPurpose.MAGIC_LINK,
                token_hash=self._hash(token),
                user_id=user.user_id,
                created_at=now,
                expires_at=now + MAGIC_LINK_TTL,
            )
        )
        await self.session.flush()
        return token, (user.locale or self.settings.DEFAULT_LOCALE)

    # --- Redeeming ------------------------------------------------------------------------------

    async def redeem(self, token: str, address: str, user_agent: str | None) -> IssuedSession:
        now = _now()
        recent = await self._attempts_since(AttemptKind.REDEEM, ip_subject(address), now - HOUR)
        span = trace.get_current_span()
        span.set_attribute("auth.method", AuthMethod.MAGIC_LINK.value)
        if len(recent) >= REDEEMS_PER_IP_PER_HOUR:
            wait = max(int((recent[0] + HOUR - now).total_seconds()), 1)
            span.set_attribute("auth.outcome", "throttled")
            _sign_ins.add(1, {"method": AuthMethod.MAGIC_LINK.value, "outcome": "throttled"})
            raise rate_limited(wait)
        row = await self.session.scalar(
            select(AuthToken)
            .where(
                AuthToken.token_hash == self._hash(token),
                AuthToken.purpose == TokenPurpose.MAGIC_LINK,
            )
            .with_for_update()
        )
        usable = (
            row is not None
            and row.used_at is None
            and row.expires_at > now
            and row.user_id is not None
            and await PropertyService(self.session).is_user_active(row.user_id)
        )
        self._record(AttemptKind.REDEEM, ip_subject(address), usable, now)
        if not usable or row is None or row.user_id is None:
            # Committed here, so the failed attempt counts even though the 400 rolls back.
            await self.session.commit()
            span.set_attribute("auth.outcome", "failed")
            _sign_ins.add(1, {"method": AuthMethod.MAGIC_LINK.value, "outcome": "failed"})
            raise invalid_link()
        row.used_at = now
        issued = await AuthService(self.session, self.settings).start_session(
            row.user_id, AuthMethod.MAGIC_LINK, user_agent
        )
        span.set_attribute("auth.outcome", "signed_in")
        _sign_ins.add(1, {"method": AuthMethod.MAGIC_LINK.value, "outcome": "signed_in"})
        return issued


async def deliver_magic_link(app: FastAPI, email: str, address: str) -> None:
    """The background part of POST /auth/magic-link: issue (with the limits), commit, then send.

    The plain token exists only in memory between the commit and the e-mail.
    """
    settings: Settings = app.state.settings
    plugins: PluginRegistry = app.state.plugin_registry
    if not mail_allowed(settings, email) or plugins.channel("email") is None:
        return  # demo mode without AUTH_MAIL_IN_DEMO, or no e-mail plugin: nothing to send
    try:
        async with app.state.auth_session_factory() as session:
            issued = await LinkService(session, settings).issue_magic_link(email, address)
            await session.commit()
    except Exception as exc:  # the request was already answered; the type only, no traceback text
        logger.error("auth_magic_link_failed", error_type=type(exc).__name__)
        return
    if issued is None:
        return
    token, locale = issued
    await send_auth_email(
        plugins,
        settings,
        email=email,
        locale=locale,
        kind="magic_link",
        link=sign_in_link(settings.PUBLIC_BASE_URL, token),
    )


async def delete_old_rows(session: AsyncSession, now: datetime) -> dict[str, int]:
    """For the clean-up loop: used or expired links, and attempts older than a day."""
    tokens = await session.execute(
        delete(AuthToken)
        .where(or_(AuthToken.used_at.is_not(None), AuthToken.expires_at < now))
        .execution_options(synchronize_session=False)
    )
    attempts = await session.execute(
        delete(AuthAttempt)
        .where(AuthAttempt.attempted_at < now - ATTEMPT_RETENTION)
        .execution_options(synchronize_session=False)
    )
    # "links", not "tokens": a key containing "token" would be redacted in the log line.
    return {"links": tokens.rowcount or 0, "attempts": attempts.rowcount or 0}
