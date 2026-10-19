# S4DFarm design review

Reviewed against pinned commit
[`3b8b057`](https://github.com/C4T-BuT-S4D/S4DFarm/commit/3b8b057c95e0467c6afb3a47648e8b6befd1a815).

## Conclusion

S4DFarm is a useful offense-farm reference, not a complete Attack-Defense
control plane. Arena should borrow its proven mechanics below the service-cell
layer, without adopting its architecture wholesale.

The resulting topology is:

```text
event controller
|- service cell A: agents, monitors, offense runner, defense controller
|- service cell B: agents, monitors, offense runner, defense controller
|- service cell N: agents, monitors, offense runner, defense controller
`- shared: FlagBroker, tick/scoreboard adapter, provider quotas
```

## Adopted

| S4DFarm idea | Arena implementation |
| --- | --- |
| Repeated parallel exploit runs | Each cell runs its declared targets concurrently every tick |
| Strict process deadlines and cleanup | Every hook has a timeout; timed-out children are terminated then killed |
| Deterministic `K/N` distribution | CRC32-based `(service,target)` sharding through `[farm]` config |
| Central deduplication | The event-wide FlagBroker uses a SHA-256 primary key in SQLite |
| Flag lifetime | Event `[submission].flag_lifetime_seconds`; stale queue entries become `expired` |
| Fair limited submission | Expiry-aware round-robin leases across service/target pairs |
| Retryable verdicts | Failed/unknown hook results return to `retry` until expiry |
| Pluggable submission protocol | Event `hooks.submit` can call any HTTP/TCP/event adapter executable |
| Output batching contract | Exploits may emit NDJSON `{"flags":[...]}`; regex output remains supported |

The relevant upstream mechanics are in
[`start_sploit.py`](https://github.com/C4T-BuT-S4D/S4DFarm/blob/master/client/start_sploit.py),
[`tasks.py`](https://github.com/C4T-BuT-S4D/S4DFarm/blob/master/server/app/tasks.py),
and the
[`protocols` directory](https://github.com/C4T-BuT-S4D/S4DFarm/tree/master/server/app/protocols).

## Not adopted

- The whole Flask/PostgreSQL/Redis/Vue stack: Arena already needs a broader
  evidence, monitoring, model-routing, deployment, and rollback control plane.
- The URL-only caching/MITM proxy: URL-only cache identity is unsafe for
  authenticated, state-changing, checker, and flag traffic.
- Fixed-loop scheduling as the only scheduler: Arena must eventually account
  for event ticks, checker state, exploit yield, provider quotas, and incidents.
- Any defense design: S4DFarm has no patch verification, canary deployment,
  checker preservation, service monitoring, or rollback workflow.

## Still to add before competition

- Prometheus-compatible counters and latency histograms by exploit, target,
  submission verdict, provider, and service.
- Event-specific scoreboard/tick adapters and jittered scheduling.
- Sandboxed exploit processes with CPU, memory, file, and network limits.
- PostgreSQL-backed queue leasing for multi-host Arena supervisors.
- Signed, atomic exploit/config rollouts so every farm worker runs the same
  verified revision.
