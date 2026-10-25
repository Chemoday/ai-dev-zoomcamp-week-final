"""Free-text types reject control characters (a NUL byte would be a 500 from PostgreSQL)."""

import pytest
from pydantic import BaseModel, ValidationError

from src.core.validation import optional_safe_text, safe_text


class Form(BaseModel):
    title: safe_text(10)  # type: ignore[valid-type]
    note: optional_safe_text(10) = None  # type: ignore[valid-type]


def test_required_text_is_trimmed() -> None:
    assert Form(title="  Hello \n").title == "Hello"


@pytest.mark.parametrize("title", ["", "   ", "\t\n", "a\u0000b", "bell\x07", "x" * 11])
def test_required_text_rejects_blank_control_characters_and_overlong(title: str) -> None:
    with pytest.raises(ValidationError):
        Form(title=title)


def test_required_text_keeps_tabs_and_line_breaks_inside() -> None:
    assert Form(title="a\tb\r\nc").title == "a\tb\r\nc"


def test_optional_text_turns_blank_into_none() -> None:
    assert Form(title="t", note="   ").note is None
    with pytest.raises(ValidationError):
        Form(title="t", note="\u0000")
