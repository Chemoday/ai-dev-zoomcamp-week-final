"""Informative polls (epic E3 part A, task V1).

docs/SCHEMA.md section 8: gov_polls, gov_poll_questions, gov_poll_options, gov_poll_electorate,
gov_ballots, gov_ballot_answers and gov_ballot_revisions.

Revision ID: 015
Revises: 014
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.UUID(as_uuid=True)

# Keep these texts identical to src/modules/governance/models.py: tests compare the catalogs.
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


def _timestamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "gov_polls",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("building_id", UUID, sa.ForeignKey("property_buildings.id"), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_scope", sa.String(32), nullable=False),
        sa.Column("target_entrance", sa.Integer(), nullable=True),
        sa.Column("target_apartment_ids", JSONB(), nullable=True),
        sa.Column("eligibility", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.UniqueConstraint("id", "building_id", name="uq_gov_polls_id_building"),
        sa.CheckConstraint("kind IN ('INFORMATIVE')", name="ck_gov_polls_kind"),
        sa.CheckConstraint(
            "target_scope IN ('ALL_BUILDING', 'ENTRANCE', 'APARTMENT_LIST')",
            name="ck_gov_polls_target_scope",
        ),
        sa.CheckConstraint(
            "eligibility IN ('RESIDENTS', 'OWNERS')", name="ck_gov_polls_eligibility"
        ),
        sa.CheckConstraint("unit IN ('APARTMENT', 'PERSON')", name="ck_gov_polls_unit"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'OPEN', 'CLOSED', 'CANCELLED')", name="ck_gov_polls_status"
        ),
        sa.CheckConstraint(POLL_TARGET_CHECK, name="ck_gov_polls_target_consistent"),
        sa.CheckConstraint("target_entrance > 0", name="ck_gov_polls_target_entrance_positive"),
        sa.CheckConstraint(POLL_STATUS_TIMES_CHECK, name="ck_gov_polls_status_times"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_gov_polls_title_not_blank"),
    )
    op.create_index("ix_gov_polls_building_id", "gov_polls", ["building_id"])

    op.create_table(
        "gov_poll_questions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("poll_id", UUID, nullable=False),
        sa.Column("building_id", UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("answer_mode", sa.String(16), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("min_choices", sa.Integer(), nullable=True),
        sa.Column("max_choices", sa.Integer(), nullable=True),
        sa.Column("min_value", sa.String(32), nullable=True),
        sa.Column("max_value", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(
            ["poll_id", "building_id"],
            ["gov_polls.id", "gov_polls.building_id"],
            ondelete="CASCADE",
            name="fk_gov_poll_questions_poll_building",
        ),
        sa.UniqueConstraint("poll_id", "position", name="uq_gov_poll_questions_position"),
        sa.UniqueConstraint("id", "poll_id", name="uq_gov_poll_questions_id_poll"),
        sa.CheckConstraint(
            "answer_mode IN ('SINGLE_CHOICE', 'MULTIPLE_CHOICE', 'OWN_VALUE')",
            name="ck_gov_poll_questions_answer_mode",
        ),
        sa.CheckConstraint(
            "value_type IN ('TEXT', 'NUMBER', 'DATE')", name="ck_gov_poll_questions_value_type"
        ),
        sa.CheckConstraint("position > 0", name="ck_gov_poll_questions_position_positive"),
        sa.CheckConstraint("btrim(text) <> ''", name="ck_gov_poll_questions_text_not_blank"),
        sa.CheckConstraint(QUESTION_CHOICES_CHECK, name="ck_gov_poll_questions_choices"),
        sa.CheckConstraint(QUESTION_BOUNDS_CHECK, name="ck_gov_poll_questions_bounds"),
    )
    op.create_index("ix_gov_poll_questions_poll_id", "gov_poll_questions", ["poll_id"])

    op.create_table(
        "gov_poll_options",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("question_id", UUID, nullable=False),
        sa.Column("poll_id", UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["question_id", "poll_id"],
            ["gov_poll_questions.id", "gov_poll_questions.poll_id"],
            ondelete="CASCADE",
            name="fk_gov_poll_options_question_poll",
        ),
        sa.UniqueConstraint("question_id", "position", name="uq_gov_poll_options_position"),
        sa.UniqueConstraint("question_id", "value", name="uq_gov_poll_options_value"),
        sa.UniqueConstraint("id", "question_id", name="uq_gov_poll_options_id_question"),
        sa.CheckConstraint("position > 0", name="ck_gov_poll_options_position_positive"),
        sa.CheckConstraint("btrim(value) <> ''", name="ck_gov_poll_options_value_not_blank"),
    )
    op.create_index("ix_gov_poll_options_question_id", "gov_poll_options", ["question_id"])
    op.create_index("ix_gov_poll_options_poll_id", "gov_poll_options", ["poll_id"])

    op.create_table(
        "gov_poll_electorate",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("poll_id", UUID, nullable=False),
        sa.Column("building_id", UUID, nullable=False),
        sa.Column("apartment_id", UUID, nullable=True),
        sa.Column("user_id", UUID, sa.ForeignKey("core_users.id"), nullable=True),
        sa.Column("weight", sa.Numeric(12, 6), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(
            ["poll_id", "building_id"],
            ["gov_polls.id", "gov_polls.building_id"],
            ondelete="CASCADE",
            name="fk_gov_poll_electorate_poll_building",
        ),
        sa.ForeignKeyConstraint(
            ["apartment_id", "building_id"],
            ["property_apartments.id", "property_apartments.building_id"],
            name="fk_gov_poll_electorate_apartment_building",
        ),
        sa.UniqueConstraint("poll_id", "apartment_id", name="uq_gov_poll_electorate_apartment"),
        sa.UniqueConstraint("poll_id", "user_id", name="uq_gov_poll_electorate_user"),
        sa.UniqueConstraint("id", "poll_id", name="uq_gov_poll_electorate_id_poll"),
        sa.CheckConstraint(
            "(apartment_id IS NULL) <> (user_id IS NULL)", name="ck_gov_poll_electorate_one_unit"
        ),
        sa.CheckConstraint("weight > 0", name="ck_gov_poll_electorate_weight_positive"),
    )
    op.create_index("ix_gov_poll_electorate_poll_id", "gov_poll_electorate", ["poll_id"])

    op.create_table(
        "gov_ballots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("poll_id", UUID, nullable=False),
        sa.Column("elector_id", UUID, nullable=False),
        sa.Column("cast_by_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["elector_id", "poll_id"],
            ["gov_poll_electorate.id", "gov_poll_electorate.poll_id"],
            ondelete="CASCADE",
            name="fk_gov_ballots_elector_poll",
        ),
        sa.UniqueConstraint("elector_id", name="uq_gov_ballots_elector"),
        sa.UniqueConstraint("id", "poll_id", name="uq_gov_ballots_id_poll"),
        sa.CheckConstraint("revision > 0", name="ck_gov_ballots_revision_positive"),
    )
    op.create_index("ix_gov_ballots_poll_id", "gov_ballots", ["poll_id"])

    op.create_table(
        "gov_ballot_answers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("ballot_id", UUID, nullable=False),
        sa.Column("poll_id", UUID, nullable=False),
        sa.Column("question_id", UUID, nullable=False),
        sa.Column("option_id", UUID, nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ballot_id", "poll_id"],
            ["gov_ballots.id", "gov_ballots.poll_id"],
            ondelete="CASCADE",
            name="fk_gov_ballot_answers_ballot_poll",
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "poll_id"],
            ["gov_poll_questions.id", "gov_poll_questions.poll_id"],
            name="fk_gov_ballot_answers_question_poll",
        ),
        sa.ForeignKeyConstraint(
            ["option_id", "question_id"],
            ["gov_poll_options.id", "gov_poll_options.question_id"],
            name="fk_gov_ballot_answers_option_question",
        ),
        sa.UniqueConstraint(
            "ballot_id", "question_id", "option_id", name="uq_gov_ballot_answers_option"
        ),
        sa.CheckConstraint(
            "(option_id IS NULL) <> (value IS NULL)", name="ck_gov_ballot_answers_one_kind"
        ),
    )
    op.create_index("ix_gov_ballot_answers_ballot_id", "gov_ballot_answers", ["ballot_id"])
    op.create_index(
        "uq_gov_ballot_answers_own_value",
        "gov_ballot_answers",
        ["ballot_id", "question_id"],
        unique=True,
        postgresql_where=sa.text("option_id IS NULL"),
    )
    op.create_index(
        "ix_gov_ballot_answers_question_option", "gov_ballot_answers", ["question_id", "option_id"]
    )

    op.create_table(
        "gov_ballot_revisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "ballot_id",
            UUID,
            sa.ForeignKey("gov_ballots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("cast_by_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("answers", JSONB(), nullable=False),
        _timestamp("created_at"),
        sa.UniqueConstraint("ballot_id", "revision", name="uq_gov_ballot_revisions_revision"),
    )


def downgrade() -> None:
    op.drop_table("gov_ballot_revisions")
    op.drop_index("ix_gov_ballot_answers_question_option", table_name="gov_ballot_answers")
    op.drop_index("uq_gov_ballot_answers_own_value", table_name="gov_ballot_answers")
    op.drop_index("ix_gov_ballot_answers_ballot_id", table_name="gov_ballot_answers")
    op.drop_table("gov_ballot_answers")
    op.drop_index("ix_gov_ballots_poll_id", table_name="gov_ballots")
    op.drop_table("gov_ballots")
    op.drop_index("ix_gov_poll_electorate_poll_id", table_name="gov_poll_electorate")
    op.drop_table("gov_poll_electorate")
    op.drop_index("ix_gov_poll_options_poll_id", table_name="gov_poll_options")
    op.drop_index("ix_gov_poll_options_question_id", table_name="gov_poll_options")
    op.drop_table("gov_poll_options")
    op.drop_index("ix_gov_poll_questions_poll_id", table_name="gov_poll_questions")
    op.drop_table("gov_poll_questions")
    op.drop_index("ix_gov_polls_building_id", table_name="gov_polls")
    op.drop_table("gov_polls")
