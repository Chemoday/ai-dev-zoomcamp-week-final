"""Public facade of the governance module: informative polls (epic E3 part A, task V1).

Rules: docs/API.md section 8 and docs/epics/E3_GOVERNANCE_VOTING.md section 3. Apartments and
memberships come from PropertyService; property models are never imported or joined.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy import ColumnElement, Text, and_, case, delete, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession
from src.core.exceptions import DomainError
from src.modules.governance.models import (
    Ballot,
    BallotAnswer,
    BallotRevision,
    Poll,
    PollElector,
    PollOption,
    PollQuestion,
)
from src.modules.governance.policies import (
    MAX_POLL_DURATION,
    AnswerProblem,
    CheckedAnswer,
    GivenAnswer,
    QuestionSpec,
    check_ballot,
    effective_status,
    electorate_units,
    is_board,
    target_problem,
)
from src.modules.governance.schemas import (
    AnswerView,
    BallotRequest,
    BallotView,
    DeadlineUpdate,
    Eligibility,
    OptionView,
    PollBase,
    PollDraftRequest,
    PollKind,
    PollStatus,
    PollSummary,
    PollUnit,
    PollView,
    QuestionView,
)
from src.modules.property.schemas import CurrentUserDTO, RelationType, TargetScope
from src.modules.property.service import PropertyService, get_property_service

# Polls whose answers are never shown and which nobody answers.
_NO_ANSWERS = frozenset({PollStatus.DRAFT, PollStatus.CANCELLED})


def _now() -> datetime:
    return datetime.now(UTC)


def _poll_not_found() -> DomainError:
    # The same answer for "does not exist", "another building" and "not visible to you".
    return DomainError(status_code=404, error_code="POLL_NOT_FOUND", message="Poll not found.")


def _forbidden() -> DomainError:
    return DomainError(
        status_code=403, error_code="FORBIDDEN", message="Only the board runs polls."
    )


def _not_draft(status: PollStatus) -> DomainError:
    return DomainError(
        status_code=409,
        error_code="POLL_NOT_DRAFT",
        message="Only a draft can be changed, opened or deleted.",
        extra={"status": status.value},
    )


def _not_open(status: PollStatus) -> DomainError:
    return DomainError(
        status_code=409,
        error_code="POLL_NOT_OPEN",
        message="The poll is not open.",
        extra={"status": status.value},
    )


def _invalid_deadline(message: str) -> DomainError:
    return DomainError(status_code=422, error_code="INVALID_DEADLINE", message=message)


def _not_eligible() -> DomainError:
    return DomainError(
        status_code=403, error_code="NOT_ELIGIBLE", message="You cannot answer this poll."
    )


def _validation_error(field: str, message: str) -> DomainError:
    """The 422 VALIDATION_ERROR shape of the request validator, for a rule checked later."""
    return DomainError(
        status_code=422,
        error_code="VALIDATION_ERROR",
        message="The request is invalid.",
        extra={"errors": [{"loc": ["body", field], "message": message, "type": "value_error"}]},
    )


def _answer_rows(
    answers: Iterable[CheckedAnswer],
) -> set[tuple[uuid.UUID, uuid.UUID | None, str | None]]:
    """The (question, option, value) rows a ballot stores for these answers."""
    rows: set[tuple[uuid.UUID, uuid.UUID | None, str | None]] = set()
    for answer in answers:
        if answer.value is not None:
            rows.add((answer.question_id, None, answer.value))
        rows.update((answer.question_id, option_id, None) for option_id in answer.option_ids)
    return rows


class GovernanceService:
    def __init__(self, session: AsyncSession, property_service: PropertyService):
        self.session = session
        self.property_service = property_service

    # --- The board's actions --------------------------------------------------------------------

    async def create_poll(self, user: CurrentUserDTO, request: PollDraftRequest) -> PollView:
        self._require_board(user)
        await self._check_target(user, request)
        poll = Poll(
            building_id=user.building_id,
            created_by_user_id=user.id,
            kind=PollKind.INFORMATIVE,
            status=PollStatus.DRAFT,
        )
        self._apply_draft(poll, request)
        self.session.add(poll)
        await self.session.flush()
        await self._add_questions(poll, request)
        return await self._view(user, poll)

    async def replace_draft(
        self, user: CurrentUserDTO, poll_id: uuid.UUID, request: PollDraftRequest
    ) -> PollView:
        self._require_board(user)
        poll = await self._board_poll(user, poll_id, lock=True)
        if poll.status != PollStatus.DRAFT:
            raise _not_draft(self._status(poll))
        await self._check_target(user, request)
        self._apply_draft(poll, request)
        poll.updated_at = _now()
        await self.session.execute(delete(PollQuestion).where(PollQuestion.poll_id == poll.id))
        await self._add_questions(poll, request)
        return await self._view(user, poll)

    async def delete_draft(self, user: CurrentUserDTO, poll_id: uuid.UUID) -> None:
        self._require_board(user)
        poll = await self._board_poll(user, poll_id, lock=True)
        if poll.status != PollStatus.DRAFT:
            raise _not_draft(self._status(poll))
        await self.session.delete(poll)
        await self.session.flush()

    async def open_poll(self, user: CurrentUserDTO, poll_id: uuid.UUID) -> PollView:
        """Open a draft and fix its electorate: the units who may answer (weight 1)."""
        self._require_board(user)
        poll = await self._board_poll(user, poll_id, lock=True)
        if poll.status != PollStatus.DRAFT:
            raise _not_draft(self._status(poll))
        now = _now()
        if poll.closes_at <= now:
            raise _invalid_deadline("The deadline must be in the future to open the poll.")
        if poll.closes_at > now + MAX_POLL_DURATION:
            raise _invalid_deadline("A poll stays open for at most a year.")
        electors = await self.property_service.poll_electors(
            poll.building_id,
            poll.target_scope,
            poll.target_entrance,
            [uuid.UUID(value) for value in poll.target_apartment_ids or ()],
            owners_only=poll.eligibility == Eligibility.OWNERS,
        )
        units = electorate_units(
            poll.unit, ((elector.user_id, elector.apartment_id) for elector in electors)
        )
        if not units:
            raise DomainError(
                status_code=422,
                error_code="POLL_HAS_NO_ELECTORATE",
                message="Nobody in the target could answer this poll.",
            )
        by_apartment = poll.unit == PollUnit.APARTMENT
        self.session.add_all(
            PollElector(
                poll_id=poll.id,
                building_id=poll.building_id,
                apartment_id=unit_id if by_apartment else None,
                user_id=None if by_apartment else unit_id,
            )
            for unit_id in units
        )
        poll.status, poll.opened_at, poll.updated_at = PollStatus.OPEN, now, now
        await self.session.flush()
        return await self._view(user, poll)

    async def close_poll(self, user: CurrentUserDTO, poll_id: uuid.UUID) -> PollView:
        return await self._end(user, poll_id, PollStatus.CLOSED)

    async def cancel_poll(self, user: CurrentUserDTO, poll_id: uuid.UUID) -> PollView:
        return await self._end(user, poll_id, PollStatus.CANCELLED)

    async def extend_poll(
        self, user: CurrentUserDTO, poll_id: uuid.UUID, request: DeadlineUpdate
    ) -> PollView:
        self._require_board(user)
        poll = await self._board_poll(user, poll_id, lock=True)
        if self._status(poll) != PollStatus.OPEN:
            raise _not_open(self._status(poll))
        if request.closes_at <= poll.closes_at:
            raise _invalid_deadline("The new deadline must be later than the current one.")
        if request.closes_at > _now() + MAX_POLL_DURATION:
            raise _invalid_deadline("A poll stays open for at most a year.")
        poll.closes_at, poll.updated_at = request.closes_at, _now()
        await self.session.flush()
        return await self._view(user, poll)

    # --- Everyone -------------------------------------------------------------------------------

    async def list_polls(
        self, user: CurrentUserDTO, status: PollStatus | None, limit: int, offset: int
    ) -> list[PollSummary]:
        """Open polls first (the nearest deadline first), then the others, newest first."""
        now = _now()
        is_open = and_(Poll.status == PollStatus.OPEN, Poll.closes_at > now)
        statement = select(Poll).where(self._visible_to(user))
        if status == PollStatus.OPEN:
            statement = statement.where(is_open)
        elif status == PollStatus.CLOSED:
            statement = statement.where(
                or_(
                    Poll.status == PollStatus.CLOSED,
                    and_(Poll.status == PollStatus.OPEN, Poll.closes_at <= now),
                )
            )
        elif status is not None:
            statement = statement.where(Poll.status == status)
        statement = statement.order_by(
            case((is_open, 0), else_=1),
            case((is_open, Poll.closes_at), else_=None),
            func.coalesce(Poll.opened_at, Poll.created_at).desc(),
            Poll.id,
        )
        polls = list(await self.session.scalars(statement.limit(limit).offset(offset)))
        units = await self._caller_units(user, polls)
        answered = await self._answered_units(
            elector for electors in units.values() for elector in electors
        )
        return [
            PollSummary(
                **self._base(poll, now).model_dump(),
                can_answer=self._status(poll, now) == PollStatus.OPEN and bool(units[poll.id]),
                answerable_apartment_ids=self._apartments(units[poll.id]),
                answered=bool(units[poll.id])
                and all(elector.id in answered for elector in units[poll.id]),
            )
            for poll in polls
        ]

    async def get_poll(self, user: CurrentUserDTO, poll_id: uuid.UUID) -> PollView:
        return await self._view(user, await self._visible_poll(user, poll_id))

    async def answer(
        self, user: CurrentUserDTO, poll_id: uuid.UUID, request: BallotRequest
    ) -> PollView:
        """Answer, or replace the answer of, the caller's unit; the last answer counts.

        The poll row is share-locked, so a concurrent close or cancel waits for this answer (or
        this answer sees the poll closed); the unit's row is locked, so two answers of the same
        apartment get consecutive revisions.
        """
        poll = await self._visible_poll(user, poll_id, lock_for_share=True)
        if self._status(poll) != PollStatus.OPEN:
            raise _not_open(self._status(poll))
        elector = await self._elector_for(user, poll, request.apartment_id)
        questions = await self._question_specs(poll.id)
        try:
            checked = check_ballot(
                questions,
                (
                    GivenAnswer(answer.question_id, answer.option_ids, answer.value)
                    for answer in request.answers
                ),
            )
        except AnswerProblem as problem:
            raise DomainError(
                status_code=422,
                error_code="INVALID_ANSWER",
                message="An answer does not fit its question.",
                extra={"question_id": str(problem.question_id), "reason": problem.reason},
            ) from problem
        await self._store_ballot(user, poll, elector, checked)
        return await self._view(user, poll)

    # --- Internals: access ----------------------------------------------------------------------

    @staticmethod
    def _require_board(user: CurrentUserDTO) -> None:
        if not is_board(user.building_role):
            raise _forbidden()

    @staticmethod
    def _visible_to(user: CurrentUserDTO) -> ColumnElement[bool]:
        """Polls of the caller's building the caller may see: the board all of them; everyone
        else an open, closed or cancelled poll targeting one of their apartments."""
        in_building = Poll.building_id == user.building_id
        if is_board(user.building_role):
            return in_building
        if not user.memberships:
            return literal(False)
        targeted: list[ColumnElement[bool]] = [Poll.target_scope == TargetScope.ALL_BUILDING]
        entrances = sorted({membership.entrance for membership in user.memberships})
        targeted.append(
            and_(Poll.target_scope == TargetScope.ENTRANCE, Poll.target_entrance.in_(entrances))
        )
        apartment_ids = sorted(str(apartment_id) for apartment_id in user.apartment_ids)
        targeted.append(
            and_(
                Poll.target_scope == TargetScope.APARTMENT_LIST,
                Poll.target_apartment_ids.has_any(literal(apartment_ids, ARRAY(Text))),
            )
        )
        return and_(in_building, Poll.status != PollStatus.DRAFT, or_(*targeted))

    async def _visible_poll(
        self, user: CurrentUserDTO, poll_id: uuid.UUID, *, lock_for_share: bool = False
    ) -> Poll:
        statement = select(Poll).where(Poll.id == poll_id, self._visible_to(user))
        if lock_for_share:
            statement = statement.with_for_update(read=True).execution_options(
                populate_existing=True
            )
        poll = await self.session.scalar(statement)
        if poll is None:
            raise _poll_not_found()
        return poll

    async def _board_poll(self, user: CurrentUserDTO, poll_id: uuid.UUID, *, lock: bool) -> Poll:
        statement = select(Poll).where(Poll.id == poll_id, Poll.building_id == user.building_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        poll = await self.session.scalar(statement)
        if poll is None:
            raise _poll_not_found()
        return poll

    async def _end(self, user: CurrentUserDTO, poll_id: uuid.UUID, to: PollStatus) -> PollView:
        """Close early or cancel an open poll (not one whose deadline already passed)."""
        self._require_board(user)
        poll = await self._board_poll(user, poll_id, lock=True)
        if self._status(poll) != PollStatus.OPEN:
            raise _not_open(self._status(poll))
        now = _now()
        poll.status, poll.updated_at = to, now
        if to == PollStatus.CLOSED:
            poll.closed_at = now
        else:
            poll.cancelled_at = now
        await self.session.flush()
        return await self._view(user, poll)

    async def _check_target(self, user: CurrentUserDTO, request: PollDraftRequest) -> None:
        def invalid(message: str, **extra: object) -> DomainError:
            return DomainError(
                status_code=422, error_code="INVALID_TARGET", message=message, extra=extra
            )

        if problem := target_problem(
            request.target_scope, request.target_entrance, request.target_apartment_ids
        ):
            raise invalid(problem)
        if request.target_scope == TargetScope.ALL_BUILDING:
            return
        apartments = await self.property_service.list_apartments(user.building_id)
        if request.target_scope == TargetScope.ENTRANCE:
            if request.target_entrance not in {apartment.entrance for apartment in apartments}:
                raise invalid(
                    f"The building has no apartments in entrance {request.target_entrance}."
                )
        elif unknown := set(request.target_apartment_ids or ()) - {a.id for a in apartments}:
            raise invalid(
                "Some target apartments are not in this building.",
                unknown_apartment_ids=sorted(str(apartment_id) for apartment_id in unknown),
            )

    async def _caller_units(
        self, user: CurrentUserDTO, polls: Sequence[Poll]
    ) -> dict[uuid.UUID, list[PollElector]]:
        """Per poll, the snapshot units the caller may answer for, in one query for all polls.

        Unit PERSON: the caller's own row. Unit APARTMENT: the caller's current apartments in
        the snapshot, through an OWNER membership for an owners-only poll; in the order of the
        caller's memberships. Drafts and cancelled polls have none.
        """
        live = {poll.id: poll for poll in polls if poll.status not in _NO_ANSWERS}
        units: dict[uuid.UUID, list[PollElector]] = {poll.id: [] for poll in polls}
        if not live:
            return units
        mine: list[ColumnElement[bool]] = [PollElector.user_id == user.id]
        if user.apartment_ids:
            mine.append(PollElector.apartment_id.in_(sorted(user.apartment_ids)))
        relations = {m.apartment_id: m.relation_type for m in user.memberships}
        order = {m.apartment_id: index for index, m in enumerate(user.memberships)}
        rows = await self.session.scalars(
            select(PollElector).where(PollElector.poll_id.in_(list(live)), or_(*mine))
        )
        for elector in rows:
            poll = live[elector.poll_id]
            if poll.unit == PollUnit.PERSON:
                if elector.user_id == user.id:
                    units[poll.id].append(elector)
            elif elector.apartment_id in relations and (
                poll.eligibility == Eligibility.RESIDENTS
                or relations[elector.apartment_id] == RelationType.OWNER
            ):
                units[poll.id].append(elector)
        for electors in units.values():
            electors.sort(key=lambda elector: order.get(elector.apartment_id, 0))
        return units

    async def _elector_for(
        self, user: CurrentUserDTO, poll: Poll, apartment_id: uuid.UUID | None
    ) -> PollElector:
        """The caller's unit for this answer, locked so two answers of one unit run in turn."""
        units = (await self._caller_units(user, [poll]))[poll.id]
        if poll.unit == PollUnit.PERSON:
            if apartment_id is not None:
                raise _validation_error("apartment_id", "a poll per person takes no apartment_id")
            chosen = units[0] if units else None
        elif apartment_id is None:
            if len(units) > 1:
                raise _validation_error(
                    "apartment_id", "choose the apartment you answer for (apartment_id)"
                )
            chosen = units[0] if units else None
        else:
            chosen = next((unit for unit in units if unit.apartment_id == apartment_id), None)
        if chosen is None:
            raise _not_eligible()
        elector = await self.session.scalar(
            select(PollElector).where(PollElector.id == chosen.id).with_for_update()
        )
        assert elector is not None  # snapshot rows are never deleted while the poll lives
        return elector

    # --- Internals: writing ---------------------------------------------------------------------

    @staticmethod
    def _apply_draft(poll: Poll, request: PollDraftRequest) -> None:
        poll.title = request.title
        poll.description = request.description or ""
        poll.target_scope = request.target_scope
        poll.target_entrance = request.target_entrance
        poll.target_apartment_ids = (
            [str(apartment_id) for apartment_id in request.target_apartment_ids]
            if request.target_apartment_ids
            else None
        )
        poll.eligibility = request.eligibility
        poll.unit = request.unit
        poll.closes_at = request.closes_at

    async def _add_questions(self, poll: Poll, request: PollDraftRequest) -> None:
        for position, draft in enumerate(request.questions, start=1):
            question = PollQuestion(
                poll_id=poll.id,
                building_id=poll.building_id,
                position=position,
                text=draft.text,
                answer_mode=draft.answer_mode,
                value_type=draft.value_type,
                required=draft.required,
                min_choices=draft.min_choices,
                max_choices=draft.max_choices,
                min_value=draft.min_value,
                max_value=draft.max_value,
            )
            self.session.add(question)
            await self.session.flush()
            self.session.add_all(
                PollOption(question_id=question.id, poll_id=poll.id, position=index, value=value)
                for index, value in enumerate(draft.options, start=1)
            )
        await self.session.flush()

    async def _store_ballot(
        self,
        user: CurrentUserDTO,
        poll: Poll,
        elector: PollElector,
        answers: Sequence[CheckedAnswer],
    ) -> None:
        now = _now()
        ballot = await self.session.scalar(
            select(Ballot).where(Ballot.elector_id == elector.id).with_for_update()
        )
        if ballot is None:
            ballot = Ballot(
                poll_id=poll.id,
                elector_id=elector.id,
                cast_by_user_id=user.id,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            self.session.add(ballot)
            await self.session.flush()
        else:
            current = await self.session.execute(
                select(BallotAnswer.question_id, BallotAnswer.option_id, BallotAnswer.value).where(
                    BallotAnswer.ballot_id == ballot.id
                )
            )
            same = {tuple(row) for row in current} == _answer_rows(answers)
            if same and ballot.cast_by_user_id == user.id:
                return  # the same person sends the same answers again: no new version
            ballot.cast_by_user_id, ballot.revision, ballot.updated_at = (
                user.id,
                ballot.revision + 1,
                now,
            )
            await self.session.execute(
                delete(BallotAnswer).where(BallotAnswer.ballot_id == ballot.id)
            )
        rows: list[BallotAnswer] = []
        for answer in answers:
            if answer.value is not None:
                rows.append(
                    BallotAnswer(
                        ballot_id=ballot.id,
                        poll_id=poll.id,
                        question_id=answer.question_id,
                        value=answer.value,
                    )
                )
            rows.extend(
                BallotAnswer(
                    ballot_id=ballot.id,
                    poll_id=poll.id,
                    question_id=answer.question_id,
                    option_id=option_id,
                )
                for option_id in answer.option_ids
            )
        self.session.add_all(rows)
        self.session.add(
            BallotRevision(
                ballot_id=ballot.id,
                revision=ballot.revision,
                cast_by_user_id=user.id,
                answers=[
                    {
                        "question_id": str(answer.question_id),
                        "option_ids": [str(option_id) for option_id in answer.option_ids],
                        "value": answer.value,
                    }
                    for answer in answers
                ],
                created_at=now,
            )
        )
        await self.session.flush()

    # --- Internals: reading ---------------------------------------------------------------------

    @staticmethod
    def _status(poll: Poll, now: datetime | None = None) -> PollStatus:
        return effective_status(PollStatus(poll.status), poll.closes_at, now or _now())

    def _base(self, poll: Poll, now: datetime) -> PollBase:
        return PollBase(
            id=poll.id,
            kind=poll.kind,
            title=poll.title,
            description=poll.description,
            status=self._status(poll, now),
            target_scope=poll.target_scope,
            target_entrance=poll.target_entrance,
            target_apartment_ids=(
                [uuid.UUID(value) for value in poll.target_apartment_ids]
                if poll.target_apartment_ids is not None
                else None
            ),
            eligibility=poll.eligibility,
            unit=poll.unit,
            closes_at=poll.closes_at,
            opened_at=poll.opened_at,
            closed_at=poll.closed_at,
            cancelled_at=poll.cancelled_at,
            created_at=poll.created_at,
        )

    async def _question_specs(self, poll_id: uuid.UUID) -> list[QuestionSpec]:
        questions, options = await self._questions(poll_id)
        return [
            QuestionSpec(
                id=question.id,
                answer_mode=question.answer_mode,
                value_type=question.value_type,
                required=question.required,
                min_choices=question.min_choices,
                max_choices=question.max_choices,
                min_value=question.min_value,
                max_value=question.max_value,
                option_ids=frozenset(option.id for option in options.get(question.id, ())),
            )
            for question in questions
        ]

    async def _questions(
        self, poll_id: uuid.UUID
    ) -> tuple[list[PollQuestion], dict[uuid.UUID, list[PollOption]]]:
        questions = list(
            await self.session.scalars(
                select(PollQuestion)
                .where(PollQuestion.poll_id == poll_id)
                .order_by(PollQuestion.position)
            )
        )
        options: dict[uuid.UUID, list[PollOption]] = {}
        for option in await self.session.scalars(
            select(PollOption).where(PollOption.poll_id == poll_id).order_by(PollOption.position)
        ):
            options.setdefault(option.question_id, []).append(option)
        return questions, options

    async def _view(self, user: CurrentUserDTO, poll: Poll) -> PollView:
        now = _now()
        questions, options = await self._questions(poll.id)
        electors = (await self._caller_units(user, [poll]))[poll.id]
        ballots = await self._ballots(electors, questions, options)
        return PollView(
            **self._base(poll, now).model_dump(),
            questions=[
                QuestionView(
                    id=question.id,
                    position=question.position,
                    text=question.text,
                    answer_mode=question.answer_mode,
                    value_type=question.value_type,
                    required=question.required,
                    min_choices=question.min_choices,
                    max_choices=question.max_choices,
                    min_value=question.min_value,
                    max_value=question.max_value,
                    options=[
                        OptionView(id=option.id, position=option.position, value=option.value)
                        for option in options.get(question.id, [])
                    ],
                )
                for question in questions
            ],
            can_answer=self._status(poll, now) == PollStatus.OPEN and bool(electors),
            answerable_apartment_ids=self._apartments(electors),
            my_ballots=ballots,
        )

    @staticmethod
    def _apartments(electors: Iterable[PollElector]) -> list[uuid.UUID]:
        return [elector.apartment_id for elector in electors if elector.apartment_id is not None]

    async def _ballots(
        self,
        electors: Sequence[PollElector],
        questions: Sequence[PollQuestion],
        options: dict[uuid.UUID, list[PollOption]],
    ) -> list[BallotView]:
        """The current answers of these units, in question order and option order."""
        if not electors:
            return []
        ballots = {
            ballot.elector_id: ballot
            for ballot in await self.session.scalars(
                select(Ballot).where(Ballot.elector_id.in_([elector.id for elector in electors]))
            )
        }
        if not ballots:
            return []
        rows = list(
            await self.session.scalars(
                select(BallotAnswer).where(
                    BallotAnswer.ballot_id.in_([ballot.id for ballot in ballots.values()])
                )
            )
        )
        question_order = {question.id: question.position for question in questions}
        option_order = {
            option.id: option.position for group in options.values() for option in group
        }
        rows.sort(
            key=lambda row: (
                question_order.get(row.question_id, 0),
                option_order.get(row.option_id, 0) if row.option_id else 0,
            )
        )
        grouped: dict[uuid.UUID, dict[uuid.UUID, AnswerView]] = {}
        for row in rows:
            view = grouped.setdefault(row.ballot_id, {}).setdefault(
                row.question_id, AnswerView(question_id=row.question_id, option_ids=[], value=None)
            )
            if row.option_id is not None:
                view.option_ids.append(row.option_id)
            else:
                view.value = row.value
        return [
            BallotView(
                apartment_id=elector.apartment_id,
                answers=list(grouped.get(ballots[elector.id].id, {}).values()),
                updated_at=ballots[elector.id].updated_at,
            )
            for elector in electors
            if elector.id in ballots
        ]

    async def _answered_units(self, electors: Iterable[PollElector]) -> set[uuid.UUID]:
        ids = [elector.id for elector in electors]
        if not ids:
            return set()
        return set(
            await self.session.scalars(select(Ballot.elector_id).where(Ballot.elector_id.in_(ids)))
        )


def get_governance_service(
    session: DbSession, property_service: PropertyService = Depends(get_property_service)
) -> GovernanceService:
    return GovernanceService(session, property_service)
