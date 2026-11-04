# Native local-model reasoning selector

The user requests reasoning strength directly inside BeefTV, without opening a separate web settings page. The already working local Codex bridge provides eight native models, per-model saved effort, and full text/tool/canvas roundtrips. Preserve those behaviors and the user's data.

## Behavior

- Show a compact reasoning picker next to the assistant model selector and the regular text prompt panel model picker. Reuse the same component for a separate script-node model picker.
- The picker reads actual model-supported efforts and the saved override from the local bridge. It offers model default plus only supported levels, with Chinese labels.
- The same model shares one preference across assistant and text surfaces. Selection saves immediately, synchronizes through a shared query cache, and affects only the next new inference turn.
- Changing models or receiving a late settings response must not apply a preference to a different model. A revision conflict refreshes the current value and reports the conflict, without automatically overwriting it.
- Keep generation inactive during verification. UI settings changes are local and never invoke Codex turn/start or paid media APIs.

## Architecture

React uses the existing `http` API client with GET/PUT `/workspace/model-config/reasoning`. It sends channel IDs and model IDs, never upstream URLs or tokens. A narrow backend domain service resolves the enabled saved text channel and obtains its credential from encrypted workspace configuration. It accepts literal loopback upstreams only, rejects redirects, and calls the bridge's authenticated `/v1/reasoning-settings` endpoint. Responses expose model capabilities, overrides, and integer revision only.

The bridge adds authenticated GET/PUT reasoning settings routes, preserving the existing settings store and turn pinning. Browser settings session credentials do not grant this API access; the registered channel Bearer credential is required. Existing public settings-page endpoints remain compatible.

## Packaging and validation

Build from official v2.1.1 commit `60d22756918127106190d48587b9735c8156dbcf` using the repository's pinned Windows toolchain conventions. Use a local version suffix when supported. Replace only the desktop executable after successful builds and tests; retain current agent-host, CLI, plugin packages and data directory. Back up the old executable and changed bridge code, and verify startup plus persisted model/preferences and canvas data.

Verify domain proxy boundaries and CAS behavior with local fake servers, React behavior with offline fixtures, bridge API authentication and persistence with fake inference, and native Windows build/startup. No real model inference or paid generation is authorized as an automatic test.
