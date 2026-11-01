"""Texts of messages sent outside the app (pure functions; docs/ARCHITECTURE.md 3.2).

Two kinds (owner decision, E1 section 5):
- custom: an emergency is the post's title and body exactly as the board wrote them;
- system: every other text comes from messages.csv (`key,en,ru,et`), in the delivery's locale,
  with English when a cell is empty. The chairman cannot change them.

The catalog format and its loader live in src/core/messages.py (shared with module auth).
"""

__all__ = ["MessageCatalog", "MessageCatalogError", "compose", "default_catalog", "placeholders"]

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.core.messages import MessageCatalog, MessageCatalogError, placeholders
from src.modules.notifications.schemas import NotificationEventType
from src.modules.property.schemas import Locale
from src.plugins.registry import SIGNATURE_SEPARATOR

CATALOG_FILE = Path(__file__).with_name("messages.csv")
LOCALES = tuple(locale.value for locale in Locale)


@cache
def default_catalog() -> MessageCatalog:
    return MessageCatalog.from_csv(CATALOG_FILE, LOCALES)


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    text: str
    footer: str

    @property
    def body(self) -> str:
        """The text and the footer, as e-mail and Telegram show them."""
        return f"{self.text}{SIGNATURE_SEPARATOR}{self.footer}"


def _target(catalog: MessageCatalog, locale: str, params: Mapping[str, Any]) -> str:
    scope = params.get("target_scope") or "ALL_BUILDING"
    return catalog.render(f"target.{scope}", locale, entrance=params.get("target_entrance"))


def _local_date(value: str, timezone: str) -> str:
    return datetime.fromisoformat(value).astimezone(ZoneInfo(timezone)).strftime("%d.%m.%Y")


def compose(
    event_type: NotificationEventType,
    locale: str,
    params: Mapping[str, Any],
    *,
    site_url: str,
    timezone: str,
    title: str = "",
    body: str = "",
    catalog: MessageCatalog | None = None,
) -> RenderedMessage:
    """Subject and plain-text body of one external message.

    `title` and `body` are the emergency's own text (EMERGENCY_* only); everything else comes
    from the catalog. Every message ends with the footer that links to the site.
    """
    catalog = catalog or default_catalog()
    E = NotificationEventType
    subject_key = f"subject.{event_type.value}"
    if event_type == E.EMERGENCY_PUBLISHED:
        subject = catalog.render(subject_key, locale, title=title)
        affected = catalog.render("line.affected", locale, target=_target(catalog, locale, params))
        text = f"{body}\n\n{affected}"
    elif event_type == E.EMERGENCY_RESOLVED:
        subject = catalog.render(subject_key, locale, title=title)
        affected = catalog.render("line.affected", locale, target=_target(catalog, locale, params))
        text = f"{catalog.render('text.EMERGENCY_RESOLVED', locale, title=title)}\n\n{affected}"
    elif event_type == E.READING_DEADLINE:
        period_code = params["period_code"]
        subject = catalog.render(subject_key, locale, period_code=period_code)
        text = catalog.render(
            "text.READING_DEADLINE",
            locale,
            period_code=period_code,
            due_date=_local_date(params["due_date"], timezone),
        )
    else:  # PERIOD_REVIEW
        period_code = params["period_code"]
        subject = catalog.render(subject_key, locale, period_code=period_code)
        text = catalog.render("text.PERIOD_REVIEW", locale, period_code=period_code)
    footer = catalog.render("footer", locale, site_url=site_url)
    # A subject is one line: a title may contain line breaks (also U+2028), which a mail header
    # cannot carry, and the mandatory alert must not fail because of them.
    subject = " ".join(subject.split())
    return RenderedMessage(subject=subject, text=text, footer=footer)
