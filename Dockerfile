# LeadGraph API (M8). Build: docker build -t leadgraph-api .
# Run:   docker run -p 8000:8000 -v %cd%/outputs:/app/outputs leadgraph-api
# Keys are sent per run in the request body (BYOK); none are baked into the image.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONUTF8=1

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra api --no-install-project
COPY src ./src
COPY api ./api
COPY config ./config
RUN uv sync --frozen --no-dev --extra api

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
