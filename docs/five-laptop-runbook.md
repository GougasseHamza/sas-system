# Five-laptop match runbook

This runbook assumes five laptops, one independently authenticated Codex,
Claude Code, and OpenCode/DeepSeek installation per laptop, and exactly two
assigned services per laptop. It does not assume SAS 2026 challenge names or
network endpoints.

## Before service release

On every laptop:

```bash
cd /Users/blinkyy/sas-agent-arena
uv sync
uv run sas-arena --config config/arena.local.toml doctor
```

Use the same repository revision, event directory, cluster manifest, hub token,
and configured event scope on all five laptops. Each laptop keeps its own model
credentials and SQLite task database.

## Service release and assignment

Bootstrap each unpacked service on the coordinator copy:

```bash
uv run sas-arena service-bootstrap events/live/event.toml unpacked/SERVICE \
  --id SERVICE_ID --name SERVICE_NAME
```

Generate and review the assignments:

```bash
uv run sas-arena cluster-plan events/live/event.toml \
  --nodes laptop-1,laptop-2,laptop-3,laptop-4,laptop-5 \
  --services-per-node 2 > config/cluster.live.toml
uv run sas-arena cluster-validate events/live/event.toml config/cluster.live.toml
```

Copy the updated event directory and cluster manifest to the other laptops.
With five services, the ten assignment slots give every service two owners.

## Start coordination

On the coordinator laptop:

```bash
export SAS_ARENA_HUB_TOKEN='RANDOM_MATCH_SECRET'
uv run sas-arena --config config/arena.local.toml hub-serve \
  events/live/event.toml config/cluster.live.toml --port 8765
```

On laptop N:

```bash
export SAS_ARENA_HUB_TOKEN='RANDOM_MATCH_SECRET'
uv run sas-arena --config config/arena.local.toml cells \
  events/live/event.toml --cluster config/cluster.live.toml \
  --node laptop-N --hub http://COORDINATOR_IP:8765
```

On the coordinator laptop in another terminal:

```bash
uv run sas-arena --config config/arena.local.toml tui \
  events/live/event.toml --cluster config/cluster.live.toml --node laptop-1
```

## Grace period

Press `g` once. The default profile, per service, is:

- ten controller workers;
- five concurrent Codex task streams queued;
- up to ten Claude/DeepSeek child branches per Codex task;
- health/checker preparation enabled;
- opponent traffic, exploit farming, and flag submission disabled.

The generated checker, exploit, patch, deployment, and rollback gates start
fail-closed. Service owners must replace them with real commands before results
can be accepted or deployed.

Grace reaching `00:00:00` does not start live play. Wait for the organizer's
network-open signal.

## Live game

Press `l` when the network opens. The default profile, per service, becomes:

- five controller workers;
- one persistent Claude service operator; Codex is reserved for escalations;
- up to four child branches per Codex task;
- traffic analysis, verified exploit farming, monitoring, and submission enabled.

Press `f` when the scoreboard freezes. Work and ticks continue. Press `e` only
at the official competition end.

## Recovery

Create a consistent database and artifact snapshot on each laptop:

```bash
uv run sas-arena --config config/arena.local.toml backup \
  backups/laptop-N-prelive.tar.gz
```

The hub shows heartbeat age for every laptop. A lost laptop does not stop the
other four; its duplicate service owner continues with independently persisted
evidence and receives peer claims again when connectivity returns.
