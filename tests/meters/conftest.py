"""A building with history, shaped like the Task 7 demo data.

Periods 2026-07..09 are LOCKED with readings; 2026-10 is OPEN_FOR_RESIDENTS.
Apt 14 (entrance 2): owner Ivan Petrov, cold water meter, history deltas 4, 5, 6 (avg 5.000).
Apt 2  (entrance 1): tenant Mari Tamm, cold water meter, history deltas 3, 3, 3.
Apt 5  (entrance 1): owner who never submits, cold water meter without history.
Basement: one COLD_WATER master meter with history.
Staff: chairman, accountant, board member (no apartments).
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.meters.models import MeterDevice, ReadingPeriod
from src.modules.meters.schemas import PeriodStatus
from src.modules.property.models import ApartmentMembership, Building, User
from src.modules.property.schemas import BuildingRole, RelationType
from tests.factories import (
    create_apartment,
    create_building,
    create_membership,
    create_meter,
    create_period,
    create_reading,
    create_user,
)

D = Decimal


@dataclass
class MetersWorld:
    building: Building
    periods: dict[str, ReadingPeriod]
    owner_14: User
    tenant_2: User
    tenant_2_membership: ApartmentMembership
    owner_5: User
    chairman: User
    accountant: User
    board: User
    meter_14: MeterDevice
    meter_2: MeterDevice
    meter_5: MeterDevice
    master: MeterDevice


@pytest.fixture
async def world(db_session: AsyncSession) -> MetersWorld:
    building = await create_building(db_session)
    apt_14 = await create_apartment(db_session, building, "14", entrance=2, floor=4)
    apt_2 = await create_apartment(db_session, building, "2", entrance=1, floor=1)
    apt_5 = await create_apartment(db_session, building, "5", entrance=1, floor=2)

    owner_14 = await create_user(db_session, building, "Ivan Petrov", phone="+372 5555 1234")
    tenant_2 = await create_user(db_session, building, "Mari Tamm")
    owner_5 = await create_user(db_session, building, "Oleg Kuznetsov")
    chairman = await create_user(db_session, building, "Oleg Sidorov", role=BuildingRole.CHAIRMAN)
    accountant = await create_user(db_session, building, "Anna Kask", role=BuildingRole.ACCOUNTANT)
    board = await create_user(db_session, building, "Peeter Saar", role=BuildingRole.BOARD)
    await create_membership(db_session, owner_14, apt_14)
    tenant_2_membership = await create_membership(
        db_session, tenant_2, apt_2, relation=RelationType.TENANT
    )
    await create_membership(db_session, owner_5, apt_5)

    meter_14 = await create_meter(db_session, building, apt_14, initial_value=D("100.000"))
    meter_2 = await create_meter(db_session, building, apt_2, initial_value=D("50.000"))
    meter_5 = await create_meter(db_session, building, apt_5, initial_value=D("10.000"))
    master = await create_meter(
        db_session, building, None, initial_value=D("1000.000"), max_monthly_delta=D("500.000")
    )

    periods = {
        code: await create_period(db_session, building, code, PeriodStatus.LOCKED)
        for code in ("2026-07", "2026-08", "2026-09")
    }
    periods["2026-10"] = await create_period(
        db_session, building, "2026-10", PeriodStatus.OPEN_FOR_RESIDENTS
    )

    history = {
        meter_14: ["104.000", "109.000", "115.000"],  # deltas 4, 5, 6
        meter_2: ["53.000", "56.000", "59.000"],  # deltas 3, 3, 3
        master: ["1010.000", "1020.000", "1030.000"],
    }
    for meter, values in history.items():
        previous = meter.initial_value
        for code, value in zip(("2026-07", "2026-08", "2026-09"), values, strict=True):
            await create_reading(db_session, meter, periods[code], D(value), previous)
            previous = D(value)

    return MetersWorld(
        building=building,
        periods=periods,
        owner_14=owner_14,
        tenant_2=tenant_2,
        tenant_2_membership=tenant_2_membership,
        owner_5=owner_5,
        chairman=chairman,
        accountant=accountant,
        board=board,
        meter_14=meter_14,
        meter_2=meter_2,
        meter_5=meter_5,
        master=master,
    )
