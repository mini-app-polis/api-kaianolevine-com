"""Unit tests for the migration runner.

The runner is asyncpg-specific so we don't run real DB integration here —
the test suite uses SQLite in-memory, which can't exercise asyncpg or
Postgres advisory locks. Pure functions (file discovery, marker parsing,
URL normalization, bootstrap-exclude parsing) are tested directly.
The asyncpg interaction is tested with mocks to verify the runner's
control flow without touching a real DB.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make scripts/ importable. The runner script is not a package member, just
# a standalone file under scripts/.
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import apply_migrations as runner  # noqa: E402


class TestNormalizeDatabaseUrl:
    def test_strips_sqlalchemy_asyncpg_driver(self):
        assert (
            runner._normalize_database_url("postgresql+asyncpg://u:p@h/db")
            == "postgresql://u:p@h/db"
        )

    def test_upgrades_legacy_postgres_scheme(self):
        assert (
            runner._normalize_database_url("postgres://u:p@h/db")
            == "postgresql://u:p@h/db"
        )

    def test_leaves_plain_postgres_url_unchanged(self):
        assert (
            runner._normalize_database_url("postgresql://u:p@h/db")
            == "postgresql://u:p@h/db"
        )

    def test_url_with_query_params(self):
        # asyncpg connection params (sslmode etc) should survive.
        url = "postgresql+asyncpg://u:p@h/db?sslmode=require"
        assert (
            runner._normalize_database_url(url)
            == "postgresql://u:p@h/db?sslmode=require"
        )


class TestIsTransactional:
    def test_default_is_transactional(self):
        assert runner._is_transactional("CREATE TABLE foo (id INT);") is True

    def test_no_transaction_marker_first_line(self):
        sql = "-- migration:notransaction\nCREATE INDEX CONCURRENTLY foo ON bar(x);"
        assert runner._is_transactional(sql) is False

    def test_marker_with_leading_whitespace(self):
        sql = "\n\n  -- migration:notransaction\nSELECT 1;"
        assert runner._is_transactional(sql) is False

    def test_marker_only_recognized_on_first_line(self):
        # If the marker appears on a later line, it's just a comment.
        sql = "CREATE TABLE foo (id INT);\n-- migration:notransaction\n"
        assert runner._is_transactional(sql) is True

    def test_other_first_line_comments_are_transactional(self):
        sql = "-- Migration 019: WCS entity substrate\nCREATE TABLE foo (id INT);"
        assert runner._is_transactional(sql) is True


class TestListMigrationFiles:
    def test_lists_valid_migrations_in_numeric_order(self, tmp_path, monkeypatch):
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        # Create out of lexical order to verify numeric sorting.
        (migrations / "010_tenth.sql").write_text("--")
        (migrations / "001_first.sql").write_text("--")
        (migrations / "002_second.sql").write_text("--")

        monkeypatch.setattr(runner, "MIGRATIONS_DIR", migrations)

        files = runner._list_migration_files()
        names = [f.name for f in files]
        assert names == ["001_first.sql", "002_second.sql", "010_tenth.sql"]

    def test_ignores_non_matching_filenames(self, tmp_path, monkeypatch):
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "001_valid.sql").write_text("--")
        (migrations / "README.md").write_text("docs")
        (migrations / "archived_v1.sql").write_text("--")  # missing NNN prefix
        (migrations / "001.sql").write_text("--")  # missing description

        monkeypatch.setattr(runner, "MIGRATIONS_DIR", migrations)

        files = runner._list_migration_files()
        assert [f.name for f in files] == ["001_valid.sql"]

    def test_missing_migrations_dir_raises(self, tmp_path, monkeypatch):
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setattr(runner, "MIGRATIONS_DIR", nonexistent)
        with pytest.raises(FileNotFoundError):
            runner._list_migration_files()

    def test_empty_dir_returns_empty_list(self, tmp_path, monkeypatch):
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        monkeypatch.setattr(runner, "MIGRATIONS_DIR", migrations)
        assert runner._list_migration_files() == []

    def test_handles_three_and_four_digit_numbers(self, tmp_path, monkeypatch):
        # Migrations may grow past 999 someday. Ensure NN+1 digits still work
        # and sort correctly.
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "0999_last.sql").write_text("--")
        (migrations / "1000_overflow.sql").write_text("--")
        (migrations / "100_hundred.sql").write_text("--")

        monkeypatch.setattr(runner, "MIGRATIONS_DIR", migrations)

        files = runner._list_migration_files()
        names = [f.name for f in files]
        assert names == ["100_hundred.sql", "0999_last.sql", "1000_overflow.sql"]


class TestParseBootstrapExclude:
    def test_empty_returns_default(self):
        result = runner._parse_bootstrap_exclude(None)
        assert result == set(runner.DEFAULT_BOOTSTRAP_EXCLUDE)

    def test_whitespace_only_returns_default(self):
        result = runner._parse_bootstrap_exclude("   ")
        assert result == set(runner.DEFAULT_BOOTSTRAP_EXCLUDE)

    def test_single_filename(self):
        assert runner._parse_bootstrap_exclude("020_foo.sql") == {"020_foo.sql"}

    def test_comma_separated(self):
        result = runner._parse_bootstrap_exclude("019_a.sql,020_b.sql,021_c.sql")
        assert result == {"019_a.sql", "020_b.sql", "021_c.sql"}

    def test_strips_whitespace_around_items(self):
        result = runner._parse_bootstrap_exclude(" 019_a.sql , 020_b.sql ")
        assert result == {"019_a.sql", "020_b.sql"}

    def test_ignores_empty_items(self):
        result = runner._parse_bootstrap_exclude("019_a.sql,,020_b.sql,")
        assert result == {"019_a.sql", "020_b.sql"}


class TestApplyOne:
    """Test _apply_one against a mocked asyncpg connection."""

    @pytest.mark.asyncio
    async def test_transactional_migration_wraps_in_transaction(self, tmp_path):
        migration = tmp_path / "001_test.sql"
        migration.write_text("CREATE TABLE foo (id INT);")

        conn = MagicMock()
        # Mock the transaction() context manager.
        txn = AsyncMock()
        conn.transaction = MagicMock(return_value=txn)
        conn.execute = AsyncMock()

        await runner._apply_one(conn, migration)

        # The transaction context manager should have been entered.
        txn.__aenter__.assert_called()
        # Body + INSERT both ran.
        assert conn.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_non_transactional_migration_skips_outer_transaction(self, tmp_path):
        migration = tmp_path / "020_concurrent.sql"
        migration.write_text(
            "-- migration:notransaction\nCREATE INDEX CONCURRENTLY foo ON bar(x);"
        )

        conn = MagicMock()
        txn = AsyncMock()
        conn.transaction = MagicMock(return_value=txn)
        conn.execute = AsyncMock()

        await runner._apply_one(conn, migration)

        # transaction() is still called for the tracking INSERT + duration UPDATE,
        # but the migration body should be executed directly.
        # We check execute was called with the body SQL.
        body_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args and "CREATE INDEX CONCURRENTLY" in str(c.args[0])
        ]
        assert len(body_calls) == 1


class TestBootstrap:
    @pytest.mark.asyncio
    async def test_marks_non_excluded_migrations(self, tmp_path):
        migrations = [
            tmp_path / "001_a.sql",
            tmp_path / "002_b.sql",
            tmp_path / "019_c.sql",
        ]
        for m in migrations:
            m.write_text("--")

        conn = MagicMock()
        conn.executemany = AsyncMock()

        await runner._bootstrap(conn, migrations, exclude={"019_c.sql"})

        conn.executemany.assert_awaited_once()
        assert conn.executemany.called
        assert conn.executemany.call_count == 1
        # Verify the records being inserted exclude 019.
        call_args = conn.executemany.call_args
        records = call_args.args[1]
        names = [r[0] for r in records]
        assert names == ["001_a.sql", "002_b.sql"]

    @pytest.mark.asyncio
    async def test_no_migrations_to_mark(self, tmp_path):
        # All migrations are excluded — nothing to do.
        migration = tmp_path / "001_a.sql"
        migration.write_text("--")

        conn = MagicMock()
        conn.executemany = AsyncMock()

        await runner._bootstrap(conn, [migration], exclude={"001_a.sql"})

        # executemany should not be called when there's nothing to mark.
        conn.executemany.assert_not_awaited()
        assert not conn.executemany.called
        assert conn.executemany.call_count == 0


class TestRunFlow:
    """Integration-style tests of _run with everything below asyncpg mocked.

    We're testing the control flow: lock acquisition, table creation,
    bootstrap path vs normal path, pending detection, error handling.
    """

    @pytest.mark.asyncio
    async def test_no_pending_migrations_returns_zero(self, tmp_path, monkeypatch):
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "001_existing.sql").write_text("--")
        monkeypatch.setattr(runner, "MIGRATIONS_DIR", migrations)

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(return_value=[{"filename": "001_existing.sql"}])
        conn.close = AsyncMock()

        with patch("apply_migrations.asyncpg.connect", AsyncMock(return_value=conn)):
            result = await runner._run(
                "postgresql://x", bootstrap=False, bootstrap_exclude=set()
            )

        assert result == 0
        # Lock acquired and released.
        execute_sqls = [str(c.args[0]) for c in conn.execute.await_args_list]
        assert any("pg_advisory_lock" in s for s in execute_sqls)
        assert any("pg_advisory_unlock" in s for s in execute_sqls)

    @pytest.mark.asyncio
    async def test_lock_released_even_on_error(self, tmp_path, monkeypatch):
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        # Migration that will fail (mocked to raise).
        migration = migrations / "001_bad.sql"
        migration.write_text("THIS WILL FAIL")
        monkeypatch.setattr(runner, "MIGRATIONS_DIR", migrations)

        conn = MagicMock()
        conn.close = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        # Track all execute calls; raise on the migration body, succeed on
        # everything else (lock, tracking table, unlock).
        async def execute_side_effect(sql, *args, **kwargs):
            if "THIS WILL FAIL" in sql:
                raise RuntimeError("boom")

        conn.execute = AsyncMock(side_effect=execute_side_effect)

        txn = AsyncMock()
        txn.__aenter__ = AsyncMock(side_effect=lambda: None)
        txn.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=txn)

        with patch("apply_migrations.asyncpg.connect", AsyncMock(return_value=conn)):
            with pytest.raises(RuntimeError, match="boom"):
                await runner._run(
                    "postgresql://x", bootstrap=False, bootstrap_exclude=set()
                )

        # Verify pg_advisory_unlock was still called.
        execute_sqls = [str(c.args[0]) for c in conn.execute.await_args_list]
        assert any("pg_advisory_unlock" in s for s in execute_sqls)
