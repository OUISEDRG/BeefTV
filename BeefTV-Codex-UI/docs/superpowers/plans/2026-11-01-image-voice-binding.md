# Canvas picture voice binding Implementation Plan

> **For agentic workers:** Implement independent scoped tasks with shared contracts and final integration review.

**Goal:** Bind Dreama voices directly on pictures and consume the selected voice in video and independent audio generation.
**Architecture:** Native dialog → node metadata/resource store → structured task provider options → existing local bridge → verified Dreama character/audio contracts.
**Tech Stack:** React/TypeScript, existing Go media/plugin pipeline, Python bridge, Wails Windows.
**Spec:** ../specs/2026-11-01-image-voice-binding-design.md

## Global Constraints

- No inference/paid generation tests, no prompt rewrite, no automatic project creation.
- Preserve previous Codex/native UI work and production data.
- Validate upstream field and reference contracts from official evidence.

## Review Focus

- Picture ordering changes must not swap voices.
- Two speaking pictures must not silently select one voice for a single-speaker audio task.
- Local samples cannot be represented only by a stale preview URL.
- Removing a binding and restoring/copying nodes must persist correctly.
- Save/readback failure and unknown submission cannot become an automatic paid retry.

## Tasks

- [x] UI/metadata: shared typed binding, native image-toolbar dialog, catalog/preview and resource selection, clear binding, focused tests.
- [x] Generation: exact input slot binding in providerOptions, independent audio voice mapping, plugin body mapping, prompt-preservation tests.
- [x] Bridge: authenticated voice catalog route, character asset/voice handling, generation readback verification, standalone audio mapping, offline tests.
- [x] Integrate: review contracts, update plugin/config capabilities without broad model changes; relevant tests/typecheck/build.
- [x] Deploy: app was closed and idle; backed up DB/config/executable/bridge; installed plugin, migrated only video protocol fields, deployed bridge and final executable, relaunched original native canvas.
- [ ] Final native dialog click/save visual verification: blocked by CUA `coordinate input geometry is unavailable`, minimized-state errors and screenshot timeout. New executable and original canvas were observed; no assertion that the dialog was clicked successfully.

## Verification receipt

- Final executable SHA256: `9801bce74af8da52d607d40885fc0a3048546bbe96a70d1947ba8b25dfbf6590`.
- Plugin 1.1.0 is enabled, verified by official registered-client `business_get_plugins`. Upload returned an error after successful commit because the pre-existing native schema lacks `admin_audit_events`; reconciled registry/package hash instead of repeating the operation or editing its database.
- Config revision 49; 4 video models and 1 extension model preserved. Codex remains 8 models, both defaults gpt-6-astra; reasoning selection high observed in native UI.
- Live read-only catalog: 136 voices (83 public, 53 personal), including personal voice 575709.
- Full bridge suite: 152 passing. Subsequent MIME alias change: 15 binding tests passing. Preview/UI/API: 9 passing; typecheck, native build and diff whitespace check passing. Earlier targeted generation, Go handler/catalog/resource guards and actual plugin-manifest mapping tests passed.
- Dreama ledger remains 7 jobs; BeefTV remains 8 tasks. No inference or paid generation submitted.
- Deployment evidence: `D:\BeefTV-Codex-UI-tools\voice-deployment.json`.
- Backup: `D:\BeefTV-Codex-UI-tools\backups\voice-binding-20261101-120839`.
