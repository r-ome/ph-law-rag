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
# Cloud image bakes the seed artifacts; fail fast if they're missing. Local dev
# builds (compose mounts ./data and syncs after startup) leave REQUIRE_SEED=false
# so a fresh clone can build before any sync has run.
ARG REQUIRE_SEED=false
RUN if [ "$REQUIRE_SEED" = "true" ]; then \
      test -f data/sqlite/ph-law-rag.db && test -f data/bm25/params.index.json \
      || (echo "ERROR: seed artifacts missing — run raglab sync/reindex before build" && exit 1); \
    fi
RUN uv sync --frozen --no-dev

# Optionally pre-bake the configured reranker for frozen images. Local dev
# compose mounts the host Hugging Face cache instead, so rebuilds don't block
# on model downloads.
ARG PRELOAD_RERANKER=false
# Serving runs MiniLM (bedrock is quota-capped at 2 calls/min, qwen3 needs a GPU —
# see ADR-021 / ADR-016).
ARG RERANKER_BACKEND=minilm
ARG RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ARG QWEN3_RERANKER_MODEL=Qwen/Qwen3-Reranker-0.6B
RUN if [ "$PRELOAD_RERANKER" != "true" ]; then \
      echo "Skipping reranker preload"; \
    elif [ "$RERANKER_BACKEND" = "bedrock" ]; then \
      echo "bedrock reranker is a remote API — nothing to preload"; \
    elif [ "$RERANKER_BACKEND" = "minilm" ]; then \
      RERANKER_MODEL="$RERANKER_MODEL" .venv/bin/python -c "import os; from sentence_transformers import CrossEncoder; CrossEncoder(os.environ['RERANKER_MODEL'])"; \
    elif [ "$RERANKER_BACKEND" = "qwen3" ]; then \
      QWEN3_RERANKER_MODEL="$QWEN3_RERANKER_MODEL" .venv/bin/python -c "import os; from transformers import AutoModelForCausalLM, AutoTokenizer; model = os.environ['QWEN3_RERANKER_MODEL']; AutoTokenizer.from_pretrained(model, padding_side='left'); AutoModelForCausalLM.from_pretrained(model)"; \
    else \
      echo "unsupported RERANKER_BACKEND=$RERANKER_BACKEND" && exit 1; \
    fi

ENV PATH="/app/.venv/bin:$PATH"
