# ADR-020: Cloud runtime profile for portfolio serving

## Date

2026-07-07

## Status

Accepted

## Plain English

Keep local development local-first, but serve the portfolio demo with managed cloud services where the laptop-only pieces do not fit: Bedrock for embeddings, Anthropic for generation, Qdrant Cloud for vectors, and MiniLM as the CPU-safe reranker.

## Context

The local architecture still depends on Ollama for embeddings and generation, local Qdrant for vectors, and optional Qwen3 reranking for eval-quality retrieval. That works on the developer machine, but it does not map cleanly to a small AWS demo:

- Ollama would require running a model server in the deployment and would lose Apple Silicon GPU acceleration inside containers.
- Qwen3 reranking is not viable on CPU-only Docker/Fargate. It OOMs small containers or takes minutes per query. ADR-016 records the eval-vs-serving split.
- Local Qdrant is stateful infrastructure that should not live inside an ephemeral Fargate task.
- The demo needs to deploy and tear down cheaply, with secrets outside the image and no NAT Gateway.

The code now has the seams for this profile: `embedding_backend=bedrock`, `llm_model=claude-*`, Qdrant API keys, `RAGLAB_ENV_FILE=.env.cloud-gate`, and CDK task definitions.

## Decision

Use a distinct cloud runtime profile for serving:

- Embeddings: AWS Bedrock Titan Text Embeddings v2 via `embedding_backend=bedrock`.
- Generation: first-party Anthropic API via the existing `generate()` route for `claude*` models.
- Vectors: Qdrant Cloud with authenticated `qdrant_url` and `qdrant_api_key`.
- Sparse/metadata stores: prebuilt SQLite and BM25 artifacts baked into the image as seed artifacts.
- Runtime: one Docker image with separate FastAPI and Streamlit entrypoints on ECS Fargate, fronted by an ALB to the UI only; API traffic stays internal through Service Connect.
- Reranker: cloud serving pins `reranker_backend=minilm`; the managed-reranker option landed as ADR-021 (Bedrock Rerank is the host/eval default, quota-bound out of serving; Qwen3 retired to a research arm).
- Optional local-model side paths are disabled in the cloud gate: query rewriting, answerability gate, faithfulness self-check, and query decomposition.
- Trace persistence is disabled for cloud serving by default because local JSONL traces can include legal questions and chunk previews.

## Alternatives Considered

1. Run Ollama in Fargate. Rejected because it adds model-server operations and CPU-only latency, and it does not meet the zero-Ollama gate.
2. Use Bedrock Claude for generation. Rejected for now because first-party Anthropic was already implemented and evaluated; Bedrock Claude would add IAM/model-access work with no quality benefit to the immediate deployment.
3. Run Qdrant inside ECS. Rejected because task storage is ephemeral and Qdrant Cloud already gives a managed vector endpoint for the demo.
4. Serve Qwen3 on CPU. Rejected by Docker testing: memory and latency are not acceptable for interactive serving.
5. Split API and UI into separate images. Deferred. One image with two entrypoints is simpler while the codebase is still a modular monolith.

## Reasons

- The cloud profile proves the app can run without local-only services while keeping the local development path cheap.
- Managed embeddings and managed generation remove the two hardest runtime dependencies from the demo.
- Qdrant Cloud keeps vector state outside the throwaway Fargate tasks.
- Service Connect plus an ALB that fronts only the UI keeps the API off the public internet without paying for a second load balancer.
- Pinning MiniLM in serving is an explicit, honest trade-off rather than letting a fresh deploy inherit the eval-only Qwen3 default and fail.

## Consequences

- Local and cloud are now two runtime profiles, not one identical stack. Config and eval labels must say which profile produced a result.
- Eval-quality Qwen3 retrieval numbers do not automatically describe served MiniLM answers. ADR-016 remains the source for that gap.
- Changing `embedding_backend` or embedding dimension requires a fresh Qdrant collection and reindex; existing collection dimensions are validated and fail loud.
- The cloud image depends on seed artifacts being present when `REQUIRE_SEED=true`.
- Secrets live in Secrets Manager or local `.env.cloud-gate`, never baked into the image.
- Future work should close reranker parity with a managed reranker backend, a GPU endpoint, quantized Qwen3, or a MiniLM-matched eval baseline.
