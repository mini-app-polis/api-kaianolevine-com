"""Eval-harness conftest.

Module-level pytestmark in test_harness.py handles the skip-on-missing-keys
guard. This conftest exists for future shared fixtures and to anchor the
package as a pytest collection root.

Run locally with:
    RUN_EVALS=1 doppler run -- pytest tests/evals/

Note: tests/integration/conftest.py (which resets a local *_test database)
does not apply here. The eval harness uses
get_sessionmaker(settings.DATABASE_URL) directly, so it writes to whatever
database the environment names — the development database under the repo's
Doppler `dev` config. It runs only when RUN_EVALS=1 is set, and refuses to
collect at all when the process resolves as production (test_harness.py,
CD-033).
"""
