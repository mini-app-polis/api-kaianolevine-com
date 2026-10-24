# The API's image. Railway builds it from here (railway.json selects it), so
# the base image, interpreter patch and system packages are recorded in the
# repo instead of inferred by the builder at build time (CD-021).
#
# Base images are pinned by digest (CD-022) — the same python and uv digests
# as db-ops. To move to a newer patch, look up the tag's current digest and
# replace it here, in a commit of its own.
#
# The start command is the image's CMD, and only here: .railway/railway.ts,
# which declares the rest of the service's settings, deliberately does not
# repeat it (CD-017 accepts either, not both). It is wrapped in /bin/sh -c
# for the `&&` and $PORT.

FROM python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

# Dependencies first, so a code-only change reuses this layer. The version
# file is copied early because hatchling reads the version from it.
COPY pyproject.toml uv.lock ./
COPY src/kaianolevine_api/_version.py src/kaianolevine_api/_version.py
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
COPY migrations ./migrations
RUN uv sync --locked --no-dev

# CD-023: the final stage runs unprivileged. Writes nothing to disk outside /tmp.
RUN useradd --system --uid 10001 --no-create-home --home-dir /app kaianolevine-api
USER kaianolevine-api

CMD ["/bin/sh", "-c", "python scripts/apply_migrations.py && exec uvicorn src.kaianolevine_api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
