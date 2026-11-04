# Grouped Video Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task.

**Goal:** Four generation model entries with 480P / 720P chosen in video settings.
**Architecture:** Existing canonical 480P IDs represent families in public catalogs. Preparation resolves resolution and freezes the actual EID before quotation and submission. Raw catalogs remain available to historical jobs.
**Tech Stack:** Python, unittest, encrypted BeefTV JSON configuration, existing Wails UI.
**Spec:** ../specs/2026-11-01-grouped-video-models-design.md

## Global Constraints
- No paid tests or new live generation jobs.
- Preserve verbatim prompts, prior jobs, 5-second opt-out execution, and enhancement behavior.
- Modify the existing bridge and encrypted configuration, not BeefTV binaries or saved canvas payloads.

## Review Focus
- Every selected 720P option must use the 720P EID for both quote and submission.
- Unsupported resolutions must fail before upstream writes.
- Existing fixed-EID jobs remain queryable and submit with their original EID.
- Re-running model sync must not restore the other 18 advertised entries.
- Current 480P defaults and completed canvas nodes remain usable.

## Task 1: Family routing and profiles
- [x] Add failing tests for four profiles, dual resolution settings, all eight normal routes and two extension routes, rejection, and legacy job behavior.
- [x] Add grouped catalog/resolver helpers in model_catalog.py and extension.py; resolve before job persistence in bridge.py / extension.py.
- [x] Update install_channel.py, install_extension.py and server.py advertised catalogs and capability versions.
- [x] Run focused tests, then the full suite.

## Task 2: Install and verify
- [x] Update sync_models.py to use grouped public models and map retained historical defaults to the corresponding visible family.
- [x] Back up and safely update encrypted model configuration while BeefTV is closed; preserve unrelated fields.
- [x] Restart bridge and BeefTV; verify four names plus resolution buttons in the native UI without generation.
- [x] Independent review, regression checks, evidence and documentation.

No Git repository is present; use dated ZIP/config backups instead of commits.
