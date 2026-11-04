# Local Codex integration contract

Verified against the installed `Codex Desktop/0.166.0-alpha.5.1` app-server on 2026-11-01. Generated protocol schemas are in `protocol-schema/`.

## Authentication and test boundary

`codex login status` reports `Logged in using ChatGPT`. Both an ordinary child app-server and the isolated `RpcSession` child report `account.type=chatgpt`, `planType=prolite`. The child uses normal Codex account storage; Python never reads or copies tokens/auth files. The process retains the normal `CODEX_HOME` location and strips inherited desktop chat/tool-pipe environment values.

`initialize`, `account/read(refreshToken=false)`, and `model/list` were sent to the real app-server. A subsequently authorized zero-inference `thread/start` accepted the dynamic tool descriptor, empty base instructions, BeefTV developer instructions, and gpt-6-astra; the thread was ephemeral with zero turns and was immediately closed. No real `turn/start` was sent. Model catalog availability does not prove an inference request will succeed or be free. Actual inference remains a user action through BeefTV.

## Python API

```python
rpc = RpcSession(executable, isolated_working_directory)
proof = rpc.probe()  # account and model metadata only
thread_id = rpc.start_thread(model, tools, base_instructions, developer_instructions)
turn_id = rpc.start_turn(input_items, effort="high")
event = rpc.next_event(timeout=30)  # queue.Empty on timeout
rpc.reply_tool(server_jsonrpc_id, [{"type":"inputText","text":"result"}], True)
rpc.cancel()
rpc.close()
```

Construction only starts the transport and performs initialize. Starting a turn is an explicit separate call. `tools` accepts flat native dynamicTools or OpenAI function descriptors. No other tool executor is supplied by the bridge.

## Dynamic tool lifecycle

The app-server sends a JSON-RPC request:

```json
{"id":72,"method":"item/tool/call","params":{"threadId":"thread-id","turnId":"turn-id","callId":"call-id","tool":"create_text_node","arguments":{"text":"example"}}}
```

The HTTP adapter presents `params.callId` as the OpenAI tool call ID. It keeps this RPC session and pending server request alive until BeefTV returns the corresponding tool result. It then replies to JSON-RPC `id=72`:

```json
{"id":72,"result":{"contentItems":[{"type":"inputText","text":"node created"}],"success":true}}
```

Codex continues the existing turn. Do not initiate another turn for a pending tool result. `inputImage`/`imageUrl` and `inputAudio`/`audioUrl` content item variants are also defined in this version. The transport rejects unknown dynamic tool names, duplicate results, and native approval/server requests.

## History and streaming

`turn/start.input` contains typed text/image inputs, not OpenAI role messages. The exposed `thread/resume.history` field is explicitly marked internal/unstable for Codex Cloud and is not used. Existing OpenAI history must be represented as clearly bounded role-labelled history data on first request; subsequent tool results use the native request/reply lifecycle. A persistent HTTP conversation can reuse a thread for later user turns. Main streaming events are `item/agentMessage/delta`, `item/completed`, and `turn/completed`. `start_turn` requests `summary=auto`. Progress summaries arrive as `item/reasoning/summaryTextDelta` with `params.delta`, `threadId`, `turnId`, `itemId`, and `summaryIndex`. Summary-part events are `item/reasoning/summaryPartAdded`. Use these summary events for progress; do not expose the separate raw `item/reasoning/textDelta` stream.

### BeefTV Durable context tail (1.2.1)

The installed host appends a transient user-role message after every request's transcript, including after tool results. Its serialized form contains the exact `<beeftv_plan>` / `<beeftv_studio>` sections and, when needed, the existing restored-skill block. The bridge recognizes only this final, complete format, removes it from conversation identity comparisons, and supplies it as task data beside the original user input or the matching native tool reply. The actual system/developer instructions, user messages, model, and tool set still have to match. Tool-result image carriers are resolved after removing this tail.

Before 1.2.1, the tail hid each trailing tool result from continuation detection. Every tool round started another native session without returning its predecessor's result; eight pending sessions exhausted the local limit. The fix keeps a single native turn through repeated tools. Pending calls are never evicted to make capacity, and missing/expired call IDs never cause a new inference request. Validation/admission failures do not reserve an idempotency key indefinitely.

`tests/test_studio_continuation.py` covers twelve sequential calls, changing plans, restored skills, tool images, distinct concurrent conversations, strict user/system binding, stale IDs, cached retries, conflicting results, capacity and admission. These tests use fake native transport and do not prove live model completion or media generation quality.

## Tool isolation

Do not rely only on `features.code_mode=false` or `features.multi_agent=false`. The installed catalog uses `tool_mode=code_mode_only`, `multi_agent_version=v2`, and `apply_patch_tool_type=freeform` for the chosen models. The bridge derives a catalog from this executable's bundled metadata, preserving model identity and instructions but changing only:

- `tool_mode=direct`
- `shell_type=disabled`
- `apply_patch_tool_type=null`
- `experimental_supported_tools=[]`
- `multi_agent_version=disabled`

The derived catalog is set at process startup using `model_catalog_json`; per-thread catalog overrides do not reapply it. Process overrides disable shell, image viewing/generation, web search, plugins/apps, known MCP servers, hooks, skills, memories, native agents, and native environment access. `project_doc_max_bytes=0` suppresses repository AGENTS discovery, but the installed runtime still reads the user's global `CODEX_HOME/AGENTS.md`. The real thread/start response lists this single global source. Current native authentication home is retained; no global instruction files or credentials are changed. Threads are ephemeral, read-only, with `environments=[]` and no approval escalation. A future Codex update that changes metadata/config contracts requires retesting.

The isolation flags and metadata were accepted by the actual installed app-server. Both metadata probes and the zero-inference thread-start probe passed; the latter returned `sandbox={type:readOnly,networkAccess:false}` and `approvalPolicy=never`. Since no real turn was initiated, the actual generated tool surface has not been observed on a live model request. Fake-transport tests prove the bridge's own tool allowlist/result handling without incurring inference usage.

## Evidence and source

- `codex-readonly-proof.json`: ordinary native auth/model probe.
- `codex-isolated-readonly-proof.json`: isolated bridge auth/model probe.
- `codex-thread-readonly-proof.json`: actual ephemeral thread/start acceptance, zero turns, immediately closed.
- `tests/test_codex_rpc.py`: nine fake-transport tests, all pass.
- Installed executable's generated JSON schemas are the protocol authority for this machine.
- OpenAI source reviewed for tool selection: https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/spec_plan.rs
- OpenAI source reviewed for model-selected Code Mode: https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/mod.rs
- OpenAI source reviewed for model-selected multi-agent overrides: https://github.com/openai/codex/blob/main/codex-rs/core/src/config/mod.rs
