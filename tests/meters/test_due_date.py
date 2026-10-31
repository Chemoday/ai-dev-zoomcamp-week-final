"""Reading deadlines end at local midnight (docs/SCHEMA.md, meters_periods.due_date)."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.meters.policies import period_due_date
from tests.factories import create_building, create_period

TALLINN = "Europe/Tallinn"


def test_winter_and_summer_deadlines_in_tallinn() -> None:
    # 4 Nov is winter time (UTC+2), 4 Oct still summer time (UTC+3).
    assert period_due_date("2026-10", 4, TALLINN) == datetime(2026, 11, 4, 21, 59, 59, tzinfo=UTC)
    assert period_due_date("2026-09", 4, TALLINN) == datetime(2026, 10, 4, 20, 59, 59, tzinfo=UTC)


def test_a_december_period_is_due_in_january() -> None:
    assert period_due_date("2026-12", 4, TALLINN) == datetime(2027, 1, 4, 21, 59, 59, tzinfo=UTC)


def test_another_time_zone() -> None:
    assert period_due_date("2026-10", 4, "UTC") == datetime(2026, 11, 4, 23, 59, 59, tzinfo=UTC)


def _migration_006():
    path = Path(__file__).parents[2] / "alembic" / "versions" / "006_notifications.py"
    spec = importlib.util.spec_from_file_location("migration_006", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_migration_moves_old_utc_deadlines_to_local_midnight(
    db_session: AsyncSession,
) -> None:
    building = await create_building(db_session)
    old = await create_period(db_session, building, period_code="2026-09")
    new = await create_period(db_session, building, period_code="2026-10")
    old.due_date = datetime(2026, 10, 4, 23, 59, 59, tzinfo=UTC)  # written by the old seed
    await db_session.flush()

    await db_session.execute(text(_migration_006().LOCAL_DEADLINE_SQL), {"tz": TALLINN})
    rows = dict(
        (
            await db_session.execute(
                text("SELECT period_code, due_date FROM meters_periods WHERE building_id = :b"),
                {"b": building.id},
            )
        ).all()
    )

    assert rows["2026-09"] == datetime(2026, 10, 4, 20, 59, 59, tzinfo=UTC)
    assert rows["2026-10"] == new.due_date, "already local: untouched"
