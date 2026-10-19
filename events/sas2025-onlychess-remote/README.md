# OnlyChess remote A/D rehearsal

This event points Arena at the isolated SAS 2025 OnlyChess lab on
`147.182.179.38`. The service and simulator ports are restricted by the VPS
firewall to the operator's source IP.

Endpoints:

- challenge: `http://147.182.179.38:4000/`
- tick/checker state: `http://147.182.179.38:8088/api/state`
- ForcAD-shaped attack data: `http://147.182.179.38:8088/api/client/attack_data`
- synthetic flag receiver: `PUT http://147.182.179.38:8088/flags` with
  `X-Team-Token: rehearsal-team-token`

The simulator advances every 120 seconds, performs CHECK, PUT, and one
historical GET in the SAS 2026 order, retains flags for five rounds, and
publishes only the current game ID using the official attack-data shape.
Plaintext flags and checker credentials remain in a root-only VPS state file.

Validate without invoking a model:

```bash
uv run sas-arena --config config/arena.onlychess-remote.toml \
  event-validate events/sas2025-onlychess-remote/event.toml
uv run sas-arena --config config/arena.onlychess-remote.toml \
  monitor-once events/sas2025-onlychess-remote/event.toml
```

Start an agent rehearsal:

```bash
uv run sas-arena --config config/arena.onlychess-remote.toml \
  phase-grace events/sas2025-onlychess-remote/event.toml --duration 1800
uv run sas-arena --config config/arena.onlychess-remote.toml \
  cells events/sas2025-onlychess-remote/event.toml
```

In another terminal:

```bash
uv run sas-arena --config config/arena.onlychess-remote.toml \
  tui events/sas2025-onlychess-remote/event.toml
```

Press `l` in the TUI when you want verified exploits to farm flags and submit
them to the simulator.

Exploit candidates must provide `exploit.py`, `run`, or `exploit`. The program
receives the target IP as its first argument and must print recovered flags
matching `[A-Z0-9]{31}=`. The verifier also supplies `SAS_SERVICE_URL` and
`SAS_ATTACK_DATA_URL`.

Patch verification cross-compiles the candidate, runs it as an isolated canary
on the VPS, executes the flag round-trip, and requires the known chat
authorization probe to be blocked. Deployment updates only the `sas-onlychess`
container. Rollback restores the previously recorded lab image.

The traffic hook converts recent Gin access logs for `/chat`, `/chat-key`, and
`/private-chat` into bounded detections. This is sufficient to exercise the
Claude analyst → Claude reconstruction → Codex reverse-exploit pipeline; it is
not a replacement for full PCAP capture.

VPS services:

```text
sas-onlychess-lab.service
sas-ad-simulator.service
```

Their files and private state live under `/opt/sas-ad-lab`.

For the complete attacker-laptop versus defender-laptop procedure, restricted
SSH onboarding, clean reset, agent interaction, and pass criteria, see
[`docs/two-machine-rehearsal.md`](../../docs/two-machine-rehearsal.md).
