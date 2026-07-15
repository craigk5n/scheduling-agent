# Scheduling agent image (uv-based, Python 3.12). Runs the web UI by default.
FROM python:3.12-slim

# uv for fast, locked installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies first (cached unless the lock changes), then the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY . .
RUN uv sync --frozen --no-dev

# Non-root runtime user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
# Run the installed console script directly (no runtime re-sync).
# Web UI (POST /schedule, /approve). For the CLI: `docker run ... scheduling-agent`.
CMD ["scheduling-agent-web"]
