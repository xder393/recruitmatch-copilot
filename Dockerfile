FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim

WORKDIR /app

# 先装依赖，利用 Docker 层缓存。当前基础镜像保留所有组，供 Compose 中的检查任务使用。
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --all-groups

COPY app/ app/
COPY web/ web/
COPY scripts/ scripts/
COPY alembic/ alembic/
COPY alembic.ini .
COPY tests/ tests/

ENV PATH="/app/.venv/bin:$PATH"

# 模型缓存卷：避免每次重建容器都重新下载 embedding 模型
ENV HF_HOME=/root/.cache/huggingface
VOLUME ["/root/.cache/huggingface"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
