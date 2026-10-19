# SAS Agent Arena

SAS Agent Arena is a generic, latency-aware control plane for an authorized
Attack-Defense CTF. It is being built for the upcoming SAS 2026 event; it does
not contain knowledge of any assumed 2026 challenge.

The central design is:

> Deterministic programs run the match. Fast models create throughput. Codex
> captains handle difficult exploitation, patching, and ambiguous failures. The
> environment—not a model's confidence—approves results.

The current MVP implements service-isolated cells, multi-provider routing,
Codex-to-fast-worker dispatch, bounded evidence context, health/checker/integrity
monitoring, incident creation, grace/live phase profiles, five-laptop service
assignments and phase synchronization, sharded exploit farming, a central
expiry-aware FlagBroker, exploit and patch promotion, rollback, persistent
captain sessions, a terminal dashboard, and a bounded traffic replay pipeline.

Historical SAS 2025 material is retained only under `vendor/`, `lab/`, and
`fixtures/sas2025/` as regression data. None of the Python control plane imports
or names a 2025 service.

## Current status

Status date: **2026-10-19**

This repository is a **functional phase-aware, five-laptop orchestration MVP**.
It is not yet competition-ready and has not completed a full multi-hour
Attack-Defense rehearsal with live Codex, Claude, and DeepSeek turns.

The distinction is important:

- **Built:** Python implementation exists and is exercised by automated tests.
- **Integrated:** the component has been connected to its real local runtime.
- **Rehearsed:** it has survived an end-to-end competition-like run.

| Area | Status | What is true today |
| --- | --- | --- |
| Generic event/service model | Built | No SAS 2025 challenge is hardcoded into the control plane. |
| Unknown-service bootstrap | Built, draft quality | `service-bootstrap` detects compose/checker/language hints, creates a descriptor, updates the event, and queues mapper tasks. Security proof hooks intentionally fail until configured. |
| Per-service agent cells | Built | Each service has isolated queues, context, sessions, monitors, workspaces, and offense loops. |
| Grace/live phase control | Built | Grace uses 10 workers, one Claude operator plus four Codex deep specialists, and up to ten child branches per service by default; live keeps one Claude operator, reserves Codex for escalations, and enables offense and submission. If Claude is unavailable on a laptop, its operator task falls back to Codex. |
| Five-laptop assignment | Built | A cluster manifest enforces five nodes and exactly two distinct services per laptop; round-robin planning supports duplicated coverage. |
| Multi-laptop synchronization | Built, local-work model | An authenticated hub distributes phase/tick state and assignments, receives heartbeats, and shares bounded claims/events between duplicate service owners. Each laptop retains its own task database and subscriptions. |
| Task queue and evidence store | Built | SQLite provides leasing, deduplication, claims, events, deployments, flags, and session IDs. |
| Codex SDK/app-server | Integrated | Persistent process, ChatGPT-auth verification, thread start/resume, skill input, structured output, cancellation, and MCP startup are working. |
| Claude worker | Integrated, not rehearsed | Claude Code runs as a full coding agent with its normal read/write/shell/subagent tools, streaming activity, and session resume. A live Sonnet 5.5 smoke reached the harness but safety-refused the cyber-context turn, which is handled as provider failure. |
| DeepSeek worker | Integrated, not rehearsed | OpenCode runs DeepSeek as a full coding agent with read/edit/shell tools and persistent sessions. A live smoke used both file and shell tools and returned structured evidence. |
| Codex-to-fast-worker dispatch | Built | Codex can enqueue bounded Claude/DeepSeek branches through a task-scoped MCP server and continue its turn. |
| Agent-facing service control | Built | Claude operators and SDK Codex specialists receive service-scoped `sas_control` and `sas_dispatch` MCP servers. Defense calls create operator actions; they cannot bypass deterministic gates. |
| Context, prompts, and skills | Built | Bounded accepted evidence, operational state, freshness, role prompts, and untrusted-data separation are implemented. |
| Exploit and patch verification | Built | Service-supplied deterministic hooks are mandatory before accepting implementation tasks. |
| Monitoring and incidents | Built | Health/checker/integrity hooks, thresholds, incident tasks, recovery, and rollback triggers exist. |
| Patch deployment | Built, operator-triggered | Accepted patches enter a verified queue; the Claude operator can request promotion, and human execution runs verify, deploy, post-health/checker, and rollback. |
| Exploit farm and flag broker | Built | Target checks, sharding, timeouts, extraction, expiry, fair leasing, deduplication, retry, and submission exist. |
| Tick/scoreboard observation | Partial | The rulebook confirms ForcAD at `10.60.20.2`, 120-second rounds, `PUT /flags`, `X-Team-Token`, official flag syntax, and attack-data shape. The live tick/scoreboard JSON response paths remain unknown until VPN access opens. |
| Operator visibility | Built (CLI/TUI) | The terminal dashboard shows phase countdown, tick, phase concurrency, two-service assignment, task/monitor/flag state, and laptop heartbeats. `agent-watch` streams safe public activity. |
| Operator steering | Built with transport limits | `agent-message` reaches a live Codex turn immediately and is injected into the next resumed Claude/OpenCode turn. Their CLIs do not expose the same mid-turn steering channel. |
| Metrics | Partial | Prometheus export covers tasks, flags, monitors, providers, phase/tick, exploits, patches, nodes, latency, and event state; there is no alert manager. |
| Traffic intelligence | Partial | Suricata EVE and generic JSONL adapters provide cursoring, checker filtering, clustering, and bounded detection tasks. Tulip uses the generic hook contract; full PCAP indexing/search is not included. |
| Exploit registry/promotion | Built | Verified artifacts are copied, versioned, activated, and executed by the farm; prior active revisions are retired. |
| Multi-host control plane | Partial | Authenticated phase/assignment synchronization and node heartbeats exist. Tasks and artifacts stay local to each laptop, matching the two-service ownership model. |
| Multiple independent Codex subscriptions | Built for laptop isolation | Each laptop uses its own Codex login and local app-server. Named additional instances remain supported on a laptop. |
| Two-machine red/blue lab | Integrated, ready for onboarding | An owned VPS runs one vulnerable service with SAS 2026-shaped 120-second CHECK/PUT/GET rounds, five-round flags, ForcAD submission and attack data, traffic detection, canary patch verification, deployment, and rollback. Red and blue launch tools are built; the teammate IP and public key are the only remaining access inputs. |
| Full competition rehearsal | Missing | The system has not yet run an eight-hour, multi-service replay under realistic ticks, failures, and provider limits. |

Current automated verification:

- the full unit/integration suite passes;
- the real Codex app-server starts successfully;
- the current ChatGPT subscription login is detected successfully;
- a real ephemeral Codex thread and required `sas_dispatch` MCP server start
  successfully without invoking model inference;
- CLI doctor, event validation, deterministic demo, and metrics smoke tests pass.
- live OpenCode/DeepSeek read, write, and shell-tool execution succeeds with
  structured output and a persistent session ID;
- live Claude Code initialization exposes its full default tool inventory; a
  provider safety refusal is correctly rejected rather than treated as success;
- live Codex SDK notifications stream into the public activity path (the smoke
  turn itself exceeded its deliberately short 120-second deadline, confirming
  why Codex must stay off the fast path).

The tests prove the control-plane contracts. They do **not** yet prove live
competition throughput, exploit quality, patch quality, or eight-hour stability.

The single-service attacker-laptop versus defender-laptop rehearsal is described
in [docs/two-machine-rehearsal.md](docs/two-machine-rehearsal.md). It includes a
clean lab reset, a restricted defender deployment account, public red-team
controls, the blue monitoring/traffic/agent launcher, and objective pass criteria.
Confirmed organizer rules and their engineering implications are recorded in
[docs/sas-2026-rulebook-notes.md](docs/sas-2026-rulebook-notes.md).

## Architecture at a glance

```text
                         event control plane
          tick / scoreboard / scope / quotas / global strategy
                                  |
                      durable task and evidence bus
                                  |
       +--------------------------+--------------------------+
       |                          |                          |
 service cell A             service cell B             service cell N
 monitor/traffic/offense    monitor/traffic/offense    monitor/traffic/offense
 context + sessions         context + sessions         context + sessions
       |                          |                          |
       +----------- provider router and verification -------+
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
 persistent Codex         Claude fast workers     DeepSeek fast workers
 captain/app-server       full Claude Code        full OpenCode agent
          |                       ^                       ^
          +---- task-scoped MCP dispatch / evidence -----+
                                  |
             exploit gate / patch gate / checker gate
                                  |
              exploit farm / deployment / rollback
                                  |
                       central FlagBroker
```

Each laptop runs its two service cells and local SQLite database. The hub shares
only phase/tick state, assignments, and bounded health summaries, so losing one
laptop does not block the other four.

## Grace period, five laptops, and the TUI

Generate the assignment file after the live services are bootstrapped:

```bash
uv run sas-arena cluster-plan path/to/event.toml \
  --nodes laptop-1,laptop-2,laptop-3,laptop-4,laptop-5 \
  --services-per-node 2 > config/cluster.live.toml
uv run sas-arena cluster-validate path/to/event.toml config/cluster.live.toml
```

Start the authenticated phase hub on the coordinator laptop:

```bash
export SAS_ARENA_HUB_TOKEN='replace-with-a-random-match-secret'
uv run sas-arena hub-serve path/to/event.toml config/cluster.live.toml \
  --bind 0.0.0.0 --port 8765
```

On each laptop, use its own node ID and local model subscriptions:

```bash
uv run sas-arena --config config/arena.local.toml cells path/to/event.toml \
  --cluster config/cluster.live.toml --node laptop-1 \
  --hub http://COORDINATOR_IP:8765
```

Run the terminal dashboard in a second terminal on the coordinator laptop:

```bash
uv run sas-arena --config config/arena.local.toml tui path/to/event.toml \
  --cluster config/cluster.live.toml --node laptop-1
```

Dashboard keys are `g` for grace, `l` for live, `f` for scoreboard freeze,
`e` for end, and `q` to exit. Grace expiry never opens the network or enables
offense automatically; the operator presses `l` only when the organizers open
the game. Defaults are configured under `[phases.grace]` and `[phases.live]`.

Bootstrap each newly released service during grace:

```bash
uv run sas-arena service-bootstrap path/to/event.toml path/to/unpacked/service \
  --id service-a --name 'Service A'
```

When organizers publish their API, copy
`config/event-http-adapter.example.toml`, fill the URLs/JSON paths, and point the
event `observe` and `submit` hooks at:

```bash
uv run sas-arena event-http-observe config/event-http-adapter.live.toml
uv run sas-arena event-http-submit config/event-http-adapter.live.toml '{flag}'
```

Create a consistent local recovery bundle during rehearsals and before live
play:

```bash
uv run sas-arena backup backups/laptop-1-prelive.tar.gz
```

## Why one service cell per challenge

Every competition service gets an independent logical agentic system:

```text
                         global match control
             scope / ticks / flags / quotas / scoreboard
                                  |
             +--------------------+--------------------+
             |                    |                    |
       service cell A       service cell B       service cell C
       - own queues         - own queues         - own queues
       - own context        - own context         - own context
       - own captains       - own captains       - own captains
       - own monitors       - own monitors        - own monitors
       - own workspaces     - own workspaces      - own workspaces
       - own exploit runs   - own exploit runs    - own exploit runs
             |                    |                    |
             +---------- central FlagBroker -----------+
```

This keeps a web service's traffic, patch history, and model sessions separate
from a pwn or crypto service. The shared layer still owns the things that must
be globally consistent: flag deduplication/submission, provider concurrency,
scope, and match-wide state.

Run one cycle across all cells:

```bash
uv run sas-arena --config config/arena.example.toml \
  cells-once events/template/event.toml
```

Run the cells continuously:

```bash
uv run sas-arena --config config/arena.local.toml \
  cells path/to/sas-2026/event.toml
```

Each service descriptor selects its worker count, provider instances, and
whether its already-verified exploit hook runs every tick:

```toml
[cell]
workers = 6
provider_instances = ["codex-a", "claude-a", "deepseek-a"]
offense_enabled = true
```

## What runs without an LLM

The critical match loop must never wait for a reasoning model. Ordinary code
handles:

- service health, checker, traffic, and integrity hooks;
- alert thresholds, recovery detection, and incident creation;
- task leasing, deduplication, deadlines, and bounded fan-out;
- isolated candidate workspaces;
- exploit replay and patch proof gates;
- canary deployment and rollback;
- known-exploit execution against declared targets;
- flag regex validation, deduplication, and submission;
- provider concurrency and session persistence.

Models are called only for intellectual work: mapping, hypothesis generation,
new exploit construction, patch construction, and diagnosis.

## Model coordination

### Codex

Codex is the persistent senior specialist. It is started immediately for
critical/high-value work and hedged for difficult normal work. A captain can
dispatch bounded tasks through the task-scoped `sas_dispatch` MCP server while
continuing its own investigation.

The default transport is the official `openai-codex` Python SDK, not a shell
wrapper. The SDK starts one long-lived local Codex app-server process per named
provider instance and reuses the existing ChatGPT/Codex login. The runtime path
is:

```text
SQLite task
  -> ContextBuilder (bounded accepted evidence + current event state)
  -> PromptComposer (charter + narrow role + untrusted-data boundary)
  -> CodexSdkProvider
       -> thread_start or thread_resume(service/role/provider/revision)
       -> relevant SkillInput
       -> task-scoped sas_dispatch MCP server
       -> service-scoped sas_control MCP server
       -> structured output schema
  -> deterministic exploit/patch/checker/availability verifier
  -> accepted claims and artifacts
```

`CodexSdkProvider` keeps the app-server connection warm across tasks, interrupts
an in-flight turn when its Arena deadline is cancelled or expires, and closes
the server cleanly when the controller exits. `transport = "cli"` remains an
explicit fallback, but it is not the default architecture.

`auth_mode = "chatgpt"` is the safe default. Before the first turn, the provider
asks app-server for its account state and fails closed unless the active account
is a ChatGPT login. It also removes API-key and workload-identity variables from
that provider process, preventing an unrelated shell variable from silently
moving competition traffic onto API billing. `auth_mode = "any"` is available
only for an intentional API-backed deployment.

The dispatch is explicit. Fast workers do not read private chain-of-thought.
They receive a concrete goal and evidence packet such as:

```json
{
  "kind": "vuln_scout",
  "goal": "Test whether object lookup occurs before membership validation",
  "evidence": [{"path": "handlers/object.go", "line": 91}],
  "priority": 0.8,
  "write_access": false
}
```

Codex keeps working while Claude/DeepSeek execute the branches. Near the end,
it reads accepted worker updates. Child tasks cannot recursively create another
tree, and every parent has a fan-out cap.

### How agents and the operator communicate

Communication is through durable, explicit messages—not by letting one model
read another model's hidden reasoning:

```text
Claude operator or Codex specialist
  -> sas_dispatch.dispatch_task(goal + observed evidence)
  -> SQLite child task
  -> Claude Code / OpenCode worker leases the task
  -> deterministic verifier runs the candidate
  -> accepted result + claims are stored
  -> sas_dispatch.get_worker_updates returns the compact result to the parent

Operator
  -> agents / agent-watch reads public task and tool events
  -> agent-message queues a steering message
  -> active Codex SDK turn receives turn.steer(message)

Claude operator / Codex reverse captain
  -> sas_control reads only its assigned service state/evidence
  -> request_patch_deploy / request_rollback / request_monitor_check
  -> durable operator action (not yet executed)
  -> operator action-execute invokes deterministic controller hooks
```

In a second terminal, list current work and follow one task live:

```bash
uv run sas-arena --config config/arena.local.toml agents
uv run sas-arena --config config/arena.local.toml \
  agent-watch TASK_ID --follow
```

The blue-team rehearsal launcher seeds the defense roles immediately: Claude
traffic analyst/reconstructor, Codex reverse-exploit and patch captains, Claude
patch builder, and DeepSeek patch verifier. Start it with:

```bash
uv run --no-sync python lab/onlychess/rehearsal_defender.py --workers 10
```

There is also a single wrapper for both sides. On a defender laptop:

```bash
./tools/start-rehearsal.sh defender
```

On Sami/Kodo's attacker laptop:

```bash
./tools/start-rehearsal.sh attacker sami
```

The attacker mode runs the red cell beside the same interactive TUI; quitting
the TUI stops that red-cell launcher.

Its TUI is interactive. Press `a` for the live-agent view, `j/k` (or arrow
keys) to select a task, `m` to send a message to that task's configured
provider, and `o` to return to the overview. The view shows only public task
events, tool activity, summaries, verifier results, and errors; private model
reasoning is never displayed. The launcher also accepts `--read-only` when a
non-interactive observer is required.

The watch stream shows state changes, provider/session startup, public tool
activity, completion, cancellation, verifier results, and errors. It never
shows private chain-of-thought.

You can steer an active Codex turn without stopping it:

```bash
uv run sas-arena --config config/arena.local.toml \
  agent-message TASK_ID "The checker failure only occurs after anonymous migration" \
  --provider codex
```

That message is inserted into the SQLite control inbox and the live SDK turn
polls and delivers it with `turn.steer`. Use the named instance, for example
`--provider codex-b`, when that is the active provider. Mid-turn steering for
Arena-launched Claude Code and OpenCode workers is **not implemented yet**;
their public activity and final session IDs are visible, and follow-up tasks can
resume their sessions, but live operator messages currently target Codex only.

All three harnesses run on the teammate's own laptop. Codex is confined by its
SDK task sandbox (`read-only` or `workspace-write`) and receives the relevant
Arena skill plus `sas_dispatch` and `sas_control`. Claude Code and OpenCode run
inside Arena-created candidate workspaces with their normal coding tools, but
they are not an OS security boundary: they inherit the teammate account's local
permissions. Arena embeds the relevant Arena workflow in their task prompt; it
does not blindly expose every personal skill installed on that laptop.

### Claude and DeepSeek

Claude and DeepSeek are high-throughput workers for source maps, independent
hypothesis tests, traffic triage, first exploit candidates, and first patch
candidates. Their output is useful only after deterministic verification.

They are now real coding-agent harnesses, not reduced chat completions:

- **Claude** runs the installed Claude Code CLI with its `default` tools and
  `permission-mode=auto`. It can read and edit files, run commands and tests,
  use configured skills, and create Claude subagents. Arena gives it an
  isolated candidate workspace and tells it whether the task authorizes source
  changes; deterministic gates still decide whether its work is usable.
- **DeepSeek** runs through OpenCode with `--auto`. It can use OpenCode's file,
  edit, search, shell, skill, and subagent capabilities. Arena ships a
  secret-free `opencode.json` model/provider declaration; each machine supplies
  its own DeepSeek credential through OpenCode or `DEEPSEEK_API_KEY`.
- The old direct DeepSeek API adapter remains available only with
  `transport = "api"`. It is a constrained fallback for a machine that cannot
  run OpenCode, not the default worker.

`write_access = false` is an instruction and task contract, while the harness
still has the technical ability to run normal development commands. Arena gives
each candidate a separate worktree/copy so accepted and rejected edits do not
collide. This is **not an OS security sandbox** for Claude Code or OpenCode:
their local shell can technically reach whatever that teammate account can
reach. Competition deployment therefore needs disposable worker containers/VMs
with only the service workspace mounted. Codex already uses its SDK sandbox.

Both CLI adapters consume JSON event streams. Arena records public activity
such as session initialization, tool name, command lifecycle, file-change
lifecycle, completion, and error. It intentionally discards hidden thinking and
reasoning blocks. Peers exchange explicit tasks and evidence, never private
chain-of-thought.

The router starts one fast instance from each available family. For difficult
work it starts or hedges a Codex instance without serializing the fast attempts.
The first candidate that passes the real gate wins; other model calls are
cancelled.

### Missing harnesses on a teammate's machine

Every provider has an explicit availability mode:

```toml
[providers.codex]
availability = "required" # fail startup if missing

[providers.claude]
availability = "optional" # skip cleanly if Claude Code is absent

[providers.deepseek]
availability = "optional" # skip cleanly if OpenCode/auth is absent
```

The allowed values are:

- `required`: the controller refuses to start without the harness/credential;
- `optional`: omit this provider from that machine's pool and keep working;
- `disabled`: never load it.

`sas-arena doctor` never invokes a model or consumes quota. It resolves common
per-user install locations, checks the Python Codex SDK, and checks OpenCode for
a DeepSeek credential. On the current machine it resolves Claude Code at
`~/.local/bin/claude` and OpenCode at `~/.opencode/bin/opencode`, even though
OpenCode is not on `PATH`.

### Multiple subscriptions/accounts

Provider families and provider instances are separate concepts. `codex-a` and
`codex-b` are both Codex-family instances with independent persistent sessions
and authentication environments. `max_concurrency = 0` means Arena imposes no
per-provider turn semaphore; it does not remove upstream subscription/rate
limits or the controller's finite worker pool.

Named instances are configured independently. SDK instances use the runtime
pinned by `openai-codex` unless `codex_bin` is explicitly set:

```toml
[[provider_instances]]
name = "codex-a"
family = "codex"
availability = "required"
transport = "sdk"
codex_bin = ""
auth_mode = "chatgpt"
reasoning_effort = "high"
max_concurrency = 0 # no Arena-imposed turn semaphore
timeout_seconds = 900

[[provider_instances]]
name = "codex-b"
family = "codex"
availability = "optional"
transport = "sdk"
codex_bin = ""
auth_mode = "chatgpt"
reasoning_effort = "high"
max_concurrency = 0 # no Arena-imposed turn semaphore
timeout_seconds = 900

[[provider_instances]]
name = "claude-a"
family = "claude"
availability = "optional"
command = "claude-a"
model = "sonnet"
max_concurrency = 0 # no Arena-imposed turn semaphore

[[provider_instances]]
name = "deepseek-a"
family = "deepseek"
availability = "optional"
transport = "opencode"
command = "opencode"
model = "deepseek/deepseek-v4-flash"
max_concurrency = 4
```

Two SDK instances in the same OS account inherit the same Codex login; they do
not create a second subscription quota or bypass an upstream concurrency limit.
For genuinely separate subscriptions, run each SDK-backed Arena worker under a
separately authenticated OS account or isolated host. Until the PostgreSQL
backend is implemented, keep one machine as the SQLite controller and connect
remote model workers through a future worker API; do not place SQLite on a
shared network filesystem. The legacy `transport = "cli"` mode can still use
separately authenticated local wrapper commands during development.

Do not put subscription cookies or API keys in TOML, task JSON, prompts, or
service descriptors.
OpenCode reads the DeepSeek credential from its own credential store or
`DEEPSEEK_API_KEY`; Arena never copies the key into a task or prompt. The
direct-API fallback reads `DEEPSEEK_API_KEY` from the controller environment.
Verification hooks have all model-provider secrets removed from their
environment.

If a service omits `cell.provider_instances`, it can use the whole configured
pool. Otherwise it gets only the named instances. This is how additional Codex
subscriptions can be dedicated to the hardest services without mixing their
sessions or workspaces.

## Prompts and context

Prompts are assembled from four pieces:

1. `prompts/charter.md`: authorization, scope, evidence, and verification rules.
2. `prompts/roles/*.md`: the narrow job for this task.
3. A trusted controller packet: task, service descriptor, revision, accepted
   claims, and recent state transitions.
4. A separately labelled untrusted-evidence block for logs, traffic, source
   snippets, tool output, or model-generated observations.

Relevant roles currently include:

- source mapper and checker mapper;
- traffic analyst and vulnerability scout;
- exploit builder and independent exploit verifier;
- patch builder and independent patch verifier;
- incident responder and service captain.

Full model conversations are never copied to another worker. The context
builder selects bounded recent claims/events and labels freshness by service
revision. Captain sessions are stored under:

```text
service + role + provider instance + service revision
```

A new deployment revision therefore cannot accidentally resume a stale captain
session. Codex threads are started/resumed through app-server and their IDs are
persisted in SQLite. Claude Code and OpenCode session IDs are likewise persisted
and resumed for their captains. The direct-API fallback receives the compact
packet on every task. The controller does not copy one harness's entire
transcript into another harness.

Repository-scoped reusable workflows live in `.agents/skills/`:

- `arena-service-bootstrap`: onboard an unknown event service;
- `arena-offense`: discover, prove, register, and farm an exploit;
- `arena-defense`: diagnose, patch, canary, deploy, and roll back safely.

Skills describe repeatable procedure. They do not contain challenge answers.
`PromptComposer` includes the relevant workflow in every provider's bounded task
packet, so Claude Code and OpenCode follow the same procedure even when their
personal skill directories differ between teammates. The Codex SDK additionally
sends it as a native `SkillInput`: bootstrap for source/checker mapping, offense
for vulnerability/exploit work, and defense for patch/incident work. OpenCode
also discovers the repository's `.agents/skills/` directory when supported by
the installed harness.

## Event and service descriptors

The Python code understands generic hooks, not challenge stacks. Event-day
knowledge is supplied through TOML.

Start from:

```text
events/template/event.toml
events/template/services/service-a.toml
```

An event descriptor defines services, tick timing, flag syntax, submission, and
authorized network scope:

```toml
schema_version = 1
id = "sas-2026"
name = "SAS 2026 Finals"
tick_seconds = 120
flag_pattern = '[A-Z0-9]{31}='
services = ["services/service-a.toml", "services/service-b.toml"]

[scope]
network_enabled = true
allowed_cidrs = ["10.60.0.0/16"]

[submission]
flag_lifetime_seconds = 600
interval_seconds = 5
batch_size = 100
lease_seconds = 30

[hooks.submit]
argv = ["./ops/submit-flag", "{flag}"]
timeout_seconds = 5
```

A service descriptor declares its source root, targets, commands, monitors, and
cell allocation:

```toml
schema_version = 1
id = "service-a"
name = "Event-provided name"
root = "../../../services/service-a"
tags = ["web", "stateful"]

[targets]
hosts = ["10.60.1.2", "10.60.1.3"]

[context]
revision = "initial-source-hash"

[cell]
workers = 6
provider_instances = ["codex-a", "claude-a", "deepseek-a"]
offense_enabled = false

[hooks.health]
argv = ["./ops/health"]
timeout_seconds = 5

[hooks.checker]
argv = ["./ops/checker-roundtrip"]
timeout_seconds = 30

[hooks.verify_exploit]
argv = ["./ops/verify-exploit", "{candidate_root}"]
timeout_seconds = 120

[hooks.verify_patch]
argv = ["./ops/verify-patch", "{candidate_root}"]
timeout_seconds = 300

[hooks.deploy]
argv = ["./ops/deploy", "{candidate_root}", "{revision}"]
timeout_seconds = 120

[hooks.rollback]
argv = ["./ops/rollback"]
timeout_seconds = 120

[hooks.attack]
argv = ["./ops/run-verified-exploit", "{target}"]
timeout_seconds = 20

[monitors.availability]
hook = "health"
interval_seconds = 5
failure_threshold = 2
recovery_threshold = 1
task_kind = "incident_response"
lane = "defense"
risk = "critical"
auto_rollback = true
```

Hooks are argv arrays executed without a shell. Runtime placeholders include
`{service_root}`, `{event_root}`, `{candidate_root}`, `{workspace}`, `{revision}`,
`{service_id}`, `{target}`, and `{flag}` when the relevant controller supplies
them. Other braces remain unchanged so flags, JSON, regex, and source snippets
can safely appear in arguments.

Validate before running:

```bash
uv run sas-arena event-validate path/to/event.toml
```

## Monitoring and defense

The monitor is deterministic; no LLM continuously watches the service.

Here, **deterministic** means the monitor is an ordinary repeatable program:
the same hook exit code/output and previous counter state produce the same next
state. For example, every five seconds it can run `curl /health`, the official
checker round-trip, a container/process check, a database integrity query, or a
service-specific smoke script. Exit code `0` is success; a non-zero exit code or
timeout is failure. The model is not asked whether the service "looks healthy."

This is deliberately split into two loops:

```text
fast control loop (no model)
  timer -> health/checker/integrity hook -> counters -> alert/rollback decision

reasoning loop (only after an event)
  alert -> incident task -> Claude/DeepSeek/Codex diagnosis -> patch candidate
        -> verify_patch -> deploy -> health + checker -> keep or rollback
```

So monitoring remains responsive even when Codex is thinking for several
minutes, a provider is rate-limited, or every model is offline. Models diagnose
and repair; they are not the heartbeat.

Each configured monitor:

1. runs its hook at the configured interval;
2. stores the bounded result and consecutive success/failure state;
3. opens an alert only at the failure threshold;
4. creates a service-local defense task;
5. optionally invokes rollback;
6. records recovery after the configured success threshold.

Run monitors once or continuously:

```bash
uv run sas-arena monitor-once path/to/event.toml
uv run sas-arena monitor path/to/event.toml
```

### Traffic detection and exploit reconstruction

Traffic tools do the continuous collection and cheap filtering. The model
hierarchy is fixed rather than selected by the general router:

```text
Tulip / Suricata / service logs
              |
              v
Claude traffic analyst (fast classification)
              |
       suspicious candidate
              v
Claude reconstruction lead (faithful local replay)
              |
       locally proven primitive
              v
Codex reverse-exploit captain <----> 3+ DeepSeek parallel workers
              |
              v
verify_exploit gate -> farm candidate + defensive root cause
```

The Claude analyst and reconstruction lead use persistent service-local
sessions. The analyst classifies and stops benign traffic. The reconstruction
lead writes and proves the replay. Only a proven replay starts the persistent
Codex reverse captain; the controller immediately seeds three DeepSeek branches
for protocol/state analysis, source-to-sink analysis, and an independent exploit
candidate. Codex can dispatch more branches while continuing its own
investigation.

The hook may query Tulip, consume Suricata EVE output, inspect a reverse proxy
log, or call a service-specific decoder. It emits one bounded JSON object per
line, or a `detections` array:

```json
{
  "detections": [
    {
      "fingerprint": "private-object-read-v1",
      "summary": "unauthenticated request returned a private object",
      "score": 0.94,
      "flow_id": "tulip-session-3817",
      "artifact": "captures/session-3817.pcap"
    }
  ]
}
```

Configure the adapter per service:

```toml
[hooks.traffic]
argv = ["./ops/read-traffic-detections"]
timeout_seconds = 5

[traffic]
hook = "traffic"
interval_seconds = 2
min_score = 0.7
max_detections = 50
dedupe_window_seconds = 300
deadline_seconds = 300
risk = "high"
```

Run it directly with:

```bash
uv run sas-arena traffic-once path/to/event.toml
uv run sas-arena traffic path/to/event.toml
```

Normalize an incremental Suricata EVE stream before the hook consumes it:

```bash
uv run sas-arena traffic-normalize /var/log/suricata/eve.json \
  --format suricata --cursor .arena/cursors/service-a.json \
  --checker-cidr 10.10.0.0/24
```

For each accepted detection the controller records `traffic.detection`, creates
one Claude `traffic_triage` task per signature per observed tick, and puts the
evidence in the prompt's untrusted-data section. Candidate results advance to a
separate Claude `traffic_reconstruct` task. A locally proven replay advances to
a Codex `reverse_exploit` task and its DeepSeek children. The final candidate is
still blocked on `verify_exploit`; any patch remains blocked on the normal
patch/checker gates.

Patch tasks cannot be accepted by model confidence. With an event descriptor,
`patch_build`, `patch_verify`, and `incident_response` tasks require the
service's `verify_patch` hook. `exploit_build` and `exploit_verify` require
`verify_exploit`.

Deploy a verified candidate:

```bash
uv run sas-arena defense-deploy path/to/event.toml service-a \
  .arena/worktrees/TASK_ID/codex-a --revision patch-001
```

The defense controller executes:

```text
verify_patch -> deploy -> health -> checker -> active
                  |
                  +-- any failure -> rollback
                                      -> rolled_back / rollback_failed
```

All transitions and hook evidence are stored in SQLite. A failed canary is not
left active.

## Offense and flags

Only an exploit that passed `verify_exploit` is eligible for the farm. Accepted
artifacts are copied into `.arena/artifacts`, versioned in the exploit registry,
and activated automatically by default. The target must appear in
`targets.hosts`, event and runtime network scope must both be enabled, and the
target IP must be inside an allowed CIDR.

Inspect or change the active revision:

```bash
uv run sas-arena exploits --service service-a
uv run sas-arena exploit-promote EXPLOIT_ID
```

Accepted patches remain operator-controlled:

```bash
uv run sas-arena patches --service service-a
uv run sas-arena patch-promote path/to/event.toml PATCH_ID
```

When the Claude operator or a Codex specialist requests an action, the TUI
`actions` count increases.
Inspect and execute it explicitly:

```bash
uv run sas-arena actions --service service-a --status requested
uv run sas-arena action-execute path/to/event.toml ACTION_ID
```

`action-execute` supports verified patch deployment, rollback of the currently
active Arena deployment, and a named monitor check. The model cannot execute
arbitrary controller hooks through this interface.

Run one target manually:

```bash
uv run sas-arena attack-once path/to/event.toml service-a 10.60.1.2
```

The service offense runner:

1. enforces the declared target and CIDR;
2. runs the verified exploit hook;
3. accepts either NDJSON records such as `{"flags":["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="]}` or ordinary
   output, while retaining only strings matching the event flag pattern;
4. sends validated flags to the event-wide FlagBroker.

The FlagBroker then:

1. hashes and deduplicates flags atomically in SQLite;
2. expires flags after the event-defined lifetime;
3. leases a batch so multiple farm processes cannot submit the same flag at
   the same time;
4. allocates scarce submission slots round-robin across service/target pairs;
5. invokes the pluggable event submission hook;
6. stores success, retry, expiry, attempt count, raw response, exploit version,
   and tick metadata.

Submit a recovered backlog without rerunning exploits:

```bash
uv run sas-arena flags-submit-once path/to/event.toml
```

Set `cell.offense_enabled = true` only after `verify_exploit` passes. The service
cell then runs the hook for every declared target once per tick.

### Horizontal farm scaling

Targets can be split deterministically between machines or processes. Give each
instance the same `shard_count` and a distinct zero-based `shard_index`:

```toml
[farm]
shard_index = 0
shard_count = 3
submission_worker_id = "farm-host-a"
```

Each `(service, target)` pair belongs to exactly one shard. All shards may share
the SQLite database only on a filesystem with correct SQLite locking; for
multi-host deployment, replace the store with PostgreSQL before competition.
The database lease still protects against duplicate submissions from multiple
supervisors using that shared store.

These mechanics were deliberately adapted from
[S4DFarm](https://github.com/C4T-BuT-S4D/S4DFarm): target sharding, strict
subprocess deadlines, central deduplication, flag lifetime, fair submission,
and protocol adapters. Its URL-only caching proxy is intentionally not reused;
it is unsafe for authenticated or state-changing traffic. S4DFarm is an
offense-only farm, so monitoring, checker-aware defense, deployment, and
rollback remain Arena responsibilities.

The evidence-backed comparison and adoption decisions are recorded in
[`docs/s4dfarm-review.md`](docs/s4dfarm-review.md).

## Task routing

Task JSON is available for manual experiments:

```bash
uv run sas-arena --config config/arena.local.toml \
  enqueue examples/source-map-task.json

uv run sas-arena --config config/arena.local.toml \
  --event events/template/event.toml run --workers 4 --idle-exit-seconds 5
```

Important task fields:

```json
{
  "service": "service-a",
  "kind": "vuln_scout",
  "lane": "offense",
  "goal": "Test one bounded authorization hypothesis",
  "workdir": "services/service-a",
  "risk": "high",
  "expected_value": 0.9,
  "deadline_seconds": 300,
  "max_attempts": 2,
  "context": {
    "role": "vulnerability-scout",
    "captain_session": false,
    "write_access": false,
    "service_revision": "source-hash",
    "untrusted_observations": [],
    "verification": {"commands": []}
  }
}
```

Supported task kinds are `source_map`, `checker_map`, `traffic_triage`,
`traffic_reconstruct`, `reverse_exploit`, `vuln_scout`, `exploit_build`,
`exploit_verify`, `patch_build`, `patch_verify`, `incident_response`, and
`diagnose`. Lanes are `analysis`, `offense`, `defense`, and `operations`.

## Installation and checks

Requirements:

- Python 3.12 or newer;
- `uv`;
- a Codex login for SDK-backed Codex workers, and/or Claude Code;
- Docker and Git for event services that use them;
- OpenCode plus a connected DeepSeek credential or `DEEPSEEK_API_KEY` when the
  OpenCode/DeepSeek provider is enabled.

Install and test:

```bash
cd /Users/blinkyy/sas-agent-arena
uv sync
uv run python -m unittest discover -s tests -v
uv run sas-arena doctor
uv run sas-arena event-validate events/template/event.toml
uv run sas-arena --config config/arena.example.toml \
  cells-once events/template/event.toml
```

`doctor` never calls a model and never spends quota.

## Files

```text
AGENTS.md                         Repository agent policy
.agents/skills/                  Bootstrap/offense/defense workflows
config/arena.example.toml        Safe runtime and provider template
config/cluster.five-laptops.example.toml Two-service laptop assignment example
opencode.json                    Secret-free DeepSeek/OpenCode model declaration
events/template/                 Generic event/service descriptor example
examples/                        Generic task examples
fixtures/generic-service/        Runnable empty descriptor fixture
fixtures/sas2025/                Historical regression task packets
lab/onlychess/                   Historical 2025 integration harness
prompts/                         Charter and role prompts
schemas/agent_result.schema.json Structured model-output contract
src/sas_arena/cells.py           Per-service cell supervisor
src/sas_arena/cluster.py         Five-laptop assignment validation/planning
src/sas_arena/dashboard.py       Grace/tick terminal dashboard
src/sas_arena/event_adapters.py  Configurable organizer HTTP adapter
src/sas_arena/hub.py             Authenticated phase sync and heartbeats
src/sas_arena/phases.py          Grace/live/freeze phase policy and task seeding
src/sas_arena/promotion.py       Artifact, exploit, and patch promotion
src/sas_arena/context.py         Bounded service context packets
src/sas_arena/defense.py         Verify/deploy/check/rollback controller
src/sas_arena/flags.py           Global expiry-aware flag broker
src/sas_arena/hooks.py           Shell-free deterministic hook runner
src/sas_arena/monitoring.py      Monitor state machine and incident creation
src/sas_arena/mcp_control.py     Service-scoped agent reads and action requests
src/sas_arena/offense.py         Exploit runner and flag pipeline
src/sas_arena/orchestrator.py    Candidate races and verification
src/sas_arena/providers/codex_sdk.py Persistent Codex SDK/app-server adapter
src/sas_arena/providers/         Claude Code, OpenCode, API fallback, and mock adapters
src/sas_arena/specs.py           Event/service TOML contracts
src/sas_arena/store.py           SQLite evidence and operational state
src/sas_arena/traffic.py         Detection ingestion, deduplication, and replay tasks
src/sas_arena/traffic_adapters.py Suricata/generic traffic normalization
vendor/sasctf-finals-2025/       Historical official archive
```

## Safety model

- Filesystem work is restricted to configured roots.
- Network execution is denied by default.
- Live attack requires both runtime config and event scope to enable it.
- Exploit targets must be explicitly declared and inside allowed CIDRs.
- Hooks never use a shell.
- Model credentials are removed from hook environments.
- Candidate work happens in a separate Git worktree when possible, or a copied
  directory when the source is not a Git repository. This provides change
  isolation, not an OS security boundary for full CLI harnesses.
- Fast child tasks cannot recursively dispatch more children.
- Source, traffic, logs, and model output are explicitly treated as untrusted
  prompt data.

This repository is for authorized competition and replay infrastructure only.

## What is missing from the architecture

### P0 — required for the first serious rehearsal

1. **Full-harness worker sandboxing**

   Run Claude Code and OpenCode in disposable containers/VMs with only the
   candidate workspace, required build/runtime sockets, and authorized event
   network attached. The current local adapters intentionally have normal shell
   capability and therefore inherit the teammate OS account's access.

2. **Complete generated service gates**

   Bootstrap produces safe draft hooks and discovered checker candidates. A
   grace-period agent still has to configure the actual checker round-trip,
   exploit proof, patch proof, deploy, and rollback commands for the new service.

3. **Full PCAP/Tulip artifact index**

   Suricata EVE and generic JSONL collectors are built. Add long-term PCAP
   storage, Tulip session links, protocol-aware indexing, and cross-node search.

4. **SAS 2026 adapter configuration**

   Fill the generic HTTP adapter with the actual tick, team, scoreboard,
   attack-info, flag-submission, and checker-status URLs and response paths once
   the organizers provide them.

5. **One-service end-to-end rehearsal**

   Run the full path with real model turns:

   ```text
   archive -> bootstrap -> map -> find -> exploit -> synthetic flag proof
           -> farm -> patch -> checker/availability proof -> deploy -> rollback test
   ```

   Repeat until the run is deterministic enough to measure time-to-exploit,
   patch survival, false positives, model latency, and subscription usage.

### P1 — required for multiple subscriptions and event reliability

1. **Remote artifact replication** between duplicate service owners. Bounded
   claims and events synchronize now; large captures and build artifacts remain
   local.
2. **Credential isolation from full harnesses** using disposable worker VMs or
   containers while retaining the shell/build capabilities required by services.
3. **Provider telemetry and admission control** for rate limits, banked resets,
   current concurrency, latency percentiles, failures, and quota-aware routing.
4. **Supervisor packaging** using containers or system services, health probes,
   restart policy, resource limits, structured logs, and configuration validation.
5. **Recovery drills** for controller loss, worker loss, stale leases,
   corrupted artifacts, and provider outages.

### P2 — useful after correctness and reliability

1. Browser dashboard; the local terminal dashboard is already operational.
2. Adaptive routing learned from rehearsal data: verified results per minute,
   exploit yield, patch survival, and score gain per model-minute.
3. Automatic context compaction and long-session health checks.
4. Cross-service strategy that reallocates workers based on SLA loss, opponent
   exposure, exploit yield, flag expiry, and expected score gain.
5. Service-type plugins for pwn, crypto, AI/prompt, game protocols, and browser
   workflows, while keeping the control plane challenge-agnostic.

## Recommended implementation order

Do not add more permanent agents first. Build the missing proof loop in this
order:

1. Bootstrap one SAS 2025 service and replace its generated fail-closed hooks
   with the real checker/exploit/patch/deploy gates.
2. Run the grace profile with Claude operators, Codex specialists, and DeepSeek workers.
3. Prove automatic exploit promotion and manual patch canary deployment.
4. Replay ticks, checker failures, rollback, flag expiry, and provider failure
   for several hours.
5. Rehearse the hub with five laptops and two assigned services each.
6. Add optional evidence/artifact replication only if rehearsals show that local
   ownership prevents useful cross-team coordination.

This sequence makes the single-service loop correct before distributing it.
Otherwise a multi-host deployment would only scale unverified behavior.

## Definition of competition-ready

Arena should not be called competition-ready until all of these are true:

- a new service can be onboarded without editing Python control-plane code;
- event tick, scoreboard, checker, and flag submission adapters are operational;
- incoming traffic becomes bounded, queryable evidence;
- at least one exploit is discovered, verified, promoted, farmed, and retired
  through the controller;
- at least one patch is generated, exploit-tested, checker-tested, canary
  deployed, and automatically rolled back during a forced failure;
- each independent subscription runs in an isolated authenticated worker;
- controller restart and worker loss do not lose accepted evidence, flags, or
  deployment state;
- a multi-service rehearsal runs for at least eight hours with measured SLA,
  flag yield, provider latency, escalation rate, and cost/quota consumption;
- all destructive actions remain confined to the authorized event scope.

Until the rehearsal and generated live gates pass, the accurate label is
**phase-aware five-laptop orchestration MVP**.
