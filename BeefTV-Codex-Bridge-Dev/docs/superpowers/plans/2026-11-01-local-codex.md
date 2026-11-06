# Local Codex Implementation Plan

**Goal:** Connect BeefTV default text and assistant to authenticated local Codex, including native canvas tools.
**Architecture:** Python loopback Chat Completions adapter over ephemeral Codex app-server; BeefTV executes tools.
**Tech Stack:** Python standard library, local Codex JSON-RPC, existing BeefTV official OpenAI protocol.
**Spec:** ../../../DESIGN.md

## Constraints
- No real turn/start/inference or paid generation during installation tests.
- Never copy auth tokens or modify global Codex settings.
- Preserve other BeefTV/Dreama channels and exact prompt text.
- Unknown/expired tool chains fail explicitly; never replay automatically.

## Work
- [ ] codex_rpc.py and fake transport tests: initialize, isolated thread config, RPC routing, pending tools, cancellation, account/model probes.
- [ ] completion_bridge.py and fake engine tests: text/images, roles/history, live tool roundtrip, model/context/tool binding, duplicate results, errors and expiry.
- [ ] server.py and HTTP tests: authenticated loopback GET health/models and POST completions, JSON/SSE compatibility, request limits.
- [ ] install_codex.py and migration tests: encrypted backup/atomic writes, process guard, channel/default update, read-only CLI verification.
- [ ] Independent review, deploy hidden service + login startup, close/reopen BeefTV, verify config/native catalog without generation.

## Review focus
- Parallel or repeated tool calls and mismatched tool output must not execute twice.
- Restart/timeout/disconnect must not silently create a fresh inference.
- Existing image inputs and history must retain exact text and role boundaries.
- Native Codex environment must not inherit extra tools or workspace instructions.
- BeefTV stale config must not overwrite the migration.
