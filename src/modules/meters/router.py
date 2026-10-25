"""Thin HTTP layer of the meters module (docs/API.md, sections 2 and 3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import StreamingResponse

from src.core.exceptions import DomainError
from src.core.security import get_current_user, require_building_roles
from src.modules.meters.schemas import (
    PERIOD_CODE_PATTERN,
    DashboardResponse,
    EstimateResponse,
    MatrixResponse,
    PeriodStatusUpdateRequest,
    PeriodStatusUpdateResponse,
    ReadingHistoryResponse,
    ReadingResponse,
    ReadingSubmitRequest,
)
from src.modules.meters.service import MetersService, get_meters_service
from src.modules.property.schemas import BuildingRole, CurrentUserDTO
from src.plugins.registry import PluginRegistry, get_plugin_registry

router = APIRouter(prefix="/api/v1/meters", tags=["meters"])

PeriodCodePath = Annotated[str, Path(pattern=PERIOD_CODE_PATTERN, examples=["2026-10"])]

staff_viewer = require_building_roles(
    BuildingRole.CHAIRMAN, BuildingRole.BOARD, BuildingRole.ACCOUNTANT
)
period_manager = require_building_roles(BuildingRole.CHAIRMAN, BuildingRole.ACCOUNTANT)


@router.get("/my-dashboard", response_model=DashboardResponse)
async def my_dashboard(
    period_code: Annotated[str | None, Query(pattern=PERIOD_CODE_PATTERN)] = None,
    user: CurrentUserDTO = Depends(get_current_user),
    service: MetersService = Depends(get_meters_service),
) -> DashboardResponse:
    return await service.get_dashboard(user, period_code)


@router.post("/readings", response_model=ReadingResponse)
async def submit_reading(
    body: ReadingSubmitRequest,
    user: CurrentUserDTO = Depends(get_current_user),
    service: MetersService = Depends(get_meters_service),
) -> ReadingResponse:
    return await service.submit_reading(user, body)


@router.get("/readings/{reading_id}/history", response_model=ReadingHistoryResponse)
async def reading_history(
    reading_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_current_user),
    service: MetersService = Depends(get_meters_service),
) -> ReadingHistoryResponse:
    return await service.get_history(user, reading_id)


@router.get("/periods/{period_code}/matrix", response_model=MatrixResponse)
async def period_matrix(
    period_code: PeriodCodePath,
    user: CurrentUserDTO = Depends(staff_viewer),
    service: MetersService = Depends(get_meters_service),
) -> MatrixResponse:
    return await service.get_matrix(user, period_code)


@router.post("/periods/{period_code}/estimate-missing", response_model=EstimateResponse)
async def estimate_missing(
    period_code: PeriodCodePath,
    user: CurrentUserDTO = Depends(period_manager),
    service: MetersService = Depends(get_meters_service),
) -> EstimateResponse:
    return await service.estimate_missing(user, period_code)


@router.patch("/periods/{period_code}/status", response_model=PeriodStatusUpdateResponse)
async def change_period_status(
    period_code: PeriodCodePath,
    body: PeriodStatusUpdateRequest,
    user: CurrentUserDTO = Depends(period_manager),
    service: MetersService = Depends(get_meters_service),
) -> PeriodStatusUpdateResponse:
    return await service.change_period_status(user, period_code, body.status)


@router.get("/periods/{period_code}/export-csv", response_class=StreamingResponse)
async def export_csv(
    period_code: PeriodCodePath,
    user: CurrentUserDTO = Depends(staff_viewer),
    service: MetersService = Depends(get_meters_service),
    plugins: PluginRegistry = Depends(get_plugin_registry),
) -> StreamingResponse:
    try:
        exporter = plugins.exporter("csv")
    except LookupError as error:
        raise DomainError(
            status_code=404,
            error_code="EXPORTER_NOT_ENABLED",
            message="CSV export is not enabled on this instance (ENABLED_PLUGINS).",
        ) from error
    # Rows are loaded before streaming starts: the DB session is closed once the response begins.
    rows = await service.export_rows(user, period_code)
    filename = service.export_filename(period_code, exporter)
    return StreamingResponse(
        exporter.render(rows),
        media_type=exporter.media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
