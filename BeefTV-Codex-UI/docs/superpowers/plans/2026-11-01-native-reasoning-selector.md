# Native reasoning selector implementation plan

**Goal:** Adjust local Codex effort inside BeefTV text and assistant interfaces.
**Architecture:** Shared React control → existing authenticated workspace backend → saved local channel → bridge preference store.
**Tech stack:** React/TypeScript, Gin/Go, Python loopback bridge, Wails Windows desktop.
**Spec:** ../specs/2026-11-01-native-reasoning-selector.md

- [x] Locate exact v2.1.1 UI, backend boundaries and build dependencies.
- [x] Add bridge Bearer GET/PUT settings API and offline authentication/CAS tests.
- [x] Implement backend domain port, handlers and safe loopback proxy; verify with fake local servers.
- [x] Implement shared reasoning picker and mount beside assistant/text/script model pickers; verify dynamic options, pending state and conflicts.
- [x] Run relevant tests and type checks, build embedded frontend and Windows desktop executable.
- [x] Independently inspect final diff; back up and deploy executable plus bridge changes while idle.
- [x] Verify native startup, saved selections/data and visible controls without inference; document local build and rollback.

Deployment/verification record: `D:\BeefTV-Codex-UI-tools\NATIVE_UI_VERIFICATION.md`.
