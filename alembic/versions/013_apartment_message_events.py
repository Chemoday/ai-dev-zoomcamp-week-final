"""Notification event types of the board <-> apartment threads (epic E8, task P3).

docs/SCHEMA.md section 5: APARTMENT_MESSAGE and APARTMENT_MESSAGE_TO_BOARD join the event_type
checks of notif_preferences and notif_deliveries.

Revision ID: 013
Revises: 012
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = "('EMERGENCY_PUBLISHED', 'EMERGENCY_RESOLVED', 'READING_DEADLINE', 'PERIOD_REVIEW')"
AFTER = (
    "('EMERGENCY_PUBLISHED', 'EMERGENCY_RESOLVED', 'READING_DEADLINE', 'PERIOD_REVIEW', "
    "'APARTMENT_MESSAGE', 'APARTMENT_MESSAGE_TO_BOARD')"
)
NEW = "('APARTMENT_MESSAGE', 'APARTMENT_MESSAGE_TO_BOARD')"
CHECKS = (
    ("notif_preferences", "ck_notif_preferences_event_type"),
    ("notif_deliveries", "ck_notif_deliveries_event_type"),
)


def _replace(event_types: str) -> None:
    for table, name in CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, f"event_type IN {event_types}")


def upgrade() -> None:
    _replace(AFTER)


def downgrade() -> None:
    # The old checks cannot hold the new types: their rows go first.
    for table, _ in CHECKS:
        op.execute(f"DELETE FROM {table} WHERE event_type IN {NEW}")
    _replace(BEFORE)
