"""Private SQLAlchemy models of the governance module. NEVER import them from another module.

docs/SCHEMA.md section 8. Composite foreign keys keep every row in its poll's building, as in
the comm_* tables; apartments and users are read only through PropertyService.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, uuid_pk, varchar_enum
from src.modules.governance.schemas import (
    AnswerMode,
    Eligibility,
    PollKind,
    PollStatus,
    PollUnit,
    ValueType,
)
from src.modules.property.schemas import TargetScope

# Keep these texts identical to alembic/versions/015_governance_polls.py: tests compare catalogs.
POLL_TARGET_CHECK = (
    "(target_scope = 'ALL_BUILDING' AND target_entrance IS NULL"
    " AND target_apartment_ids IS NULL)"
    " OR (target_scope = 'ENTRANCE' AND target_entrance IS NOT NULL"
    " AND target_apartment_ids IS NULL)"
    " OR (target_scope = 'APARTMENT_LIST' AND target_entrance IS NULL"
    " AND target_apartment_ids IS NOT NULL"
    " AND jsonb_typeof(target_apartment_ids) = 'array'"
    " AND target_apartment_ids <> '[]'::jsonb"
    " AND NOT jsonb_path_exists(target_apartment_ids,"
    ' \'$[*] ? (!(@.type() == "string"'
    ' && @ like_regex "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"))\'))'
)
POLL_STATUS_TIMES_CHECK = (
    "(status = 'DRAFT' AND opened_at IS NULL AND closed_at IS NULL AND cancelled_at IS NULL)"
    " OR (status = 'OPEN' AND opened_at IS NOT NULL AND closed_at IS NULL"
    " AND cancelled_at IS NULL)"
    " OR (status = 'CLOSED' AND opened_at IS NOT NULL AND closed_at IS NOT NULL"
    " AND cancelled_at IS NULL)"
    " OR (status = 'CANCELLED' AND opened_at IS NOT NULL AND closed_at IS NULL"
    " AND cancelled_at IS NOT NULL)"
)
QUESTION_CHOICES_CHECK = (
    "(answer_mode = 'MULTIPLE_CHOICE' AND min_choices IS NOT NULL AND max_choices IS NOT NULL"
    " AND min_choices >= 1 AND min_choices <= max_choices)"
    " OR (answer_mode <> 'MULTIPLE_CHOICE' AND min_choices IS NULL AND max_choices IS NULL)"
)
QUESTION_BOUNDS_CHECK = (
    "(min_value IS NULL AND max_value IS NULL)"
    " OR (answer_mode = 'OWN_VALUE' AND value_type IN ('NUMBER', 'DATE'))"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(**kwargs: Any) -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
        **kwargs,
    )


class Poll(Base):
    """An informative poll of the board (kind DECISION comes with part B)."""

    __tablename__ = "gov_polls"
    __table_args__ = (
        UniqueConstraint("id", "building_id", name="uq_gov_polls_id_building"),
        CheckConstraint(POLL_TARGET_CHECK, name="ck_gov_polls_target_consistent"),
        CheckConstraint("target_entrance > 0", name="ck_gov_polls_target_entrance_positive"),
        CheckConstraint(POLL_STATUS_TIMES_CHECK, name="ck_gov_polls_status_times"),
        CheckConstraint("btrim(title) <> ''", name="ck_gov_polls_title_not_blank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False
    )
    kind: Mapped[PollKind] = mapped_column(
        varchar_enum(PollKind, "ck_gov_polls_kind", length=16), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    target_scope: Mapped[TargetScope] = mapped_column(
        varchar_enum(TargetScope, "ck_gov_polls_target_scope"), nullable=False
    )
    target_entrance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_apartment_ids: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    eligibility: Mapped[Eligibility] = mapped_column(
        varchar_enum(Eligibility, "ck_gov_polls_eligibility", length=16), nullable=False
    )
    unit: Mapped[PollUnit] = mapped_column(
        varchar_enum(PollUnit, "ck_gov_polls_unit", length=16), nullable=False
    )
    status: Mapped[PollStatus] = mapped_column(
        varchar_enum(PollStatus, "ck_gov_polls_status", length=16),
        nullable=False,
        default=PollStatus.DRAFT,
        server_default=PollStatus.DRAFT.value,
    )
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp()


class PollQuestion(Base):
    __tablename__ = "gov_poll_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["poll_id", "building_id"],
            ["gov_polls.id", "gov_polls.building_id"],
            ondelete="CASCADE",
            name="fk_gov_poll_questions_poll_building",
        ),
        UniqueConstraint("poll_id", "position", name="uq_gov_poll_questions_position"),
        UniqueConstraint("id", "poll_id", name="uq_gov_poll_questions_id_poll"),
        CheckConstraint("position > 0", name="ck_gov_poll_questions_position_positive"),
        CheckConstraint("btrim(text) <> ''", name="ck_gov_poll_questions_text_not_blank"),
        CheckConstraint(QUESTION_CHOICES_CHECK, name="ck_gov_poll_questions_choices"),
        CheckConstraint(QUESTION_BOUNDS_CHECK, name="ck_gov_poll_questions_bounds"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    poll_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    building_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    answer_mode: Mapped[AnswerMode] = mapped_column(
        varchar_enum(AnswerMode, "ck_gov_poll_questions_answer_mode", length=16), nullable=False
    )
    value_type: Mapped[ValueType] = mapped_column(
        varchar_enum(ValueType, "ck_gov_poll_questions_value_type", length=16), nullable=False
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    min_choices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_choices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_value: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_value: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PollOption(Base):
    __tablename__ = "gov_poll_options"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "poll_id"],
            ["gov_poll_questions.id", "gov_poll_questions.poll_id"],
            ondelete="CASCADE",
            name="fk_gov_poll_options_question_poll",
        ),
        UniqueConstraint("question_id", "position", name="uq_gov_poll_options_position"),
        UniqueConstraint("question_id", "value", name="uq_gov_poll_options_value"),
        UniqueConstraint("id", "question_id", name="uq_gov_poll_options_id_question"),
        CheckConstraint("position > 0", name="ck_gov_poll_options_position_positive"),
        CheckConstraint("btrim(value) <> ''", name="ck_gov_poll_options_value_not_blank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    poll_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)


class PollElector(Base):
    """One unit of the electorate fixed when the poll opened: an apartment or a person."""

    __tablename__ = "gov_poll_electorate"
    __table_args__ = (
        ForeignKeyConstraint(
            ["poll_id", "building_id"],
            ["gov_polls.id", "gov_polls.building_id"],
            ondelete="CASCADE",
            name="fk_gov_poll_electorate_poll_building",
        ),
        ForeignKeyConstraint(
            ["apartment_id", "building_id"],
            ["property_apartments.id", "property_apartments.building_id"],
            name="fk_gov_poll_electorate_apartment_building",
        ),
        UniqueConstraint("poll_id", "apartment_id", name="uq_gov_poll_electorate_apartment"),
        UniqueConstraint("poll_id", "user_id", name="uq_gov_poll_electorate_user"),
        UniqueConstraint("id", "poll_id", name="uq_gov_poll_electorate_id_poll"),
        CheckConstraint(
            "(apartment_id IS NULL) <> (user_id IS NULL)",
            name="ck_gov_poll_electorate_one_unit",
        ),
        CheckConstraint("weight > 0", name="ck_gov_poll_electorate_weight_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    poll_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    building_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    apartment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=True
    )
    weight: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal(1), server_default=text("1")
    )


class Ballot(Base):
    """The current answers of one unit; every version is also kept in BallotRevision."""

    __tablename__ = "gov_ballots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["elector_id", "poll_id"],
            ["gov_poll_electorate.id", "gov_poll_electorate.poll_id"],
            ondelete="CASCADE",
            name="fk_gov_ballots_elector_poll",
        ),
        UniqueConstraint("elector_id", name="uq_gov_ballots_elector"),
        UniqueConstraint("id", "poll_id", name="uq_gov_ballots_id_poll"),
        CheckConstraint("revision > 0", name="ck_gov_ballots_revision_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    poll_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    elector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cast_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp()


class BallotAnswer(Base):
    __tablename__ = "gov_ballot_answers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ballot_id", "poll_id"],
            ["gov_ballots.id", "gov_ballots.poll_id"],
            ondelete="CASCADE",
            name="fk_gov_ballot_answers_ballot_poll",
        ),
        ForeignKeyConstraint(
            ["question_id", "poll_id"],
            ["gov_poll_questions.id", "gov_poll_questions.poll_id"],
            name="fk_gov_ballot_answers_question_poll",
        ),
        ForeignKeyConstraint(
            ["option_id", "question_id"],
            ["gov_poll_options.id", "gov_poll_options.question_id"],
            name="fk_gov_ballot_answers_option_question",
        ),
        UniqueConstraint(
            "ballot_id", "question_id", "option_id", name="uq_gov_ballot_answers_option"
        ),
        CheckConstraint(
            "(option_id IS NULL) <> (value IS NULL)", name="ck_gov_ballot_answers_one_kind"
        ),
        # One own value per question and ballot (NULL option ids would not collide above).
        Index(
            "uq_gov_ballot_answers_own_value",
            "ballot_id",
            "question_id",
            unique=True,
            postgresql_where=text("option_id IS NULL"),
        ),
        # Foreign-key checks when a draft's questions go, and counting results (V2).
        Index("ix_gov_ballot_answers_question_option", "question_id", "option_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    ballot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    poll_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    option_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class BallotRevision(Base):
    """Every version of a ballot as given; never returned in part A (official decisions need it)."""

    __tablename__ = "gov_ballot_revisions"
    __table_args__ = (
        UniqueConstraint("ballot_id", "revision", name="uq_gov_ballot_revisions_revision"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    ballot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gov_ballots.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    cast_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False
    )
    answers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _timestamp()
