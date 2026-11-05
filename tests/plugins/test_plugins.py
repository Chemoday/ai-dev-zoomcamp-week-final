"""Unit tests for the plugin registry and the CSV exporter (no database)."""

import csv
import io
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.plugins.common.csv_export import COLUMNS, CsvReadingExporter, neutralize_formula
from src.plugins.common.fcm_push import CHANNEL_NAME as FCM_CHANNEL_NAME
from src.plugins.common.fcm_push import FcmPushChannel
from src.plugins.registry import (
    DeliveryResult,
    OutgoingMessage,
    PluginRegistry,
    ReadingExportRow,
    parse_plugin_list,
)


def row(**overrides) -> ReadingExportRow:
    values = {
        "period_code": "2026-10",
        "entrance": 2,
        "apartment_number": "14",
        "meter_id": uuid.UUID(int=1),
        "resource_type": "COLD_WATER",
        "serial_number": "SN-1",
        "location_label": "Bathroom",
        "unit": "m3",
        "previous_value": Decimal("115.000"),
        "value": Decimal("120.000"),
        "consumption_delta": Decimal("5.000"),
        "source": "RESIDENT",
        "is_anomaly_confirmed": False,
        "recorded_at": datetime(2026, 10, 25, 10, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return ReadingExportRow(**values)


def render(rows) -> str:
    return "".join(CsvReadingExporter().render(rows))


def test_csv_has_bom_header_and_canonical_values() -> None:
    text = render([row(), row(entrance=None, apartment_number=None, is_anomaly_confirmed=True)])

    assert text.startswith("﻿")
    parsed = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert tuple(parsed[0]) == COLUMNS
    assert parsed[1] == [
        "2026-10", "2", "14", str(uuid.UUID(int=1)), "COLD_WATER", "SN-1", "Bathroom", "m3",
        "115.000", "120.000", "5.000", "RESIDENT", "false", "2026-10-25T10:00:00+00:00",
    ]  # fmt: skip
    assert parsed[2][1:3] == ["", "MASTER"]
    assert parsed[2][12] == "true"
    assert text.count("\r\n") == 3


def test_csv_streams_one_chunk_per_row() -> None:
    chunks = list(CsvReadingExporter().render([row(), row(), row()]))

    assert len(chunks) == 4  # header + 3 rows


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("=SUM(A1)", "'=SUM(A1)"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@cmd", "'@cmd"),
        ("\tx", "'\tx"),
        ("Basement Master", "Basement Master"),
        ("14A", "14A"),
    ],
)
def test_formula_neutralization(text: str, expected: str) -> None:
    assert neutralize_formula(text) == expected


def test_registry_loads_plugins_once_and_rejects_bad_names() -> None:
    registry = PluginRegistry()

    registry.load(["common.csv_export"])
    registry.load(["common.csv_export"])  # idempotent

    assert registry.exporter("csv").media_type == "text/csv; charset=utf-8"
    with pytest.raises(LookupError, match="no exporter 'xml'"):
        registry.exporter("xml")
    with pytest.raises(ValueError, match="invalid plugin name"):
        registry.load(["../../etc/passwd"])
    with pytest.raises(ModuleNotFoundError):
        registry.load(["countries.zz.nothing"])


def test_duplicate_exporter_names_are_refused() -> None:
    registry = PluginRegistry()
    registry.register_exporter(CsvReadingExporter())

    with pytest.raises(ValueError, match="already registered"):
        registry.register_exporter(CsvReadingExporter())


def test_plugin_list_parsing() -> None:
    assert parse_plugin_list(" common.csv_export , countries.ee.e_arve_xml,, ") == [
        "common.csv_export",
        "countries.ee.e_arve_xml",
    ]
    assert parse_plugin_list("") == []


class FakeChannel:
    name = "fake"

    async def send(self, message: OutgoingMessage) -> DeliveryResult:
        return DeliveryResult(ok=True)


def message() -> OutgoingMessage:
    return OutgoingMessage(
        address={"token": "secret-device-token"},
        locale="en",
        event_type="EMERGENCY_PUBLISHED",
        subject="Hot water off",
        body="Entrance 2",
        deep_link="https://example.test/posts/1",
        urgent=True,
        collapse_key="post:1",
        expires_at=datetime(2026, 10, 31, 12, 0, tzinfo=UTC),
    )


def test_registry_holds_notification_channels() -> None:
    registry = PluginRegistry()
    channel = FakeChannel()

    registry.register_channel(channel)

    assert registry.channel("fake") is channel
    assert registry.channel("email") is None
    assert registry.channels() == [channel]
    with pytest.raises(ValueError, match="already registered"):
        registry.register_channel(FakeChannel())


def test_outgoing_message_repr_hides_address_and_texts() -> None:
    text = repr(message())

    for secret in ("secret-device-token", "Hot water off", "Entrance 2", "example.test"):
        assert secret not in text
    assert "urgent=True" in text
    assert "EMERGENCY_PUBLISHED" in text


def test_fcm_push_stub_cannot_be_enabled() -> None:
    registry = PluginRegistry()

    with pytest.raises(RuntimeError, match="not implemented yet"):
        registry.load(["common.fcm_push"])

    assert not registry.is_enabled("common.fcm_push")
    assert registry.channel(FCM_CHANNEL_NAME) is None


async def test_fcm_push_stub_does_not_send() -> None:
    channel = FcmPushChannel(project_id="demo", service_account_file="/run/secrets/fcm.json")

    assert channel.name == "mobile_push"
    with pytest.raises(NotImplementedError):
        await channel.send(message())
    with pytest.raises(NotImplementedError):
        channel.build_message(message())
