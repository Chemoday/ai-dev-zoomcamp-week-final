"""A building for the notifications tests.

Entrance 2: Apt 14 owned by Ivan Petrov; Apt 15 owned by board member Peeter Saar (so he is both
a targeted resident and the board: he must get one notification, not two); Apt 16 with an
inactive owner.
Entrance 1: Apt 2 rented by Mari Tamm.
Staff without apartments: the chairman and an accountant.
A second building with its own resident, who must never be notified about this building.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.meters.models import ReadingPeriod
from src.modules.property.models import Apartment, Building, User
from src.modules.property.schemas import BuildingRole
from tests.factories import (
    create_apartment,
    create_building,
    create_demo_building,
    create_membership,
    create_period,
    create_user,
)


@dataclass
class NotifWorld:
    building: Building
    apt_14: Apartment
    apt_2: Apartment
    apt_15: Apartment
    owner_14: User
    tenant_2: User
    board_15: User
    inactive_16: User
    chairman: User
    accountant: User
    other_resident: User
    period: ReadingPeriod


@pytest.fixture
async def world(db_session: AsyncSession) -> NotifWorld:
    demo = await create_demo_building(db_session)
    apt_15 = await create_apartment(db_session, demo.building, "15", entrance=2, floor=5)
    apt_16 = await create_apartment(db_session, demo.building, "16", entrance=2, floor=6)
    board_15 = await create_user(db_session, demo.building, "Peeter Saar", role=BuildingRole.BOARD)
    await create_membership(db_session, board_15, apt_15)
    inactive_16 = await create_user(db_session, demo.building, "Old Owner", is_active=False)
    await create_membership(db_session, inactive_16, apt_16)
    accountant = await create_user(
        db_session, demo.building, "Anna Kask", role=BuildingRole.ACCOUNTANT
    )

    other_building = await create_building(db_session, name="KÜ Tamme 3")
    other_apartment = await create_apartment(db_session, other_building, "1")
    other_resident = await create_user(db_session, other_building, "Jaan Tamm")
    await create_membership(db_session, other_resident, other_apartment)

    period = await create_period(db_session, demo.building)
    return NotifWorld(
        building=demo.building,
        apt_14=demo.apt_14,
        apt_2=demo.apt_2,
        apt_15=apt_15,
        owner_14=demo.owner_14,
        tenant_2=demo.tenant_2,
        board_15=board_15,
        inactive_16=inactive_16,
        chairman=demo.chairman,
        accountant=accountant,
        other_resident=other_resident,
        period=period,
    )
