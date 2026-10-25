"""Reusable validators for free-text input (reasons, post titles and bodies, comments).

PostgreSQL rejects NUL bytes in text columns, so an unchecked "\\u0000" in any field becomes a
500 — on exactly the endpoints the user-impact alerts watch. Free text must use these types.
"""

import re
from typing import Annotated

from pydantic import AfterValidator, Field

# C0 control characters except tab, line feed and carriage return, plus DEL.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: str) -> str:
    if _CONTROL_CHARACTERS.search(value):
        raise ValueError("text must not contain control characters")
    return value.strip()


def clean_required_text(value: str) -> str:
    """Like clean_text, but blank input is rejected."""
    cleaned = clean_text(value)
    if not cleaned:
        raise ValueError("text must not be blank")
    return cleaned


SafeText = Annotated[str, AfterValidator(clean_text)]


# Length limits come AFTER the cleaning validator, so they apply to the trimmed text:
# "  " + 255 characters is a valid 255-character title.


def safe_text(max_length: int) -> object:
    """Type for a required free-text field (titles, bodies, comments): cleaned, never blank,
    at most max_length characters after trimming."""
    return Annotated[str, AfterValidator(clean_required_text), Field(max_length=max_length)]


def _blank_to_none(value: str | None) -> str | None:
    return value or None


def optional_safe_text(max_length: int) -> object:
    """Type for an optional free-text field: cleaned, length-checked after trimming,
    blank -> None."""
    return Annotated[
        Annotated[str, AfterValidator(clean_text), Field(max_length=max_length)] | None,
        AfterValidator(_blank_to_none),
    ]
