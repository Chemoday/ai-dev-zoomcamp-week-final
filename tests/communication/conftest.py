"""A building for the communication tests.

Apt 14 (entrance 2, floor 4): owner Ivan Petrov (with email and phone).
Apt 2  (entrance 1, floor 1): tenant Mari Tamm.
Apt 15 (entrance 2, floor 5): nobody (an entrance-2 target that is not Apt 14).
Staff: chairman, board member, accountant (no apartments).
A second building with its own resident, who must never see this building's posts.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.property.models import Apartment, ApartmentMembership, Building, User
from src.modules.property.schemas import BuildingRole
from tests.factories import (
    create_apartment,
    create_building,
    create_demo_building,
    create_membership,
    create_user,
)


@dataclass
class CommWorld:
    building: Building
    apt_14: Apartment
    apt_2: Apartment
    apt_15: Apartment
    owner_14: User
    owner_14_membership: ApartmentMembership
    tenant_2: User
    chairman: User
    board: User
    accountant: User
    other_apartment: Apartment
    other_resident: User


@pytest.fixture
async def world(db_session: AsyncSession) -> CommWorld:
    demo = await create_demo_building(db_session)
    apt_15 = await create_apartment(db_session, demo.building, "15", entrance=2, floor=5)
    board = await create_user(db_session, demo.building, "Peeter Saar", role=BuildingRole.BOARD)
    accountant = await create_user(
        db_session, demo.building, "Anna Kask", role=BuildingRole.ACCOUNTANT
    )

    other_building = await create_building(db_session, name="KÜ Tamme 3")
    other_apartment = await create_apartment(db_session, other_building, "1")
    other_resident = await create_user(db_session, other_building, "Jaan Tamm")
    await create_membership(db_session, other_resident, other_apartment)

    return CommWorld(
        building=demo.building,
        apt_14=demo.apt_14,
        apt_2=demo.apt_2,
        apt_15=apt_15,
        owner_14=demo.owner_14,
        owner_14_membership=demo.owner_14_membership,
        tenant_2=demo.tenant_2,
        chairman=demo.chairman,
        board=board,
        accountant=accountant,
        other_apartment=other_apartment,
        other_resident=other_resident,
    )
