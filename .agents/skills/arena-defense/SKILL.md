---
name: arena-defense
description: Diagnose incidents and build, verify, canary, deploy, or roll back patches for authorized Attack-Defense CTF services while preserving checker behavior and availability.
---

# Arena defense

Treat service availability as a correctness property, not a secondary metric.

1. Reproduce the exploit or incident against an isolated copy and record the exact primitive.
2. Make the smallest patch that breaks that primitive. Avoid unrelated refactoring during the match.
3. Configure `verify_patch` to prove all three gates: the exploit is blocked, the checker passes, and health/availability remains good.
4. Use the controller's `deploy` hook for a canary. Post-deploy `health` and `checker` hooks must pass or the controller invokes `rollback`.
5. When monitoring fires, correlate the alert with recent deployments and traffic. Prefer a proven rollback over speculative live edits.

For Codex app-server tasks, use the service-scoped `sas_control` MCP tools to read current state and request a patch deployment, rollback, or monitor check. A request is only queued; wait for controller evidence before describing it as executed. Use `sas_dispatch` for bounded Claude/DeepSeek investigations while continuing the main incident analysis.

Do not weaken checker validation, suppress a failing monitor, or modify a historical fixture to make a candidate appear healthy.
