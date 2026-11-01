"""Demo building "KÜ Sõpruse 12" for local runs and the public demo (docs/TASKS.md, Task 7).

Run by entrypoint.sh after the migrations:  python -m src.core.seed
It only seeds when SEED_DEMO_DATA is true, and only into an EMPTY database (no building yet), so it
never mixes demo residents into a real association. Running it again, or from two containers at
once (an advisory lock serializes them), changes nothing.

Like models_registry.py this is a composition root: it writes the models of several modules
directly, because no module offers "create a building with history" as a service. It publishes no
domain events, so seeding never sends notifications. All ids are fixed (uuid5), so the demo users
are the same on every instance (see DEMO_USERS and GET /api/v1/demo/users).
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import build_engine, transaction
from src.core.telemetry import configure_logging
from src.core.validation import canonical_email
from src.modules.communication.models import Comment, Post
from src.modules.communication.schemas import PostCategory, PostType, TargetScope
from src.modules.meters.models import MeterDevice, MeterReading, MeterReadingLog, ReadingPeriod
from src.modules.meters.policies import period_due_date
from src.modules.meters.schemas import MeterUnit, PeriodStatus, ReadingSource, ResourceType
from src.modules.property.models import Apartment, ApartmentMembership, Building, User
from src.modules.property.schemas import BuildingRole, DisplayMode, RelationType

logger = structlog.get_logger(__name__)

_NAMESPACE = uuid.UUID("5b0d6f3c-9a3e-4f1e-8d2a-1c7e0b6a4f12")
# pg_advisory_xact_lock key: concurrent seeders wait for each other instead of racing.
SEED_LOCK_KEY = 7_202_610_001
D = Decimal
_PRECISION = D("0.001")


def demo_id(name: str) -> uuid.UUID:
    """The fixed id of a demo entity: the same on every instance and every run."""
    return uuid.uuid5(_NAMESPACE, name)


DEMO_BUILDING_ID = demo_id("building")
DEMO_REGISTRY_CODE = "80123456"
DEMO_ADDRESS = "Sõpruse pst 12, Kristiine, 10615 Tallinn"


# --- The demo building --------------------------------------------------------------------------


@dataclass(frozen=True)
class _ApartmentSpec:
    number: str
    entrance: int
    floor: int
    area_m2: str
    cold_per_month: str  # typical cold water consumption, m3
    initial_cold: str
    initial_hot: str


_APARTMENTS = (
    _ApartmentSpec("1", 1, 1, "48.20", "4.200", "120.000", "60.000"),
    _ApartmentSpec("2", 1, 1, "54.30", "3.100", "95.500", "41.200"),
    _ApartmentSpec("3", 1, 2, "62.10", "5.600", "210.000", "98.300"),
    _ApartmentSpec("13", 2, 3, "48.20", "2.400", "64.000", "30.100"),
    _ApartmentSpec("14", 2, 4, "54.30", "4.800", "124.000", "55.700"),
    _ApartmentSpec("15", 2, 4, "62.10", "6.300", "301.000", "140.400"),
)
_HOT_SHARE = D("0.45")  # hot water is about 45 % of cold
_MASTER_LOSS = {ResourceType.COLD_WATER: D("1.04"), ResourceType.HOT_WATER: D("1.03")}
_MASTER_INITIAL = {ResourceType.COLD_WATER: D("5000.000"), ResourceType.HOT_WATER: D("2000.000")}


class DemoUserKey(StrEnum):
    CHAIRMAN = "chairman"
    BOARD = "board"
    ACCOUNTANT = "accountant"
    OWNER_14 = "owner_14"
    OWNER_1 = "owner_1"
    TENANT_2 = "tenant_2"


@dataclass(frozen=True)
class DemoUser:
    """A seeded user for the demo role switcher. Fictitious people; the label has no name."""

    key: DemoUserKey
    label: str
    building_role: BuildingRole
    full_name: str
    apartment_number: str | None = None
    relation_type: RelationType | None = None
    display_mode: DisplayMode = DisplayMode.APARTMENT_ONLY

    @property
    def user_id(self) -> uuid.UUID:
        return demo_id(f"user:{self.key.value}")

    @property
    def email(self) -> str:
        return canonical_email(f"{self.full_name.replace(' ', '.')}@demo.invalid")


DEMO_USERS: tuple[DemoUser, ...] = (
    DemoUser(DemoUserKey.CHAIRMAN, "Chairman", BuildingRole.CHAIRMAN, "Oleg Sidorov"),
    DemoUser(
        DemoUserKey.BOARD,
        "Board member (Apt 15)",
        BuildingRole.BOARD,
        "Peeter Saar",
        "15",
        RelationType.OWNER,
        DisplayMode.FULL_NAME,
    ),
    DemoUser(DemoUserKey.ACCOUNTANT, "Accountant", BuildingRole.ACCOUNTANT, "Anna Kask"),
    DemoUser(
        DemoUserKey.OWNER_14,
        "Owner Apt 14",
        BuildingRole.USER,
        "Ivan Petrov",
        "14",
        RelationType.OWNER,
    ),
    DemoUser(
        DemoUserKey.OWNER_1,
        "Owner Apt 1",
        BuildingRole.USER,
        "Kati Mets",
        "1",
        RelationType.OWNER,
        DisplayMode.FULL_NAME,
    ),
    DemoUser(
        DemoUserKey.TENANT_2,
        "Tenant Apt 2",
        BuildingRole.USER,
        "Mari Tamm",
        "2",
        RelationType.TENANT,
        DisplayMode.INITIALS,
    ),
)

HISTORY_PERIODS = ("2026-07", "2026-08", "2026-09")
OPEN_PERIOD = "2026-10"
_MONTH_FACTOR = {"2026-07": D("0.95"), "2026-08": D("1.00"), "2026-09": D("1.05")}


def _q(value: Decimal) -> Decimal:
    return value.quantize(_PRECISION, rounding=ROUND_HALF_UP)


def _due_date(period_code: str, deadline_day: int) -> datetime:
    return period_due_date(period_code, deadline_day, settings.INSTANCE_TIMEZONE)


_INSERT_ORDER = (
    Building,
    Apartment,
    User,
    ApartmentMembership,
    MeterDevice,
    ReadingPeriod,
    MeterReading,
    MeterReadingLog,
    Post,
    Comment,
)


class _Builder:
    """Collects the rows of the demo building; all of them are added in one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rows: list[object] = []
        self.users: dict[DemoUserKey, User] = {}
        self.apartments: dict[str, Apartment] = {}
        self.periods: dict[str, ReadingPeriod] = {}
        self.last_value: dict[uuid.UUID, Decimal] = {}

    def add(self, *rows: object) -> None:
        self.rows.extend(rows)

    async def insert(self) -> None:
        """Insert table by table, parents first. Models of different modules have no ORM
        relationship(), so the unit of work cannot know that a comment needs its post first."""
        for model in _INSERT_ORDER:
            self.session.add_all(row for row in self.rows if type(row) is model)
            await self.session.flush()
        leftover = {type(row).__name__ for row in self.rows} - {m.__name__ for m in _INSERT_ORDER}
        if leftover:
            raise RuntimeError(f"seed rows without an insert order: {sorted(leftover)}")

    # --- property ---

    def building(self) -> Building:
        building = Building(
            id=DEMO_BUILDING_ID,
            name="KÜ Sõpruse 12",
            registry_code=DEMO_REGISTRY_CODE,
            address=DEMO_ADDRESS,
            country_code="EE",
            reading_deadline_day=4,
        )
        self.add(building)
        for spec in _APARTMENTS:
            apartment = Apartment(
                id=demo_id(f"apartment:{spec.number}"),
                building_id=building.id,
                number=spec.number,
                entrance=spec.entrance,
                floor=spec.floor,
                area_m2=D(spec.area_m2),
            )
            self.apartments[spec.number] = apartment
            self.add(apartment)
        for index, demo in enumerate(DEMO_USERS, start=1):
            user = User(
                id=demo.user_id,
                building_id=building.id,
                full_name=demo.full_name,
                email=demo.email,
                phone=f"+372 5000 {index:04d}",
                building_role=demo.building_role,
            )
            self.users[demo.key] = user
            self.add(user)
            if demo.apartment_number is not None:
                is_owner = demo.relation_type == RelationType.OWNER
                self.add(
                    ApartmentMembership(
                        id=demo_id(f"membership:{demo.key.value}"),
                        apartment_id=self.apartments[demo.apartment_number].id,
                        user_id=user.id,
                        relation_type=demo.relation_type,
                        can_submit_readings=True,
                        can_view_bills=is_owner,
                        can_vote=is_owner,
                        privacy_display_mode=demo.display_mode,
                    )
                )
        return building

    # --- meters ---

    def meters(self, building: Building) -> tuple[list[tuple[MeterDevice, Decimal]], list]:
        """Apartment meters with their monthly consumption, and the master meters."""
        apartment_meters: list[tuple[MeterDevice, Decimal]] = []
        for spec in _APARTMENTS:
            cold = D(spec.cold_per_month)
            for resource, initial, monthly, limit in (
                (ResourceType.COLD_WATER, spec.initial_cold, cold, "25.000"),
                (ResourceType.HOT_WATER, spec.initial_hot, _q(cold * _HOT_SHARE), "15.000"),
            ):
                meter = MeterDevice(
                    id=demo_id(f"meter:{spec.number}:{resource.value}"),
                    building_id=building.id,
                    apartment_id=self.apartments[spec.number].id,
                    resource_type=resource,
                    serial_number=f"SN-{spec.number}-{resource.value[:1]}W",
                    location_label="Bathroom",
                    unit=MeterUnit.M3,
                    initial_value=D(initial),
                    max_monthly_delta=D(limit),
                )
                self.last_value[meter.id] = meter.initial_value
                apartment_meters.append((meter, monthly))
                self.add(meter)
        masters = []
        for resource in (ResourceType.COLD_WATER, ResourceType.HOT_WATER):
            master = MeterDevice(
                id=demo_id(f"meter:master:{resource.value}"),
                building_id=building.id,
                apartment_id=None,
                resource_type=resource,
                serial_number=f"SN-MASTER-{resource.value[:1]}W",
                location_label="Basement Master",
                unit=MeterUnit.M3,
                initial_value=_MASTER_INITIAL[resource],
                max_monthly_delta=D("500.000"),
            )
            self.last_value[master.id] = master.initial_value
            masters.append(master)
            self.add(master)
        return apartment_meters, masters

    def period(self, building: Building, code: str, status: PeriodStatus) -> ReadingPeriod:
        period = ReadingPeriod(
            id=demo_id(f"period:{code}"),
            building_id=building.id,
            period_code=code,
            status=status,
            due_date=_due_date(code, building.reading_deadline_day),
        )
        self.periods[code] = period
        self.add(period)
        return period

    def reading(
        self,
        meter: MeterDevice,
        period: ReadingPeriod,
        delta: Decimal,
        source: ReadingSource,
        actor: User | None,
        *,
        anomaly: bool = False,
        typo: Decimal | None = None,
    ) -> MeterReading:
        """A reading plus its audit trail. `typo`: first submitted value, corrected afterwards."""
        previous = self.last_value[meter.id]
        value = previous + delta
        at = period.due_date - timedelta(days=3)
        reading = MeterReading(
            id=demo_id(f"reading:{meter.id}:{period.period_code}"),
            building_id=meter.building_id,
            meter_id=meter.id,
            period_id=period.id,
            value=value,
            previous_value=previous,
            consumption_delta=delta,
            source=source,
            is_anomaly_confirmed=anomaly,
            submitted_by_user_id=actor.id if source != ReadingSource.AUTO_ESTIMATED else None,
            recorded_at=at,
        )
        self.add(reading)
        logged_by = actor or self.users[DemoUserKey.CHAIRMAN]
        first_reason = {
            ReadingSource.RESIDENT: "Resident submission",
            ReadingSource.ADMIN_OVERRIDE: "Board entry",
            ReadingSource.AUTO_ESTIMATED: "3-month avg estimate",
        }[source]
        entries = [(None, typo if typo is not None else value, first_reason)]
        if typo is not None:
            entries.append((typo, value, "Resident update"))
        for step, (old, new, reason) in enumerate(entries):
            self.add(
                MeterReadingLog(
                    id=demo_id(f"log:{reading.id}:{step}"),
                    reading_id=reading.id,
                    old_value=old,
                    new_value=new,
                    source=source,
                    changed_by_user_id=logged_by.id,
                    reason=reason,
                    changed_at=at + timedelta(hours=step * 5),
                )
            )
        self.last_value[meter.id] = value
        return reading

    # --- communication ---

    def posts(self, building: Building, now: datetime) -> None:
        users, apartments = self.users, self.apartments
        alert = Post(
            id=demo_id("post:emergency"),
            building_id=building.id,
            author_user_id=users[DemoUserKey.CHAIRMAN].id,
            post_type=PostType.EMERGENCY,
            category=PostCategory.WATER_SHUTOFF,
            title="Hot water off in entrance 2 on Monday, 09:00-15:00",
            body="The hot water riser in entrance 2 is being replaced. Please store some water "
            "in advance. Entrance 1 is not affected.",
            target_scope=TargetScope.ENTRANCE,
            target_entrance=2,
            created_at=now - timedelta(hours=2),
        )
        notice = Post(
            id=demo_id("post:notice"),
            building_id=building.id,
            author_user_id=users[DemoUserKey.CHAIRMAN].id,
            post_type=PostType.OFFICIAL_NOTICE,
            category=PostCategory.GENERAL,
            title="October meter readings are due by 4 November",
            body="Please submit your cold and hot water readings in the app. Missing readings "
            "are estimated from your 3-month average.",
            comments_enabled=False,
            created_at=now - timedelta(days=3),
        )
        bikes = Post(
            id=demo_id("post:bike-rack"),
            building_id=building.id,
            author_user_id=users[DemoUserKey.OWNER_14].id,
            author_apartment_id=apartments["14"].id,
            post_type=PostType.FORUM_TOPIC,
            category=PostCategory.GENERAL,
            title="Shall we put a bike rack in the yard?",
            body="Bikes block the stairwell every evening. A rack near entrance 2 would fit "
            "about ten bikes. What do you think?",
            created_at=now - timedelta(days=2),
        )
        give_away = Post(
            id=demo_id("post:give-away"),
            building_id=building.id,
            author_user_id=users[DemoUserKey.OWNER_1].id,
            author_apartment_id=apartments["1"].id,
            post_type=PostType.FORUM_TOPIC,
            category=PostCategory.MARKETPLACE,
            title="Giving away a children's bike",
            body="16-inch wheels, good condition. Free to a neighbour, pick it up in entrance 1.",
            created_at=now - timedelta(days=1),
        )
        self.add(alert, notice, bikes, give_away)
        comments = (
            (bikes, DemoUserKey.TENANT_2, "2", "Yes, please. I carry my bike up to the 1st floor."),
            (bikes, DemoUserKey.BOARD, "15", "The board can put it on the next meeting's agenda."),
            (bikes, DemoUserKey.OWNER_1, "1", "Good idea, but please leave room for prams."),
            (give_away, DemoUserKey.OWNER_14, "14", "My son would love it. Is it still free?"),
        )
        for step, (post, author, number, body) in enumerate(comments):
            self.add(
                Comment(
                    id=demo_id(f"comment:{step}"),
                    post_id=post.id,
                    building_id=building.id,
                    author_user_id=users[author].id,
                    author_apartment_id=apartments[number].id,
                    body=body,
                    created_at=post.created_at + timedelta(hours=step + 1),
                )
            )


async def seed_demo_data(session: AsyncSession, now: datetime | None = None) -> bool:
    """Add the demo building to an empty database. Returns False when nothing was done.

    The caller commits (use `transaction(session)`).
    """
    await session.execute(select(func.pg_advisory_xact_lock(SEED_LOCK_KEY)))
    if await session.scalar(select(Building.id).limit(1)) is not None:
        demo = await session.get(Building, DEMO_BUILDING_ID)
        if demo is not None and demo.registry_code is None:
            # Seeded before the registry code existed (migration 004): complete the demo building.
            demo.registry_code, demo.address = DEMO_REGISTRY_CODE, DEMO_ADDRESS
            logger.info("seed_demo_building_completed")
        logger.info("seed_skipped", reason="already_seeded" if demo else "not_empty")
        return False

    now = now or datetime.now(UTC)
    builder = _Builder(session)
    building = builder.building()
    apartment_meters, masters = builder.meters(building)
    users = builder.users
    resident_of = {
        demo.apartment_number: users[demo.key] for demo in DEMO_USERS if demo.apartment_number
    }
    chairman, accountant = users[DemoUserKey.CHAIRMAN], users[DemoUserKey.ACCOUNTANT]

    for code in HISTORY_PERIODS:
        period = builder.period(building, code, PeriodStatus.LOCKED)
        totals = dict.fromkeys(_MASTER_LOSS, D("0"))
        for meter, monthly in apartment_meters:
            delta = _q(monthly * _MONTH_FACTOR[code])
            number = next(n for n, a in builder.apartments.items() if a.id == meter.apartment_id)
            resident = resident_of.get(number)
            typo = None
            cold = meter.resource_type == ResourceType.COLD_WATER
            if number == "14" and code == "2026-09" and cold:
                typo = builder.last_value[meter.id] + delta + D("10.000")  # fixed a day later
            if resident is not None:
                builder.reading(meter, period, delta, ReadingSource.RESIDENT, resident, typo=typo)
            elif number == "13" and code == "2026-08":
                # Nobody submitted: the board estimated it from the only earlier month (July).
                delta = _q(monthly * _MONTH_FACTOR["2026-07"])
                builder.reading(meter, period, delta, ReadingSource.AUTO_ESTIMATED, None)
            else:  # paper forms, typed in by the chairman
                builder.reading(meter, period, delta, ReadingSource.ADMIN_OVERRIDE, chairman)
            totals[meter.resource_type] += delta
        for master in masters:
            delta = _q(totals[master.resource_type] * _MASTER_LOSS[master.resource_type])
            builder.reading(master, period, delta, ReadingSource.ADMIN_OVERRIDE, accountant)

    # October is open: some residents already submitted, Apt 14 is left for the demo.
    october = builder.period(building, OPEN_PERIOD, PeriodStatus.OPEN_FOR_RESIDENTS)
    for meter, monthly in apartment_meters:
        number = next(n for n, a in builder.apartments.items() if a.id == meter.apartment_id)
        hot = meter.resource_type == ResourceType.HOT_WATER
        if number == "1" or (number == "2" and not hot):
            builder.reading(meter, october, monthly, ReadingSource.RESIDENT, resident_of[number])
        elif number == "15":
            # A leaking hot water tap: confirmed through both anomaly popups.
            delta = D("31.000") if hot else monthly
            builder.reading(
                meter, october, delta, ReadingSource.RESIDENT, resident_of[number], anomaly=hot
            )

    builder.posts(building, now)
    await builder.insert()
    logger.info(
        "seed_completed",
        apartments=len(builder.apartments),
        users=len(users),
        periods=len(builder.periods),
    )
    return True


async def run_seed(database_url: str) -> bool:
    engine = build_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with transaction(session):
                return await seed_demo_data(session)
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging(level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)
    if not settings.SEED_DEMO_DATA:
        logger.info("seed_skipped", reason="disabled")
        return
    asyncio.run(run_seed(settings.DATABASE_URL))


if __name__ == "__main__":
    main()
