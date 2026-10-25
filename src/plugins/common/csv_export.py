"""CSV export of a period's readings (enabled as plugin "common.csv_export")."""

import csv
import io
import unicodedata
from collections.abc import Iterable, Iterator

from src.plugins.registry import PluginRegistry, ReadingExportRow

COLUMNS = (
    "period_code",
    "entrance",
    "apartment_number",
    "meter_id",
    "resource_type",
    "serial_number",
    "location_label",
    "unit",
    "previous_value",
    "value",
    "consumption_delta",
    "source",
    "is_anomaly_confirmed",
    "recorded_at",
)
MASTER_METER_LABEL = "MASTER"

# A cell starting with one of these is executed as a formula by Excel/LibreOffice (CSV injection).
_FORMULA_TRIGGERS = ("=", "+", "-", "@")
_LEADING_CONTROL = ("\t", "\r", "\n")


def neutralize_formula(text: str) -> str:
    """Prefix a quote when a spreadsheet could read the cell as a formula.

    NFKC folds look-alikes such as the full-width "＝" into "=", and leading whitespace or line
    breaks are skipped, because spreadsheets skip them too before evaluating.
    """
    probe = unicodedata.normalize("NFKC", text).lstrip(" \t\r\n")
    if text.startswith(_LEADING_CONTROL) or probe.startswith(_FORMULA_TRIGGERS):
        return f"'{text}"
    return text


class CsvReadingExporter:
    name = "csv"
    media_type = "text/csv; charset=utf-8"
    file_extension = "csv"

    def render(self, rows: Iterable[ReadingExportRow]) -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")

        def flush() -> str:
            chunk = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
            return chunk

        # The BOM makes Excel open UTF-8 (Cyrillic, Estonian letters) correctly.
        buffer.write("﻿")
        writer.writerow(COLUMNS)
        yield flush()
        for row in rows:
            writer.writerow(
                [
                    row.period_code,
                    "" if row.entrance is None else row.entrance,
                    neutralize_formula(row.apartment_number or MASTER_METER_LABEL),
                    row.meter_id,
                    row.resource_type,
                    neutralize_formula(row.serial_number),
                    neutralize_formula(row.location_label),
                    row.unit,
                    row.previous_value,
                    row.value,
                    row.consumption_delta,
                    row.source,
                    "true" if row.is_anomaly_confirmed else "false",
                    row.recorded_at.isoformat(),
                ]
            )
            yield flush()


def register(registry: PluginRegistry) -> None:
    registry.register_exporter(CsvReadingExporter())
