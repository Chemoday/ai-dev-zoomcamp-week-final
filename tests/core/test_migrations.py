from src.core.migrations import MigrationAction, plan_migration


def test_fresh_database_is_upgraded() -> None:
    assert plan_migration(current_revisions=set(), known_revisions={"001", "002"}) is (
        MigrationAction.UPGRADE
    )


def test_database_behind_or_at_head_is_upgraded() -> None:
    assert plan_migration({"001"}, {"001", "002"}) is MigrationAction.UPGRADE
    assert plan_migration({"002"}, {"001", "002"}) is MigrationAction.UPGRADE


def test_database_ahead_of_image_is_skipped_so_rollback_can_start() -> None:
    # Rolled back to an image that only knows 001, but the newer release already applied 002.
    assert plan_migration({"002"}, {"001"}) is MigrationAction.SKIP_SCHEMA_AHEAD
