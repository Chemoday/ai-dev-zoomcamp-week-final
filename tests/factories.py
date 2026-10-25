"""Test data builders. Tests may use models directly; application modules may not."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.meters.models import MeterDevice, MeterReading, ReadingPeriod
from src.modules.meters.schemas import PeriodStatus, ReadingSource, ResourceType
from src.modules.property.models import Apartment, ApartmentMembership, Building, User
from src.modules.property.schemas import BuildingRole, DisplayMode, RelationType


async def create_building(session: AsyncSession, name: str = "KÜ Sõpruse 12") -> Building:
    building = Building(name=name, address="Sõpruse pst 12, Tallinn")
    session.add(building)
    await session.flush()
    return building


async def create_apartment(
    session: AsyncSession, building: Building, number: str, entrance: int = 1, floor: int = 1
) -> Apartment:
    apartment = Apartment(
        building_id=building.id,
        number=number,
        entrance=entrance,
        floor=floor,
        area_m2=Decimal("54.30"),
    )
    session.add(apartment)
    await session.flush()
    return apartment


async def create_user(
    session: AsyncSession,
    building: Building,
    full_name: str,
    role: BuildingRole = BuildingRole.USER,
    email: str | None = None,
    phone: str | None = None,
    is_active: bool = True,
) -> User:
    user = User(
        building_id=building.id,
        full_name=full_name,
        building_role=role,
        email=email,
        phone=phone,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    return user


async def create_membership(
    session: AsyncSession,
    user: User,
    apartment: Apartment,
    relation: RelationType = RelationType.OWNER,
    mode: DisplayMode = DisplayMode.APARTMENT_ONLY,
) -> ApartmentMembership:
    membership = ApartmentMembership(
        apartment_id=apartment.id,
        user_id=user.id,
        relation_type=relation,
        can_vote=relation == RelationType.OWNER,
        privacy_display_mode=mode,
    )
    session.add(membership)
    await session.flush()
    return membership


@dataclass
class DemoBuilding:
    building: Building
    apt_14: Apartment
    apt_2: Apartment
    owner_14: User
    owner_14_membership: ApartmentMembership
    tenant_2: User
    tenant_2_membership: ApartmentMembership
    chairman: User


async def create_demo_building(session: AsyncSession) -> DemoBuilding:
    """Apt 14 (entrance 2) owned by Ivan Petrov, Apt 2 (entrance 1) rented, plus a chairman."""
    building = await create_building(session)
    apt_14 = await create_apartment(session, building, "14", entrance=2, floor=4)
    apt_2 = await create_apartment(session, building, "2", entrance=1, floor=1)
    owner_14 = await create_user(
        session,
        building,
        "Ivan Petrov",
        email=f"ivan.petrov.{uuid.uuid4().hex[:8]}@example.com",
        phone="+372 5555 1234",
    )
    tenant_2 = await create_user(session, building, "Mari Tamm", email=None, phone="+372 5111 2222")
    chairman = await create_user(
        session, building, "Oleg Sidorov", role=BuildingRole.CHAIRMAN, phone="+372 5000 0001"
    )
    return DemoBuilding(
        building=building,
        apt_14=apt_14,
        apt_2=apt_2,
        owner_14=owner_14,
        owner_14_membership=await create_membership(session, owner_14, apt_14),
        tenant_2=tenant_2,
        tenant_2_membership=await create_membership(
            session, tenant_2, apt_2, relation=RelationType.TENANT
        ),
        chairman=chairman,
    )


# --- meters --------------------------------------------------------------------------------------


async def create_meter(
    session: AsyncSession,
    building: Building,
    apartment: Apartment | None,
    resource_type: ResourceType = ResourceType.COLD_WATER,
    initial_value: Decimal = Decimal("0.000"),
    max_monthly_delta: Decimal = Decimal("25.000"),
) -> MeterDevice:
    """apartment=None creates a Building Master Meter."""
    meter = MeterDevice(
        building_id=building.id,
        apartment_id=apartment.id if apartment else None,
        resource_type=resource_type,
        serial_number=f"SN-{uuid.uuid4().hex[:10]}",
        location_label="Basement Master" if apartment is None else "Bathroom",
        initial_value=initial_value,
        max_monthly_delta=max_monthly_delta,
    )
    session.add(meter)
    await session.flush()
    return meter


async def create_period(
    session: AsyncSession,
    building: Building,
    period_code: str = "2026-10",
    status: PeriodStatus = PeriodStatus.OPEN_FOR_RESIDENTS,
) -> ReadingPeriod:
    year, month = (int(part) for part in period_code.split("-"))
    due_year, due_month = (year + 1, 1) if month == 12 else (year, month + 1)
    period = ReadingPeriod(
        building_id=building.id,
        period_code=period_code,
        status=status,
        due_date=datetime(
            due_year, due_month, building.reading_deadline_day, 23, 59, 59, tzinfo=UTC
        ),
    )
    session.add(period)
    await session.flush()
    return period


async def create_reading(
    session: AsyncSession,
    meter: MeterDevice,
    period: ReadingPeriod,
    value: Decimal,
    previous_value: Decimal,
    source: ReadingSource = ReadingSource.RESIDENT,
    submitted_by: User | None = None,
) -> MeterReading:
    reading = MeterReading(
        building_id=meter.building_id,
        meter_id=meter.id,
        period_id=period.id,
        value=value,
        previous_value=previous_value,
        consumption_delta=value - previous_value,
        source=source,
        submitted_by_user_id=submitted_by.id if submitted_by else None,
    )
    session.add(reading)
    await session.flush()
    return reading
