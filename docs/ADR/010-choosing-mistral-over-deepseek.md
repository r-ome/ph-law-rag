# ADR-010: Choosing Mistral over DeepSeek for generation

## Date

2026-06-13

## Status

Accepted

## Plain English

Use Mistral as the default local answer model because it is faster and more reliable for this project’s current RAG workflow. DeepSeek and Qwen remain useful experiments, but they should not be the baseline demo model right now.

## Context

The project uses Ollama for local LLM generation. `llm_model` is configurable, and the current codebase defaults to `mistral`, with `deepseek-r1:8b` and `qwen3:4b` kept as commented alternatives in `app/config.py`.

Manual runs and eval runs showed that DeepSeek answers much slower than Mistral for this RAG workflow. DeepSeek usually takes around 22s-120s per answer, while Mistral usually answers around 3s-30s. DeepSeek also sometimes hallucinates, while Mistral is more reliable for the current answer relevancy and context relevancy metrics.

## Decision

Use `mistral` as the default generator model for local development and demos.

## Alternatives Considered

1. `deepseek-r1:8b`. Rejected as the default because it is slower in local runs and less reliable for the current RAG answer quality.
2. `qwen3:4b`. Rejected as the default because it is very fast, but hallucinates more often in observed answers.
3. Cloud models from Anthropic/OpenAI. Rejected for default local development because ADR-004 chooses local models through Ollama to avoid token cost.

## Reasons

- Mistral has better latency for the current local-first workflow.
- Mistral is more stable against hallucinations in observed RAG answers.
- Mistral performs better for the current answer relevancy and context relevancy eval signals.
- The model remains swappable through config, so DeepSeek and Qwen can still be tested later without changing the pipeline.

## Consequences

- Local demos and eval runs are faster and easier to repeat.
- Results are still tied to the chosen local model, so changing to DeepSeek or a cloud model requires re-running evals.
- DeepSeek and Qwen remain useful as experiments, but not as the baseline generator.
