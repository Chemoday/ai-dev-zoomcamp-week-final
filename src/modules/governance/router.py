"""Thin HTTP layer of the governance module (docs/API.md section 8)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from src.core.exceptions import AUTH_ERRORS, error_responses
from src.core.security import get_current_user
from src.modules.governance.schemas import (
    POLLS_PAGE_MAX,
    BallotRequest,
    DeadlineUpdate,
    PollDraftRequest,
    PollStatus,
    PollSummary,
    PollView,
)
from src.modules.governance.service import GovernanceService, get_governance_service
from src.modules.property.schemas import CurrentUserDTO

router = APIRouter(
    prefix="/api/v1/governance",
    tags=["governance"],
    responses=error_responses({401: AUTH_ERRORS, 500: ["INTERNAL_SERVER_ERROR"]}),
)

Service = Annotated[GovernanceService, Depends(get_governance_service)]
Caller = Annotated[CurrentUserDTO, Depends(get_current_user)]


@router.get(
    "/polls",
    response_model=list[PollSummary],
    responses=error_responses({422: ["VALIDATION_ERROR"]}),
)
async def list_polls(
    user: Caller,
    service: Service,
    status: PollStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=POLLS_PAGE_MAX)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[PollSummary]:
    """The polls the caller sees: open ones first (nearest deadline first), then the newest."""
    return await service.list_polls(user, status, limit, offset)


@router.post(
    "/polls",
    response_model=PollView,
    status_code=201,
    responses=error_responses({403: ["FORBIDDEN"], 422: ["VALIDATION_ERROR", "INVALID_TARGET"]}),
)
async def create_poll(body: PollDraftRequest, user: Caller, service: Service) -> PollView:
    """Create a draft (the board)."""
    return await service.create_poll(user, body)


@router.get(
    "/polls/{poll_id}",
    response_model=PollView,
    responses=error_responses({404: ["POLL_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}),
)
async def get_poll(poll_id: uuid.UUID, user: Caller, service: Service) -> PollView:
    return await service.get_poll(user, poll_id)


@router.put(
    "/polls/{poll_id}",
    response_model=PollView,
    responses=error_responses(
        {
            403: ["FORBIDDEN"],
            404: ["POLL_NOT_FOUND"],
            409: ["POLL_NOT_DRAFT"],
            422: ["VALIDATION_ERROR", "INVALID_TARGET"],
        }
    ),
)
async def replace_draft(
    poll_id: uuid.UUID, body: PollDraftRequest, user: Caller, service: Service
) -> PollView:
    """Replace a draft (the board); questions and options get new ids."""
    return await service.replace_draft(user, poll_id, body)


@router.delete(
    "/polls/{poll_id}",
    status_code=204,
    responses=error_responses(
        {
            403: ["FORBIDDEN"],
            404: ["POLL_NOT_FOUND"],
            409: ["POLL_NOT_DRAFT"],
            422: ["VALIDATION_ERROR"],
        }
    ),
)
async def delete_draft(poll_id: uuid.UUID, user: Caller, service: Service) -> Response:
    """Delete a draft (the board); an open poll is cancelled instead."""
    await service.delete_draft(user, poll_id)
    return Response(status_code=204)


@router.patch(
    "/polls/{poll_id}",
    response_model=PollView,
    responses=error_responses(
        {
            403: ["FORBIDDEN"],
            404: ["POLL_NOT_FOUND"],
            409: ["POLL_NOT_OPEN"],
            422: ["VALIDATION_ERROR", "INVALID_DEADLINE"],
        }
    ),
)
async def extend_poll(
    poll_id: uuid.UUID, body: DeadlineUpdate, user: Caller, service: Service
) -> PollView:
    """Extend an open poll (the board): the new deadline is later than the current one."""
    return await service.extend_poll(user, poll_id, body)


@router.post(
    "/polls/{poll_id}/open",
    response_model=PollView,
    responses=error_responses(
        {
            403: ["FORBIDDEN"],
            404: ["POLL_NOT_FOUND"],
            409: ["POLL_NOT_DRAFT"],
            422: ["VALIDATION_ERROR", "INVALID_DEADLINE", "POLL_HAS_NO_ELECTORATE"],
        }
    ),
)
async def open_poll(poll_id: uuid.UUID, user: Caller, service: Service) -> PollView:
    """Open a draft and fix who may answer (the board)."""
    return await service.open_poll(user, poll_id)


_END_RESPONSES = error_responses(
    {
        403: ["FORBIDDEN"],
        404: ["POLL_NOT_FOUND"],
        409: ["POLL_NOT_OPEN"],
        422: ["VALIDATION_ERROR"],
    }
)


@router.post("/polls/{poll_id}/close", response_model=PollView, responses=_END_RESPONSES)
async def close_poll(poll_id: uuid.UUID, user: Caller, service: Service) -> PollView:
    """Close an open poll early (the board)."""
    return await service.close_poll(user, poll_id)


@router.post("/polls/{poll_id}/cancel", response_model=PollView, responses=_END_RESPONSES)
async def cancel_poll(poll_id: uuid.UUID, user: Caller, service: Service) -> PollView:
    """Cancel an open poll (the board); its answers are never shown."""
    return await service.cancel_poll(user, poll_id)


@router.put(
    "/polls/{poll_id}/ballot",
    response_model=PollView,
    responses=error_responses(
        {
            403: ["NOT_ELIGIBLE"],
            404: ["POLL_NOT_FOUND"],
            409: ["POLL_NOT_OPEN"],
            422: ["VALIDATION_ERROR", "INVALID_ANSWER"],
        }
    ),
)
async def answer_poll(
    poll_id: uuid.UUID, body: BallotRequest, user: Caller, service: Service
) -> PollView:
    """Answer, or replace the answer of, the caller's apartment or person; the last one counts."""
    return await service.answer(user, poll_id, body)
