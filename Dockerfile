FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project


FROM builder AS test-builder

RUN uv sync --frozen --all-groups --no-install-project


FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim AS app-base

RUN rm -f /usr/local/bin/uv /usr/local/bin/uvx \
    && groupadd --gid 10001 recruitmatch \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin recruitmatch \
    && mkdir -p /app/data /home/recruitmatch/.cache/huggingface \
    && chown -R recruitmatch:recruitmatch /app /home/recruitmatch

WORKDIR /app

COPY --from=builder --chown=recruitmatch:recruitmatch /app/.venv /app/.venv
COPY --chown=recruitmatch:recruitmatch app/ app/
COPY --chown=recruitmatch:recruitmatch web/ web/
COPY --chown=recruitmatch:recruitmatch alembic/ alembic/
COPY --chown=recruitmatch:recruitmatch alembic.ini ./
COPY --chown=recruitmatch:recruitmatch scripts/bootstrap.py scripts/seed_job_templates.py scripts/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/recruitmatch/.cache/huggingface

VOLUME ["/home/recruitmatch/.cache/huggingface"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=2)"


FROM app-base AS test

COPY --from=test-builder --chown=recruitmatch:recruitmatch /app/.venv /app/.venv
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --chown=recruitmatch:recruitmatch pyproject.toml uv.lock .python-version Dockerfile docker-compose.yml ./
COPY --chown=recruitmatch:recruitmatch scripts/ scripts/
COPY --chown=recruitmatch:recruitmatch tests/ tests/
COPY --chown=recruitmatch:recruitmatch evaluation/ evaluation/

USER recruitmatch

CMD ["pytest", "-q"]


FROM app-base AS runtime

USER recruitmatch

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
