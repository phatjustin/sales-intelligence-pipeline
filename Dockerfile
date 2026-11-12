FROM python:3.14.0-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/
WORKDIR /app
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra dev --no-install-project
COPY src ./src
RUN uv sync --frozen --extra dev
COPY tests ./tests
COPY scripts ./scripts
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["sales-pipeline"]
