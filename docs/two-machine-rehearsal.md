# Two-machine red/blue rehearsal

This rehearsal is restricted to the owned VPS lab at `147.182.179.38` and its
synthetic flags matching `[A-Z0-9]{31}=`. Do not point these tools at another
system.

## Topology

```text
attacker laptop ---> OnlyChess :4000 ---> traffic feed ---> defender laptop
       |                    ^                                      |
       +---- flags ----> simulator :8088                 verified patch
                            |                                      |
                            +---- PUT / GET checker <---------------+
```

The VPS behaves like the organizer. Every 120 seconds it checks old flags,
stores a new flag, and publishes only the current game ID. The attacker and
defender therefore race on the same service instance.

## One-time preparation

Both operators need the Arena directory. The defender also needs `uv`, Go,
Codex, and whichever optional Claude/OpenCode providers are available:

```bash
cd /path/to/sas-agent-arena
uv sync
uv run sas-arena --config config/arena.onlychess-remote.toml doctor
```

The defender creates a dedicated SSH key and sends only the `.pub` file and
their public IP to the lab administrator:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/sas-arena-defender
curl -4 https://icanhazip.com
```

The administrator then runs:

```bash
uv run python lab/onlychess/rehearsal_admin.py onboard \
  --source-ip TEAMMATE_PUBLIC_IP \
  --public-key-file TEAMMATE_KEY.pub
```

The defender exports these values before launching Arena:

```bash
export SAS_ARENA_SSH_USER=arena-defender
export SAS_ARENA_SSH_KEY="$HOME/.ssh/sas-arena-defender"
```

The restricted account can only verify, deploy, and roll back lab candidates.
It cannot reset the lab or obtain the simulator's private flag state.

## Start a clean match

The administrator resets the service database, flags, submissions, score, and
active patch. This is intentionally destructive only to the rehearsal lab:

```bash
uv run python lab/onlychess/rehearsal_admin.py reset --confirm RESET-LAB
```

Wait for the displayed state to report tick 1 and service status `UP`.

## Attacker laptop

The attacker should not receive source, checker credentials, or defender SSH
credentials. The small client exposes only public competition functions:

```bash
python3 lab/onlychess/red_client.py watch
python3 lab/onlychess/red_client.py attack-data
python3 lab/onlychess/red_client.py submit 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
```

Attack the challenge at `http://147.182.179.38:4000`. Previous flags remain
valid for five ticks, matching the SAS 2026 ten-minute lifetime. The client uses
the ForcAD-shaped attack-data and `PUT /flags` interfaces.

## Defender laptop

Start the complete blue pipeline:

```bash
uv run python lab/onlychess/rehearsal_defender.py
```

This performs preflight checks and starts four independent processes:

- deterministic availability/checker/integrity monitoring;
- traffic collection and Claude traffic-analysis/reconstruction tasks;
- scoreboard observation;
- Claude, DeepSeek, and Codex task workers.

The terminal opens a read-only TUI. Process logs are in
`.arena/rehearsal-logs/`. Open another terminal to inspect or talk to agents:

```bash
uv run sas-arena --config config/arena.onlychess-remote.toml agents
uv run sas-arena --config config/arena.onlychess-remote.toml agent-watch TASK_ID --follow
uv run sas-arena --config config/arena.onlychess-remote.toml \
  agent-message TASK_ID 'Prioritize reconstructing the unauthorized chat flow.' \
  --provider claude
```

To deploy a candidate manually, use the gated defense command. It first builds
an isolated canary, runs the checker, and requires the known authorization
attack to be blocked. A failed post-deployment gate triggers rollback:

```bash
uv run sas-arena --config config/arena.onlychess-remote.toml \
  defense-deploy events/sas2025-onlychess-remote/event.toml \
  onlychess /absolute/path/to/candidate --revision rehearsal-001
```

## Pass criteria

Run for at least ten ticks. The rehearsal passes when:

- the attacker steals and submits at least one flag before patching;
- the defender identifies the attack from traffic without being told the bug;
- a candidate passes canary checker and exploit-block gates;
- deployment preserves `UP` status and historical GET checks;
- the same attack no longer recovers a flag;
- rollback is demonstrated once with no flag loss.
