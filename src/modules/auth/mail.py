"""E-mails of module auth (docs/ARCHITECTURE.md 4.5).

Sent through the e-mail plugin directly, not through notifications: signing in must not depend on
NOTIFICATIONS_ENABLED, and a token must never be stored in a delivery row. They are transactional
and carry no unsubscribe link. The texts come from src/modules/auth/messages.csv.
"""

from functools import cache
from pathlib import Path

import structlog

from src.core.config import Settings
from src.core.messages import MessageCatalog
from src.modules.auth.policies import demo_mail_allowed
from src.modules.property.schemas import Locale
from src.plugins.registry import OutgoingMessage, PluginRegistry

CATALOG_FILE = Path(__file__).with_name("messages.csv")
EMAIL_CHANNEL = "email"
logger = structlog.get_logger(__name__)


@cache
def auth_catalog() -> MessageCatalog:
    return MessageCatalog.from_csv(CATALOG_FILE, [locale.value for locale in Locale])


def mail_allowed(settings: Settings, email: str) -> bool:
    """Demo mode sends login e-mails only with AUTH_MAIL_IN_DEMO (anyone can act as anyone)."""
    if not settings.DEMO_MODE_ENABLED:
        return True
    return settings.AUTH_MAIL_IN_DEMO and demo_mail_allowed(email, settings.AUTH_DEMO_MAIL_DOMAINS)


async def send_auth_email(
    plugins: PluginRegistry,
    settings: Settings,
    *,
    email: str,
    locale: str,
    kind: str,
    link: str,
) -> bool:
    """Send one login e-mail (`kind` names its texts in the catalog); True when it went out.

    The address, the link and the texts never reach a log line: only the kind and the error code.
    """
    channel = plugins.channel(EMAIL_CHANNEL)
    if channel is None:
        logger.warning("auth_mail_unavailable", kind=kind)
        return False
    try:
        catalog = auth_catalog()
        message = OutgoingMessage(
            address={"email": email},
            locale=locale,
            event_type=f"AUTH_{kind.upper()}",
            subject=catalog.render(f"{kind}.subject", locale),
            body=catalog.render(f"{kind}.text", locale, link=link),
            footer=catalog.render("footer", locale, site_url=settings.PUBLIC_BASE_URL),
            deep_link=link,
        )
        result = await channel.send(message)
    except Exception:  # a plugin bug must not turn into a 500 of an already answered request
        logger.exception("auth_mail_failed", kind=kind)
        return False
    if not result.ok:
        logger.warning("auth_mail_failed", kind=kind, error_code=result.error_code)
    return result.ok
