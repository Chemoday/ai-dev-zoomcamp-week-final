"""A building for the governance tests.

Entrance 2: Apt 14 owned by Ivan Petrov, with his relative Liis (RESIDENT); Apt 15 owned by board
member Peeter Saar. Entrance 1: Apt 2 rented by Mari Tamm (no owner in the app).
Staff: the chairman and an accountant, without apartments. A second building with its own
chairman and resident, who must never see or touch this building's polls.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.property.models import Apartment, Building, User
from src.modules.property.schemas import BuildingRole, RelationType
from tests.factories import (
    create_apartment,
    create_building,
    create_demo_building,
    create_membership,
    create_user,
)


@dataclass
class PollWorld:
    building: Building
    apt_14: Apartment
    apt_2: Apartment
    apt_15: Apartment
    owner_14: User
    family_14: User
    tenant_2: User
    board_15: User
    chairman: User
    accountant: User
    other_chairman: User
    other_resident: User


@pytest.fixture
async def world(db_session: AsyncSession) -> PollWorld:
    return await build_poll_world(db_session)


async def build_poll_world(db_session: AsyncSession) -> PollWorld:
    demo = await create_demo_building(db_session)
    apt_15 = await create_apartment(db_session, demo.building, "15", entrance=2, floor=5)
    family_14 = await create_user(db_session, demo.building, "Liis Petrova")
    await create_membership(db_session, family_14, demo.apt_14, relation=RelationType.RESIDENT)
    board_15 = await create_user(db_session, demo.building, "Peeter Saar", role=BuildingRole.BOARD)
    await create_membership(db_session, board_15, apt_15)
    accountant = await create_user(
        db_session, demo.building, "Anna Kask", role=BuildingRole.ACCOUNTANT
    )
    other = await create_building(db_session, name="KÜ Tamme 3")
    other_apartment = await create_apartment(db_session, other, "1")
    other_resident = await create_user(db_session, other, "Jaan Tamm")
    await create_membership(db_session, other_resident, other_apartment)
    other_chairman = await create_user(db_session, other, "Kalle Kask", role=BuildingRole.CHAIRMAN)
    return PollWorld(
        building=demo.building,
        apt_14=demo.apt_14,
        apt_2=demo.apt_2,
        apt_15=apt_15,
        owner_14=demo.owner_14,
        family_14=family_14,
        tenant_2=demo.tenant_2,
        board_15=board_15,
        chairman=demo.chairman,
        accountant=accountant,
        other_chairman=other_chairman,
        other_resident=other_resident,
    )
