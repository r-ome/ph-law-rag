# ADR-004: Using local open weight models through Ollama

## Date

2026-06-11

## Status

Accepted

## Plain English

Use local Ollama models by default so the project can be developed and demonstrated without paying for cloud model calls. Cloud models may be stronger later, but local-first keeps the baseline cheap, repeatable, and portfolio-friendly.

## Context

LLMs are required for this project for embedding and generating. For development purposes, Ollama offers a service that runs an open weight model. Compared to cloud-provided models, local models are free but weaker and slower from a reasoning perspective but cheaper (free). Although cloud-provided models offers a stronger and faster model, this project is currently on development phase which doesn't require stronger and faster models.

## Decision

Use local models using Ollama.

## Alternatives Considered

1. Anthropic/OpenAI models offers a strong and faster model. Rejected because spending isn't really an option for development phase.

## Reasons

- Spending for token consumption at this phase in the project isn't really an option, although local models are weaker, it can do its job well for development phase.
- Config base architecture is implemented so model APIs are just plug-and-play.

## Consequences

- Time consuming when evaluating answers. Local models usually takes longer to generate answer.
- Local models are weaker and might not reflect eval scores with production quality; swapping to cloud later means re-running evals to re-baseline.
- Dev needs Ollama running locally.
