# Minimal project workflow implementation plan

> For agentic workers: use the existing isolated worktree; implement scoped tasks with behavioral tests and one final independent review.

**Goal:** Prepare one real episode of 一胎多宝 from its local script, SRT and assets into a correctly bound BeefTV canvas, with reusable folder operations and non-billable generation validation.

**Architecture:** Codex uses the existing workflow skill and BeefTV CLI. Canvas documents own prompts, references and bindings; the existing Dreama bridge owns remote execution facts. No new service, database, scheduler or chat synchronization.

**Tech Stack:** Existing Python workflow tools, Go operation API, shared TypeScript canvas compiler, bundled Node runtime.

**Spec:** User-approved design in this conversation: folder → Codex → existing CLI → canvas → existing bridge. Only source materials belong to the folder; exports do not automatically overwrite canvas edits.

## Global constraints

- Do not run paid generation or real model inference for development validation.
- Use formal APIs for canvas/resource writes; never write the production SQLite directly.
- Preserve historical canvas bindings, tasks, source files and user edits.
- Only edit public workflow sources under D:/工作流开发/短剧工作流/production; preserve its existing dirty changes and project locks.
- Reuse the existing isolated application worktree and bundled runtime; no PATH fallback for installed compiler.
- Keep current Dreama submission guard until an equivalent frozen-snapshot, authorization and budget contract is implemented and tested.

## Review focus

- Stable IDs and repeated execution must not duplicate assets, frames, nodes or tasks.
- Concurrent/manual edits cause a revision conflict rather than being overwritten.
- Preview cannot create tasks, upload remote references or start the bridge countdown.
- Resource ownership, final reference order, voices and episode binding remain consistent.
- Source/creative rule conflicts must be resolved explicitly, never silently rewrite SRT or dialogue.

## Task 1: Formal non-billable prepare operation

Files: backend/internal/canvasgeneration/*; operations contract/dispatch; minimal domain adapter; web/src/lib/canvas/dreama-canvas-compiler.ts; scripts/package-agent-host.mjs; relevant docs/tests.

Interface: canvas.generation.prepare({canvasId,nodeIds,expectedRevision?,expectedModelConfigRevision?}) returns versions, snapshotHash, per-node preview, generationSubmitted:false and truthful submission availability. Reads canvas and config through existing ownership checks; compiles using shared TS functions via bundled Node, validates every referenced resource. No caller-supplied executable path or trusted snapshot.

- [ ] Add failing tests for permissions/revisions/resources and zero side effects.
- [ ] Implement shared compiler and bounded subprocess adapter; reject unsupported inputs explicitly.
- [ ] Verify repeated/ordered references, voices, grouping and parameter checks; package compiler with desktop.

## Task 2: Folder-to-canvas adapter in existing workflow

Files: public production/tools/workflow_beeftv.py, skill.workflow and manifest; behavior tests in existing maintenance tests.

Interfaces: read_project_binding, read_episode, stable_id, plan_canvas; one document commit with CAS through registered existing CLI client. Project binding contains only stable local project key and canvas location. Preparation reads real script/SRT/prompt files; existing source asset node IDs are supplied explicitly.

- [ ] Test safe source paths, invalid SRT, stable IDs and preservation of manual changes.
- [ ] Implement minimal reader/planner/CLI adapter; forbid owner/desktop credentials in normal client operations.
- [ ] Test repeat no-op and revision conflicts; integrate formal prepare response.

## Task 3: One real episode and delivery

- [ ] Re-read correct canvas and remote episode binding; choose complete episode 2 inputs.
- [ ] Resolve specific rule conflicts; read real references; prepare complete prompts without changing source material.
- [ ] Back up current canvas; import/reuse resources and voice facts; commit episode group, references and generation nodes via API.
- [ ] Run installed formal preview for every segment; reread all persisted node content and references.
- [ ] Confirm no generation jobs were created; verify source data preserved and repeat planning is no-op.
- [ ] Review changes, build/install updated dedicated package, publish only scoped public workflow changes through official deployment, deliver concise usage instructions.

## Execution record

Initial state: application c9537df3; bridge be737c9; Codex bridge 1.2.1. Correct project canvas Z_xnhUAsdENDge_XRUn1X; historical KYTY9Xc45ISpbdOjg5uJ7 must not be rebound.

Public workflow repository has extensive pre-existing dirty changes; do not commit or reset them. This implementation is bounded by the user's explicit no-fee instruction. Actual generated video quality is not an acceptance claim in this run.
