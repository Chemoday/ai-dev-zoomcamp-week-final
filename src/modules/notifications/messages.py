"""Texts of messages sent outside the app (pure functions; docs/ARCHITECTURE.md 3.2).

Two kinds (owner decision, E1 section 5):
- custom: an emergency is the post's title and body exactly as the board wrote them;
- system: every other text comes from messages.csv (`key,en,ru,et`), in the delivery's locale,
  with English when a cell is empty. The chairman cannot change them.

Placeholders are `{name}` and are substituted once, so text that people wrote is never parsed as a
template (a title containing "{body}" stays literal).
"""

import csv
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.modules.notifications.schemas import NotificationEventType
from src.modules.property.schemas import Locale

CATALOG_FILE = Path(__file__).with_name("messages.csv")
FALLBACK_LOCALE = Locale.EN
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
SIGNATURE_SEPARATOR = "\n\n-- \n"  # the standard e-mail signature delimiter, before the footer


class MessageCatalogError(ValueError):
    pass


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER.findall(template))


@dataclass(frozen=True)
class MessageCatalog:
    texts: Mapping[str, Mapping[str, str]]  # key -> locale -> template

    @classmethod
    def from_csv(cls, path: Path) -> "MessageCatalog":
        with path.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            columns = [c for c in reader.fieldnames or [] if c != "key"]
            if set(columns) != {locale.value for locale in Locale}:
                raise MessageCatalogError(f"{path.name}: columns {columns} do not match Locale")
            texts: dict[str, dict[str, str]] = {}
            for row in reader:
                key = row["key"]
                if key in texts:
                    raise MessageCatalogError(f"{path.name}: duplicate key {key!r}")
                english = row[FALLBACK_LOCALE.value]
                if not english:
                    raise MessageCatalogError(f"{path.name}: {key!r} has no English text")
                for locale in columns:
                    if row[locale] and placeholders(row[locale]) != placeholders(english):
                        raise MessageCatalogError(
                            f"{path.name}: {key!r} [{locale}] uses other placeholders than en"
                        )
                texts[key] = {locale: row[locale] for locale in columns}
        return cls(texts)

    def render(self, key: str, locale: str, **values: Any) -> str:
        cells = self.texts[key]
        template = cells.get(locale) or cells[FALLBACK_LOCALE.value]
        return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), template)


@cache
def default_catalog() -> MessageCatalog:
    return MessageCatalog.from_csv(CATALOG_FILE)


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    body: str


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
    return RenderedMessage(subject=subject, body=f"{text}{SIGNATURE_SEPARATOR}{footer}")
