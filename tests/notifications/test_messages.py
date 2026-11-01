"""System texts of external messages (messages.csv) and how a message is composed (no database)."""

from pathlib import Path

import pytest

from src.modules.notifications.messages import (
    SIGNATURE_SEPARATOR,
    MessageCatalog,
    MessageCatalogError,
    compose,
    default_catalog,
)
from src.modules.notifications.schemas import NotificationEventType
from src.modules.property.schemas import Locale

SITE = "https://kyu.example.test"
TZ = "Europe/Tallinn"
E = NotificationEventType
ENTRANCE_2 = {"category": "WATER_SHUTOFF", "target_scope": "ENTRANCE", "target_entrance": 2}


def test_the_catalog_has_a_column_per_locale_and_english_everywhere() -> None:
    catalog = default_catalog()  # loading validates columns, English cells and placeholders

    for key, cells in catalog.texts.items():
        assert set(cells) == {locale.value for locale in Locale}, key
        assert cells["en"], key


@pytest.mark.parametrize("locale", [locale.value for locale in Locale])
@pytest.mark.parametrize(
    ("event_type", "params"),
    [
        (E.EMERGENCY_PUBLISHED, ENTRANCE_2),
        (E.EMERGENCY_RESOLVED, {**ENTRANCE_2, "target_scope": "ALL_BUILDING"}),
        (E.READING_DEADLINE, {"period_code": "2026-11", "due_date": "2026-11-04T21:59:59+00:00"}),
        (E.PERIOD_REVIEW, {"period_code": "2026-11"}),
    ],
)
def test_every_event_renders_in_every_language_with_the_footer(
    event_type: NotificationEventType, params: dict, locale: str
) -> None:
    message = compose(
        event_type, locale, params, site_url=SITE, timezone=TZ, title="Water off", body="Riser."
    )

    assert message.subject
    assert "{" not in message.subject + message.body, "every placeholder is filled"
    text, footer = message.body.split(SIGNATURE_SEPARATOR)
    assert SITE in footer
    assert text


def test_an_emergency_is_the_boards_own_text_plus_the_target_line() -> None:
    message = compose(
        E.EMERGENCY_PUBLISHED,
        "en",
        ENTRANCE_2,
        site_url=SITE,
        timezone=TZ,
        title="Cold water off until 18:00",
        body="Vesi on kinni. Water is off.",  # any language, or several, as written
    )

    assert message.subject == "Emergency: Cold water off until 18:00"
    assert message.body.startswith("Vesi on kinni. Water is off.\n\nAffected: entrance 2")


def test_texts_people_wrote_are_never_parsed_as_templates() -> None:
    message = compose(
        E.EMERGENCY_PUBLISHED,
        "en",
        {"target_scope": "ALL_BUILDING"},
        site_url=SITE,
        timezone=TZ,
        title="{body} {site_url}",
        body="{title}",
    )

    assert message.subject == "Emergency: {body} {site_url}"
    assert message.body.startswith("{title}\n\nAffected: the whole building")


def test_the_due_date_is_shown_as_the_local_date() -> None:
    # 21:59:59 UTC is 23:59:59 in Tallinn: still the 4th there.
    message = compose(
        E.READING_DEADLINE,
        "en",
        {"period_code": "2026-11", "due_date": "2026-11-04T21:59:59+00:00"},
        site_url=SITE,
        timezone=TZ,
    )

    assert "by 04.11.2026" in message.body


def write_catalog(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "messages.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_empty_cell_falls_back_to_english(tmp_path: Path) -> None:
    catalog = MessageCatalog.from_csv(write_catalog(tmp_path, "key,en,ru,et\nhello,Hi {name},,\n"))

    assert catalog.render("hello", "et", name="Mari") == "Hi Mari"


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("key,en,ru\nhello,Hi,\n", "do not match Locale"),
        ("key,en,ru,et\nhello,,Privet,Tere\n", "no English text"),
        ("key,en,ru,et\nhello,Hi {name},Privet,\n", "other placeholders"),
        ("key,en,ru,et\nhello,Hi,,\nhello,Hey,,\n", "duplicate key"),
    ],
)
def test_a_broken_catalog_is_rejected(tmp_path: Path, text: str, error: str) -> None:
    with pytest.raises(MessageCatalogError, match=error):
        MessageCatalog.from_csv(write_catalog(tmp_path, text), [locale.value for locale in Locale])


def test_a_title_with_line_breaks_becomes_a_one_line_subject() -> None:
    message = compose(
        E.EMERGENCY_PUBLISHED,
        "en",
        ENTRANCE_2,
        site_url=SITE,
        timezone=TZ,
        title="Cold water\r\noff\u2028until 18:00",
        body="Riser.",
    )

    assert message.subject == "Emergency: Cold water off until 18:00"
