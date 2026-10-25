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


def clean_optional_text(value: str | None) -> str | None:
    """Like clean_text, but blank input means "not given"."""
    if value is None:
        return None
    return clean_text(value) or None


SafeText = Annotated[str, AfterValidator(clean_text)]


def optional_safe_text(max_length: int) -> object:
    """Type for an optional free-text field: length-checked, cleaned, blank -> None."""
    return Annotated[
        Annotated[str, Field(max_length=max_length)] | None,
        AfterValidator(clean_optional_text),
    ]
