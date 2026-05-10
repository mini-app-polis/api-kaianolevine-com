"""Eval-harness conftest.

Module-level pytestmark in test_harness.py handles the skip-on-missing-keys
guard. This conftest exists for future shared fixtures and to anchor the
package as a pytest collection root.

Run locally with:
    doppler run -- pytest tests/evals/

Note: parent tests/conftest.py defines an autouse `reset_db` that wipes a
SQLite in-memory engine — it does NOT touch the real DATABASE_URL. The eval
harness uses get_sessionmaker(settings.DATABASE_URL) directly to write to
production, bypassing that SQLite engine entirely.
"""
