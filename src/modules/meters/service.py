"""Public facade of the meters module: readings, the chairman matrix, estimates and export.

Rules live in docs/API.md (sections 2 and 3) and docs/ROADMAP_MVP.md (sections B and C).
Apartments and author badges come from PropertyService; property models are never imported.
"""

import uuid
from decimal import Decimal

from fastapi import Depends
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession
from src.core.event_bus import EventBus, get_event_bus
from src.core.exceptions import DomainError
from src.modules.meters.events import MeterReadingSubmitted, ReadingPeriodStatusChanged
from src.modules.meters.models import MeterDevice, MeterReading, MeterReadingLog, ReadingPeriod
from src.modules.meters.policies import (
    PERIOD_TRANSITIONS,
    ReadingCheckStatus,
    apartment_submission_status,
    calculate_rolling_average_delta,
    can_transition_period,
    meter_submission_status,
    validate_reading_delta,
)
from src.modules.meters.schemas import (
    DashboardApartment,
    DashboardMeter,
    DashboardResponse,
    EstimatedReading,
    EstimateResponse,
    HistoryEntry,
    MatrixApartment,
    MatrixMeter,
    MatrixResponse,
    MeterUnit,
    PeriodDTO,
    PeriodStatus,
    PeriodStatusUpdateResponse,
    ReadingHistoryResponse,
    ReadingResponse,
    ReadingSource,
    ReadingSubmitRequest,
    ReadingSummary,
    ResourceBalance,
    canonical_meter_value,
)
from src.modules.property.schemas import BuildingRole, CurrentUserDTO
from src.modules.property.service import PropertyService, get_property_service
from src.plugins.registry import ReadingExporter, ReadingExportRow

STAFF_VIEW_ROLES = frozenset({BuildingRole.CHAIRMAN, BuildingRole.BOARD, BuildingRole.ACCOUNTANT})
OVERRIDE_ROLES = frozenset({BuildingRole.CHAIRMAN, BuildingRole.ACCOUNTANT})

# Readings that count as consumption history (docs/ROADMAP_MVP.md, section C).
HISTORY_SOURCES = (ReadingSource.RESIDENT, ReadingSource.ADMIN_OVERRIDE, ReadingSource.IOT_WEBHOOK)
NO_HISTORY_AVERAGE = Decimal("0.000")
ESTIMATE_REASON = "3-month avg estimate"


tracer = trace.get_tracer(__name__)


def _not_found(error_code: str, message: str) -> DomainError:
    return DomainError(status_code=404, error_code=error_code, message=message)


def _summary(reading: MeterReading) -> ReadingSummary:
    return ReadingSummary(
        reading_id=reading.id,
        value=reading.value,
        consumption_delta=reading.consumption_delta,
        source=reading.source,
        is_anomaly_confirmed=reading.is_anomaly_confirmed,
    )


def _period_dto(period: ReadingPeriod) -> PeriodDTO:
    return PeriodDTO(period_code=period.period_code, status=period.status, due_date=period.due_date)


class MetersService:
    def __init__(
        self, session: AsyncSession, property_service: PropertyService, event_bus: EventBus
    ):
        self.session = session
        self.property_service = property_service
        self.event_bus = event_bus

    # --- Submitting readings --------------------------------------------------------------------

    async def submit_reading(
        self, user: CurrentUserDTO, request: ReadingSubmitRequest
    ) -> ReadingResponse:
        # Row lock on the meter: concurrent submissions for one meter run one after another, so
        # the unique (meter, period) row is never inserted twice and the audit log stays ordered.
        meter = await self.session.scalar(
            select(MeterDevice)
            .where(
                MeterDevice.id == request.meter_id,
                MeterDevice.building_id == user.building_id,
                MeterDevice.is_active.is_(True),
            )
            .with_for_update()
        )
        membership = (
            user.membership_for(meter.apartment_id) if meter and meter.apartment_id else None
        )
        if meter is None or (membership is None and user.building_role not in OVERRIDE_ROLES):
            raise _not_found("METER_NOT_FOUND", "Meter not found.")

        period = await self._period(meter.building_id, request.period_code)
        source = self._authorize_submission(user, membership, period)
        existing = await self.session.scalar(
            select(MeterReading).where(
                MeterReading.meter_id == meter.id, MeterReading.period_id == period.id
            )
        )
        span = trace.get_current_span()
        span.set_attributes(
            {
                "meters.meter_id": str(meter.id),
                "meters.period_code": period.period_code,
                "meters.reading_source": source.value,
                "meters.confirmation_level": request.confirmation_level,
            }
        )

        if existing is not None and existing.value == request.value:
            span.set_attribute("meters.outcome", "unchanged")
            return self._reading_response(existing, period)
        if source == ReadingSource.ADMIN_OVERRIDE and existing is not None and not request.reason:
            raise DomainError(
                status_code=422,
                error_code="REASON_REQUIRED",
                message="Changing an existing reading on behalf of the board needs a reason.",
            )

        previous_value = await self._previous_value(meter, period)
        average = calculate_rolling_average_delta(
            await self._history_deltas(meter.id, period), NO_HISTORY_AVERAGE
        )
        check = validate_reading_delta(
            previous_value,
            request.value,
            meter.max_monthly_delta,
            average,
            request.confirmation_level,
        )
        span.set_attribute("meters.outcome", check.status.value)
        self._raise_for_check(check, previous_value, request.value, average, meter)

        old_value = existing.value if existing is not None else None
        reading = existing or MeterReading(
            building_id=meter.building_id, meter_id=meter.id, period_id=period.id
        )
        reading.value = request.value
        reading.previous_value = previous_value
        reading.consumption_delta = check.consumption_delta
        reading.source = source
        reading.is_anomaly_confirmed = check.is_anomaly_confirmed
        reading.submitted_by_user_id = user.id
        if existing is None:
            self.session.add(reading)
        await self.session.flush()

        self.session.add(
            MeterReadingLog(
                reading_id=reading.id,
                old_value=old_value,
                new_value=reading.value,
                source=source,
                changed_by_user_id=user.id,
                reason=request.reason or self._default_reason(source, old_value),
            )
        )
        await self.session.flush()
        await self._publish_submitted(reading, meter, period)
        return self._reading_response(reading, period)

    @staticmethod
    def _authorize_submission(
        user: CurrentUserDTO, membership, period: ReadingPeriod
    ) -> ReadingSource:
        """Who may write in which period status (docs/API.md, POST /readings)."""
        if (
            membership is not None
            and membership.can_submit_readings
            and period.status == PeriodStatus.OPEN_FOR_RESIDENTS
        ):
            return ReadingSource.RESIDENT
        if user.building_role in OVERRIDE_ROLES:
            if period.status == PeriodStatus.LOCKED:
                raise DomainError(
                    status_code=409,
                    error_code="PERIOD_LOCKED",
                    message="The period is locked. Reopen it for review to change readings.",
                )
            return ReadingSource.ADMIN_OVERRIDE
        if membership is not None and not membership.can_submit_readings:
            raise DomainError(
                status_code=403,
                error_code="FORBIDDEN",
                message="You are not allowed to submit readings for this apartment.",
            )
        raise DomainError(
            status_code=409,
            error_code="PERIOD_CLOSED",
            message="The reading period is closed for residents.",
            extra={"period_status": PeriodStatus(period.status).value},
        )

    @staticmethod
    def _raise_for_check(check, previous_value, submitted_value, average, meter) -> None:
        delta = check.consumption_delta
        if check.status == ReadingCheckStatus.ERR_BELOW_PREV:
            raise DomainError(
                status_code=422,
                error_code="READING_BELOW_PREVIOUS",
                message=f"New reading cannot be lower than previous reading ({previous_value}).",
                extra={
                    "previous_value": str(previous_value),
                    "submitted_value": str(submitted_value),
                },
            )
        # Messages are English only; the frontend localizes them by error_code.
        unit = MeterUnit(meter.unit).value
        if check.status == ReadingCheckStatus.WARN_STEP_1:
            raise DomainError(
                status_code=409,
                error_code="ANOMALY_CONFIRMATION_STEP_1",
                message=f"Value is unusually high (consumption {delta} {unit}), are you sure?",
                extra={
                    "required_confirmation_level": check.required_confirmation_level,
                    "consumption_delta": str(delta),
                    "average_delta": str(average),
                    "max_reasonable_delta": str(meter.max_monthly_delta),
                },
            )
        if check.status == ReadingCheckStatus.WARN_STEP_2:
            raise DomainError(
                status_code=409,
                error_code="ANOMALY_CONFIRMATION_STEP_2",
                message="Please double-check the digits; this goes directly to billing!",
                extra={
                    "required_confirmation_level": check.required_confirmation_level,
                    "consumption_delta": str(delta),
                },
            )

    @staticmethod
    def _default_reason(source: ReadingSource, old_value: Decimal | None) -> str:
        if source == ReadingSource.RESIDENT:
            return "Resident submission" if old_value is None else "Resident update"
        return "Board entry" if old_value is None else "Board correction"

    # --- Resident dashboard ---------------------------------------------------------------------

    async def get_dashboard(
        self, user: CurrentUserDTO, period_code: str | None
    ) -> DashboardResponse:
        if period_code is not None:
            period = await self._period(user.building_id, period_code)
        else:
            period = await self.session.scalar(
                select(ReadingPeriod)
                .where(ReadingPeriod.building_id == user.building_id)
                .order_by(ReadingPeriod.period_code.desc())
                .limit(1)
            )
        apartment_ids = [m.apartment_id for m in user.memberships]
        meters = list(
            await self.session.scalars(
                select(MeterDevice)
                .where(
                    MeterDevice.building_id == user.building_id,
                    MeterDevice.apartment_id.in_(apartment_ids),
                    MeterDevice.is_active.is_(True),
                )
                .order_by(MeterDevice.resource_type, MeterDevice.location_label)
            )
        )
        readings = await self._readings_by_meter(period) if period else {}

        apartments = []
        for membership in user.memberships:
            dashboard_meters = []
            for meter in (m for m in meters if m.apartment_id == membership.apartment_id):
                reading = readings.get(meter.id)
                dashboard_meters.append(
                    DashboardMeter(
                        meter_id=meter.id,
                        resource_type=meter.resource_type,
                        serial_number=meter.serial_number,
                        location_label=meter.location_label,
                        unit=meter.unit,
                        previous_value=(
                            reading.previous_value
                            if reading is not None
                            else await self._previous_value(meter, period)
                        ),
                        reading=_summary(reading) if reading is not None else None,
                    )
                )
            apartments.append(
                DashboardApartment(
                    apartment_id=membership.apartment_id,
                    apartment_number=membership.apartment_number,
                    can_submit_readings=membership.can_submit_readings,
                    meters=dashboard_meters,
                )
            )
        return DashboardResponse(
            period=_period_dto(period) if period else None, apartments=apartments
        )

    # --- Chairman matrix ------------------------------------------------------------------------

    async def get_matrix(self, user: CurrentUserDTO, period_code: str) -> MatrixResponse:
        period = await self._period(user.building_id, period_code)
        readings = await self._readings_by_meter(period)
        # Active meters, plus meters replaced during the period that still have a reading in it.
        meters = list(
            await self.session.scalars(
                select(MeterDevice)
                .where(
                    MeterDevice.building_id == user.building_id,
                    MeterDevice.is_active.is_(True) | MeterDevice.id.in_(list(readings)),
                )
                .order_by(MeterDevice.resource_type, MeterDevice.location_label)
            )
        )
        apartments = await self.property_service.list_apartments(user.building_id)

        rows = []
        for apartment in apartments:
            cells = [
                self._matrix_cell(meter, readings.get(meter.id))
                for meter in meters
                if meter.apartment_id == apartment.id
            ]
            if not cells:
                continue
            rows.append(
                MatrixApartment(
                    apartment_id=apartment.id,
                    apartment_number=apartment.number,
                    entrance=apartment.entrance,
                    floor=apartment.floor,
                    status=apartment_submission_status(cell.status for cell in cells),
                    meters=cells,
                )
            )
        return MatrixResponse(
            period=_period_dto(period),
            balances=self._balances(meters, readings),
            apartments=rows,
        )

    @staticmethod
    def _matrix_cell(meter: MeterDevice, reading: MeterReading | None) -> MatrixMeter:
        return MatrixMeter(
            meter_id=meter.id,
            resource_type=meter.resource_type,
            location_label=meter.location_label,
            unit=meter.unit,
            status=meter_submission_status(
                reading.source if reading else None,
                reading.is_anomaly_confirmed if reading else False,
            ),
            reading=_summary(reading) if reading else None,
        )

    @staticmethod
    def _balances(
        meters: list[MeterDevice], readings: dict[uuid.UUID, MeterReading]
    ) -> list[ResourceBalance]:
        """Master meters minus the sum of apartment meters, per resource type."""
        balances = []
        resource_types = sorted({m.resource_type for m in meters if m.is_master_meter})
        for resource_type in resource_types:
            masters = [m for m in meters if m.is_master_meter and m.resource_type == resource_type]
            apartment_deltas = [
                readings[m.id].consumption_delta
                for m in meters
                if not m.is_master_meter and m.resource_type == resource_type and m.id in readings
            ]
            apartments_sum = canonical_meter_value(sum(apartment_deltas, start=Decimal("0.000")))
            master_total = None
            if all(m.id in readings for m in masters):
                master_total = canonical_meter_value(
                    sum((readings[m.id].consumption_delta for m in masters), start=Decimal("0.000"))
                )
            balances.append(
                ResourceBalance(
                    resource_type=resource_type,
                    unit=masters[0].unit,
                    master_total=master_total,
                    apartments_sum=apartments_sum,
                    unallocated_loss=(
                        canonical_meter_value(master_total - apartments_sum)
                        if master_total is not None
                        else None
                    ),
                )
            )
        return balances

    # --- Estimates for missing apartments -------------------------------------------------------

    async def estimate_missing(self, user: CurrentUserDTO, period_code: str) -> EstimateResponse:
        period = await self._period(user.building_id, period_code, for_update=True)
        if period.status != PeriodStatus.REVIEW_BY_BOARD:
            raise DomainError(
                status_code=409,
                error_code="PERIOD_NOT_IN_REVIEW",
                message="Missing readings can only be estimated during the board review.",
                extra={"period_status": PeriodStatus(period.status).value},
            )
        meters = list(
            await self.session.scalars(
                select(MeterDevice)
                .where(
                    MeterDevice.building_id == user.building_id,
                    MeterDevice.apartment_id.is_not(None),
                    MeterDevice.is_active.is_(True),
                )
                .order_by(MeterDevice.id)
                .with_for_update()
            )
        )
        with_reading = set(
            await self.session.scalars(
                select(MeterReading.meter_id).where(MeterReading.period_id == period.id)
            )
        )

        estimated = []
        for meter in (m for m in meters if m.id not in with_reading):
            previous_value = await self._previous_value(meter, period)
            average = calculate_rolling_average_delta(
                await self._history_deltas(meter.id, period), NO_HISTORY_AVERAGE
            )
            reading = MeterReading(
                building_id=meter.building_id,
                meter_id=meter.id,
                period_id=period.id,
                value=canonical_meter_value(previous_value + average),
                previous_value=previous_value,
                consumption_delta=average,
                source=ReadingSource.AUTO_ESTIMATED,
                is_anomaly_confirmed=False,
                submitted_by_user_id=None,
            )
            self.session.add(reading)
            await self.session.flush()
            self.session.add(
                MeterReadingLog(
                    reading_id=reading.id,
                    old_value=None,
                    new_value=reading.value,
                    source=ReadingSource.AUTO_ESTIMATED,
                    changed_by_user_id=user.id,
                    reason=ESTIMATE_REASON,
                )
            )
            estimated.append((reading, meter))
        await self.session.flush()
        for reading, meter in estimated:
            await self._publish_submitted(reading, meter, period)

        return EstimateResponse(
            period_code=period.period_code,
            estimated_count=len(estimated),
            estimated=[
                EstimatedReading(
                    reading_id=reading.id,
                    meter_id=meter.id,
                    apartment_id=meter.apartment_id,
                    value=reading.value,
                    consumption_delta=reading.consumption_delta,
                )
                for reading, meter in estimated
            ],
        )

    # --- Period status --------------------------------------------------------------------------

    async def change_period_status(
        self, user: CurrentUserDTO, period_code: str, target: PeriodStatus
    ) -> PeriodStatusUpdateResponse:
        period = await self._period(user.building_id, period_code, for_update=True)
        current = PeriodStatus(period.status)
        if not can_transition_period(current, target):
            raise DomainError(
                status_code=409,
                error_code="INVALID_PERIOD_TRANSITION",
                message=f"A period cannot go from {current.value} to {target.value}.",
                extra={
                    "current_status": current.value,
                    "requested_status": target.value,
                    "allowed_statuses": sorted(s.value for s in PERIOD_TRANSITIONS[current]),
                },
            )
        period.status = target
        await self.session.flush()
        await self.event_bus.publish(
            ReadingPeriodStatusChanged(
                period_id=period.id,
                building_id=period.building_id,
                period_code=period.period_code,
                old_status=current,
                new_status=target,
                changed_by_user_id=user.id,
            )
        )
        return PeriodStatusUpdateResponse(
            period_code=period.period_code, previous_status=current, status=target
        )

    # --- Audit history --------------------------------------------------------------------------

    async def get_history(
        self, user: CurrentUserDTO, reading_id: uuid.UUID
    ) -> ReadingHistoryResponse:
        row = (
            await self.session.execute(
                select(MeterReading.meter_id, MeterDevice.apartment_id, ReadingPeriod.period_code)
                .join(MeterDevice, MeterDevice.id == MeterReading.meter_id)
                .join(ReadingPeriod, ReadingPeriod.id == MeterReading.period_id)
                .where(MeterReading.id == reading_id, MeterReading.building_id == user.building_id)
            )
        ).one_or_none()
        visible = row is not None and (
            user.building_role in STAFF_VIEW_ROLES
            or (row.apartment_id is not None and user.membership_for(row.apartment_id))
        )
        if not visible:
            raise _not_found("READING_NOT_FOUND", "Reading not found.")

        logs = list(
            await self.session.scalars(
                select(MeterReadingLog)
                .where(MeterReadingLog.reading_id == reading_id)
                .order_by(MeterReadingLog.changed_at)
            )
        )

        # Residents are shown in their apartment's context; board actions and estimates without
        # one, so a chairman never appears as if living in that apartment.
        def author_key(log: MeterReadingLog) -> tuple[uuid.UUID, uuid.UUID | None]:
            context = row.apartment_id if log.source == ReadingSource.RESIDENT else None
            return (log.changed_by_user_id, context)

        badges = await self.property_service.get_masked_author_badges(
            author_key(log) for log in logs if log.changed_by_user_id is not None
        )
        return ReadingHistoryResponse(
            reading_id=reading_id,
            meter_id=row.meter_id,
            period_code=row.period_code,
            entries=[
                HistoryEntry(
                    changed_at=log.changed_at,
                    old_value=log.old_value,
                    new_value=log.new_value,
                    source=log.source,
                    reason=log.reason,
                    changed_by=badges[author_key(log)] if log.changed_by_user_id else None,
                )
                for log in logs
            ],
        )

    # --- Export ---------------------------------------------------------------------------------

    async def export_rows(self, user: CurrentUserDTO, period_code: str) -> list[ReadingExportRow]:
        """All readings of the period, apartments first (natural order), master meters last."""
        period = await self._period(user.building_id, period_code)
        rows = (
            await self.session.execute(
                select(MeterReading, MeterDevice)
                .join(MeterDevice, MeterDevice.id == MeterReading.meter_id)
                .where(MeterReading.period_id == period.id)
            )
        ).all()
        apartments = {
            a.id: a for a in await self.property_service.list_apartments(user.building_id)
        }
        order = {apartment_id: index for index, apartment_id in enumerate(apartments)}

        def sort_key(item):
            reading, meter = item
            position = order.get(meter.apartment_id, len(order))
            return (position, meter.resource_type, meter.location_label, str(meter.id))

        result = []
        for reading, meter in sorted(rows, key=sort_key):
            apartment = apartments.get(meter.apartment_id) if meter.apartment_id else None
            result.append(
                ReadingExportRow(
                    period_code=period.period_code,
                    entrance=apartment.entrance if apartment else None,
                    apartment_number=apartment.number if apartment else None,
                    meter_id=meter.id,
                    resource_type=meter.resource_type.value,
                    serial_number=meter.serial_number,
                    location_label=meter.location_label,
                    unit=meter.unit.value,
                    previous_value=reading.previous_value,
                    value=reading.value,
                    consumption_delta=reading.consumption_delta,
                    source=reading.source.value,
                    is_anomaly_confirmed=reading.is_anomaly_confirmed,
                    recorded_at=reading.recorded_at,
                )
            )
        return result

    @staticmethod
    def export_filename(period_code: str, exporter: ReadingExporter) -> str:
        return f"readings-{period_code}.{exporter.file_extension}"

    # --- Internals ------------------------------------------------------------------------------

    async def _period(
        self, building_id: uuid.UUID, period_code: str, for_update: bool = False
    ) -> ReadingPeriod:
        statement = select(ReadingPeriod).where(
            ReadingPeriod.building_id == building_id, ReadingPeriod.period_code == period_code
        )
        if for_update:
            statement = statement.with_for_update()
        period = await self.session.scalar(statement)
        if period is None:
            raise _not_found("PERIOD_NOT_FOUND", f"Reading period {period_code} not found.")
        return period

    async def _readings_by_meter(self, period: ReadingPeriod) -> dict[uuid.UUID, MeterReading]:
        readings = await self.session.scalars(
            select(MeterReading).where(MeterReading.period_id == period.id)
        )
        return {reading.meter_id: reading for reading in readings}

    async def _previous_value(self, meter: MeterDevice, period: ReadingPeriod | None) -> Decimal:
        """The meter's reading in the latest earlier period, else its initial value."""
        statement = (
            select(MeterReading.value)
            .join(ReadingPeriod, ReadingPeriod.id == MeterReading.period_id)
            .where(MeterReading.meter_id == meter.id)
            .order_by(ReadingPeriod.period_code.desc())
            .limit(1)
        )
        if period is not None:
            statement = statement.where(ReadingPeriod.period_code < period.period_code)
        value = await self.session.scalar(statement)
        return value if value is not None else meter.initial_value

    async def _history_deltas(self, meter_id: uuid.UUID, period: ReadingPeriod) -> list[Decimal]:
        """Last 3 qualifying deltas before the period, oldest first (ROADMAP section C)."""
        deltas = await self.session.scalars(
            select(MeterReading.consumption_delta)
            .join(ReadingPeriod, ReadingPeriod.id == MeterReading.period_id)
            .where(
                MeterReading.meter_id == meter_id,
                ReadingPeriod.period_code < period.period_code,
                MeterReading.source.in_(HISTORY_SOURCES),
                MeterReading.is_anomaly_confirmed.is_(False),
            )
            .order_by(ReadingPeriod.period_code.desc())
            .limit(3)
        )
        return list(reversed(list(deltas)))

    @staticmethod
    def _reading_response(reading: MeterReading, period: ReadingPeriod) -> ReadingResponse:
        return ReadingResponse(
            reading_id=reading.id,
            meter_id=reading.meter_id,
            period_code=period.period_code,
            value=reading.value,
            consumption_delta=reading.consumption_delta,
            source=reading.source,
            is_anomaly_confirmed=reading.is_anomaly_confirmed,
        )

    async def _publish_submitted(
        self, reading: MeterReading, meter: MeterDevice, period: ReadingPeriod
    ) -> None:
        await self.event_bus.publish(
            MeterReadingSubmitted(
                reading_id=reading.id,
                meter_id=meter.id,
                apartment_id=meter.apartment_id,
                period_code=period.period_code,
                value=reading.value,
                consumption_delta=reading.consumption_delta,
                source=reading.source,
                is_anomaly_confirmed=reading.is_anomaly_confirmed,
                submitted_by_user_id=reading.submitted_by_user_id,
            )
        )


def get_meters_service(
    session: DbSession,
    property_service: PropertyService = Depends(get_property_service),
    event_bus: EventBus = Depends(get_event_bus),
) -> MetersService:
    return MetersService(session, property_service, event_bus)
