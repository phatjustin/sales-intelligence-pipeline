FROM python:3.14.0-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/
WORKDIR /backend
COPY pyproject.toml uv.lock README.md ./
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
RUN uv sync --frozen --all-groups --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --all-groups
ENV PATH="/backend/.venv/bin:$PATH"
CMD ["uvicorn", "sales_intelligence.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
