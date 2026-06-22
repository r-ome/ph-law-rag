FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_HTTP_TIMEOUT=300 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_XET=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
 RUN test -f data/sqlite/ph-law-rag.db && test -f data/bm25/params.index.json \
      || (echo "ERROR: seed artifacts missing — run raglab sync/reindex before build" && exit 1)
RUN uv sync --frozen --no-dev

# Pre-bake the reranker cross-encoder so it never downloads at runtime
RUN .venv/bin/python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

ENV PATH="/app/.venv/bin:$PATH"