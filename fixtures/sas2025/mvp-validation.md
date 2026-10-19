# Historical SAS 2025 MVP validation — OnlyChess

This report is retained as regression evidence only. It is not the architecture
or service configuration for SAS 2026.

Date: 2026-10-12

Scope: local replay of the official SAS CTF 2025 OnlyChess service using only
synthetic accounts and flags.

## Shadow-dispatch run

- Fast pre-probe queued: 0.002 seconds after the Codex task started.
- Codex in-turn branch dispatched: 21.694 seconds.
- First Claude evidence packet accepted: 32.284 seconds.
- Second Claude evidence packet accepted: 74.373 seconds.
- Codex fetched both worker packets: 143.124 seconds.
- Codex result accepted: 179.760 seconds.

The run confirms that fast workers can produce useful evidence while the same
Codex turn continues, and that Codex can fetch those results before finalizing.

## Patch run

Codex dispatched three bounded Claude investigations while implementing an
isolated candidate patch. Claude results were accepted at 42.176, 80.506, and
144.363 seconds. Codex read all three packets before finalizing.

The deterministic patch gate then:

1. built a fresh Docker image and database;
2. stored and recovered a synthetic flag through the intended encrypted chat;
3. verified that a nonparticipant received no private ciphertext/IV history;
4. verified that a nonparticipant could not submit a private message;
5. preserved spectator public chat and server-bound author identity.

The patch result was accepted at 401.165 seconds. The final verification step
reported a successful fresh stack, successful synthetic flag round-trip, and
exploit-probe return code 10 (both authorization paths blocked).

## Interpretation

The measured shape is the desired one: useful parallel evidence appeared within
32–74 seconds even though the deep Codex result took about three minutes. Patch
verification took longer because the Codex implementation turn remained deep,
but the scheduler and verification path did not block its parallel workers.

Future patch/exploit tasks now seed a writable fast-model candidate immediately,
with the same deterministic verification commands as the Codex candidate. This
lets a verified Claude/DeepSeek implementation become usable while Codex keeps
reviewing or developing a stronger alternative.
