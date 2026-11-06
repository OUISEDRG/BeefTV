# BeefTV 2.1.1 local Codex integration contract

Verified against the installed `D:\BeefTv\agent-host` and official v2.1.1 source. All validation used read-only configuration or local fake model events; no remote inference or real canvas mutation was run.

## Configuration

`textModel` and `assistantModel` select `codex-local::gpt-6-astra`. Both should be set explicitly because a stale nonempty assistantModel does not fall back to textModel.

The enabled user channel has `apiFormat: openai`, `interfaceType: chat-completion`, nonempty local bridge `apiKey`, `models: [gpt-6-astra]`, and a matching model profile with `capability: text`, `protocol: chat-completion`. Add the encoded model key to `models` and `textModels`. There is no separate assistantModels list or tools-enabled profile flag. The official installed `openai-chat-completions.beeftv-plugin` provides text and agent scope.

Channel base URL is `http://127.0.0.1:18766/v1`. Native host mapping adds `/v1` only when absent, then the SDK adds `/chat/completions`. Both default text and assistant therefore reach `/v1/chat/completions`.

References:

- [Native selection and protocol mapping](https://github.com/glanderness/BeefTV/blob/v2.1.1/backend/internal/modelcatalog/assistant.go)
- [Frontend assistant model menu](https://github.com/glanderness/BeefTV/blob/v2.1.1/web/src/lib/assistant-model.ts)
- [Host API/base URL mapping](https://github.com/glanderness/BeefTV/blob/v2.1.1/backend/internal/assistantruntime/env.go)

## Wire contract

Native assistant uses installed `@earendil-works/pi-ai@1.3.0`, API `openai-completions`. Requests contain `model`, `messages`, `stream: true`, usually `stream_options: {include_usage: true}` and `store: false`; accept both max_tokens and max_completion_tokens. With tools:

```json
{"tools":[{"type":"function","function":{"name":"canvas_get","description":"Read canvas","parameters":{"type":"object","properties":{"canvasId":{"type":"string"}}}}}]}
```

Message roles are system, user, assistant, tool. Content can be strings or text/image_url blocks. Tool result messages use `role: tool`, `tool_call_id`, and string `content`. Preserve native call IDs across requests.

Minimal tool response SSE (each data line ends with two newlines):

```text
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1,"model":"gpt-6-astra","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1,"model":"gpt-6-astra","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_unique","type":"function","function":{"name":"canvas_get","arguments":"{\"canvasId\":\"example\"}"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1,"model":"gpt-6-astra","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
```

Native BeefTV executes the returned tool, and sends the next HTTP request with its complete conversation plus tool result. The bridge must resume the same pending Codex dynamic tool request, not start a new turn. Multiple queued Codex calls may be exposed one at a time if the remaining queue stays alive for subsequent native continuations.

Text SSE uses `delta.content`, followed by `finish_reason: stop` and `[DONE]`. Nonstream text returns an ordinary chat.completion object with `choices[0].message: {role: assistant, content: string}`, `finish_reason: stop`, and optional usage. SDK requires an explicit finish_reason before end of stream; absence is a failure, not success.

## Runtime constraints

BeefTV's own operation bridge injects real canvas scope, expectedRevision handling, operation IDs and turn authorization. Keep tool execution in this native bridge. The model endpoint only receives declared function schemas and returns function calls.

`D:\BeefTv\agent-host\model-stream-idle.mjs` imposes a 95-second meaningful-progress timeout. `: ping` alone does not reset it. Real text, reasoning-summary, tool argument progress or a terminal finish_reason does. Do not fabricate progress or tool success to keep the connection open.

Model configuration updates have no frontend hydration event. Perform installation with BeefTV normally closed to prevent a stale frontend from overwriting the changed settings. `install_codex.py` checks closure twice, preserves the entire unrelated configuration/document metadata, backs up encrypted bytes, and atomically replaces the encrypted document only if its original bytes still match.

## Evidence

- `tests/test_install_codex.py`: 9 passing unit tests for preservation, idempotence, collision rejection, encryption, safe install, backup, running-app refusal, concurrency refusal, and redacted read-only verification.
- `tests/test_sdk_http_contract.py` + `tests/sdk_http_client.mjs`: real installed SDK to loopback HTTP to real CompletionBridge to fake Codex RPC. Two queued tool calls, two native continuations, final assistant text, streaming text, nonstream text. Five HTTP requests, three fake RPC sessions, zero remote inference and zero real canvas writes.
- `test_beeftv_sdk_contract.mjs`: exact installed SDK parses streamed partial function arguments and replays native tool results.
- `verify_codex_readonly.py`: registered BeefTV client's read-only config query succeeded against current workspace without printing credentials. Full post-deployment verification also checks local bridge `/health` without submitting generation.
