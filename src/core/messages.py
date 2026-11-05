"""Catalogs of system texts: one CSV per module (`key,en,ru,et`), the only files with non-English
text in the repository (docs/ARCHITECTURE.md 3.2 and 4.5).

Placeholders are `{name}` and are substituted once, so text that people wrote is never parsed as
a template (a title containing "{body}" stays literal). An empty cell falls back to English.
"""

import csv
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FALLBACK_LOCALE = "en"
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class MessageCatalogError(ValueError):
    pass


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER.findall(template))


@dataclass(frozen=True)
class MessageCatalog:
    texts: Mapping[str, Mapping[str, str]]  # key -> locale -> template

    @classmethod
    def from_csv(cls, path: Path, locales: Collection[str] | None = None) -> "MessageCatalog":
        """Load and validate a catalog; `locales` are the columns it must have exactly."""
        with path.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            columns = [c for c in reader.fieldnames or [] if c != "key"]
            if locales is not None and set(columns) != set(locales):
                raise MessageCatalogError(f"{path.name}: columns {columns} do not match Locale")
            if FALLBACK_LOCALE not in columns:
                raise MessageCatalogError(f"{path.name}: no {FALLBACK_LOCALE} column")
            texts: dict[str, dict[str, str]] = {}
            for row in reader:
                key = row["key"]
                if key in texts:
                    raise MessageCatalogError(f"{path.name}: duplicate key {key!r}")
                english = row[FALLBACK_LOCALE]
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
        template = cells.get(locale) or cells[FALLBACK_LOCALE]
        return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), template)
