# Phase 9 Normalized Response Correction

> This document corrects the provider response boundary after checking the reference Hermes repository.

## Reference

- `D:\python-develop\project\hermes-agent\agent\transports\base.py`
- `D:\python-develop\project\hermes-agent\agent\transports\types.py`
- `D:\python-develop\project\hermes-agent\agent\transports\chat_completions.py`

## Correction

Do not introduce a custom `ProviderResponse(message, model, usage)` type.

Hermes uses transport-level normalization:

```text
provider raw response
  -> transport.normalize_response()
  -> NormalizedResponse
  -> conversation loop builds assistant message
```

The learning implementation should use a smaller Hermes-style type set:

- `ToolCall`
- `Usage`
- `NormalizedResponse`

The current `AIAgent` may still convert `NormalizedResponse` into the existing OpenAI-style `ChatMessage` before appending it to history. Session storage does not change in this batch.

## Implementation Direction

1. Add `src/learn_hermes_agent/providers/types.py`.
2. Change `ProviderTransport.complete()` to return `NormalizedResponse`.
3. Change fake and OpenAI-compatible providers to return `NormalizedResponse`.
4. Change `AIAgent` to convert normalized responses into current assistant `ChatMessage` objects.
5. Keep CLI output, session storage, and tool execution behavior unchanged.

## Out of Scope

- Full Hermes transport interface with `convert_messages`, `convert_tools`, and `build_kwargs`.
- Provider fallback chain.
- Streaming.
- Anthropic / Bedrock / Codex Responses.
- Persisting usage to SQLite.
