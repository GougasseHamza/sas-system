---
name: arena-offense
description: Discover, build, verify, and farm exploits against explicitly authorized Attack-Defense CTF services using synthetic flags and deterministic replay gates. Use for Arena offense tasks, not unrelated Internet targets.
---

# Arena offense

Keep the target inside the event descriptor and allowed CIDRs.

1. Start from a bounded hypothesis with a concrete source, protocol, traffic, or state-transition lead.
2. Prove the primitive locally with a synthetic flag. Preserve the request, script, seed, and required state.
3. Verify on a fresh instance and repeat enough to distinguish reliability from chance. A model may not approve its own exploit from prose.
4. Register an `attack` hook only after replay succeeds. It must accept `{target}` and print candidate flags to stdout without choosing out-of-scope targets itself.
5. Let the deterministic offense controller validate, deduplicate, and submit flags. Never embed submission credentials in an agent prompt or exploit artifact.

For difficult branches, the captain may dispatch independent read-only tests to fast workers and continue its own analysis. Share evidence packets, not hidden reasoning or full transcripts.

For Codex app-server tasks, use `sas_control.traffic_evidence`, `recent_evidence`, `service_state`, and `exploit_registry` for service-scoped live facts. Use `sas_dispatch` to run independent Claude/DeepSeek branches. Neither tool replaces fresh-state exploit replay.
