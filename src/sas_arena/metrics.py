from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .store import ArenaStore


def render_prometheus(store: ArenaStore) -> str:
    """Render low-cardinality operational metrics from persisted Arena state."""
    lines = [
        "# HELP sas_arena_tasks Current tasks by state.",
        "# TYPE sas_arena_tasks gauge",
    ]
    with store.connect() as db:
        for row in db.execute(
            "SELECT state, COUNT(*) AS count FROM tasks GROUP BY state ORDER BY state"
        ):
            lines.append(
                f'sas_arena_tasks{{state="{_label(row["state"])}"}} {row["count"]}'
            )
        lines.extend(
            [
                "# HELP sas_arena_flags Current flags by service and status.",
                "# TYPE sas_arena_flags gauge",
            ]
        )
        for row in db.execute(
            """
            SELECT service, status, COUNT(*) AS count FROM flags
            GROUP BY service, status ORDER BY service, status
            """
        ):
            lines.append(
                "sas_arena_flags"
                f'{{service="{_label(row["service"])}",'
                f'status="{_label(row["status"])}"}} {row["count"]}'
            )
        lines.extend(
            [
                "# HELP sas_arena_monitor_alert Whether a service monitor alert is open.",
                "# TYPE sas_arena_monitor_alert gauge",
                "# HELP sas_arena_monitor_failures Consecutive monitor failures.",
                "# TYPE sas_arena_monitor_failures gauge",
            ]
        )
        for row in db.execute(
            """
            SELECT service, monitor, alert_open, consecutive_failures
            FROM monitor_state ORDER BY service, monitor
            """
        ):
            labels = (
                f'service="{_label(row["service"])}",monitor="{_label(row["monitor"])}"'
            )
            lines.append(f"sas_arena_monitor_alert{{{labels}}} {row['alert_open']}")
            lines.append(
                f"sas_arena_monitor_failures{{{labels}}} {row['consecutive_failures']}"
            )
        lines.extend(
            [
                "# HELP sas_arena_provider_results Model results by provider and acceptance.",
                "# TYPE sas_arena_provider_results counter",
                "# HELP sas_arena_provider_latency_ms Total persisted result latency in milliseconds.",
                "# TYPE sas_arena_provider_latency_ms counter",
            ]
        )
        for row in db.execute(
            """
            SELECT provider, accepted, COUNT(*) AS count
            FROM results GROUP BY provider, accepted
            ORDER BY provider, accepted
            """
        ):
            provider = _label(row["provider"])
            accepted = str(int(row["accepted"]))
            lines.append(
                f'sas_arena_provider_results{{provider="{provider}",'
                f'accepted="{accepted}"}} {row["count"]}'
            )
        latency_rows = db.execute(
            "SELECT provider, payload_json FROM results ORDER BY provider"
        ).fetchall()
        latency: dict[str, int] = {}
        for row in latency_rows:
            payload = json.loads(str(row["payload_json"]))
            provider = str(row["provider"])
            latency[provider] = latency.get(provider, 0) + int(
                payload.get("latency_ms", 0)
            )
        for provider, milliseconds in sorted(latency.items()):
            lines.append(
                f'sas_arena_provider_latency_ms{{provider="{_label(provider)}"}} '
                f"{milliseconds}"
            )
        lines.extend(
            [
                "# HELP sas_arena_event_state_updated_seconds Last event-state update time.",
                "# TYPE sas_arena_event_state_updated_seconds gauge",
            ]
        )
        for row in db.execute("SELECT key, updated_at FROM event_state ORDER BY key"):
            lines.append(
                "sas_arena_event_state_updated_seconds"
                f'{{key="{_label(row["key"])}"}} {row["updated_at"]}'
            )
        phase_row = db.execute(
            "SELECT value_json FROM event_state WHERE key = 'competition_phase'"
        ).fetchone()
        if phase_row is not None:
            phase = json.loads(str(phase_row["value_json"]))
            lines.extend(
                [
                    "# HELP sas_arena_phase Current competition phase.",
                    "# TYPE sas_arena_phase gauge",
                    f'sas_arena_phase{{phase="{_label(phase.get("phase", "unknown"))}"}} 1',
                    "# HELP sas_arena_tick Current competition tick.",
                    "# TYPE sas_arena_tick gauge",
                    f"sas_arena_tick {int(phase.get('tick', 0))}",
                ]
            )
        lines.extend(
            [
                "# HELP sas_arena_exploits Exploit registry entries by status.",
                "# TYPE sas_arena_exploits gauge",
            ]
        )
        for row in db.execute(
            "SELECT service, status, COUNT(*) AS count FROM exploits "
            "GROUP BY service, status ORDER BY service, status"
        ):
            lines.append(
                "sas_arena_exploits"
                f'{{service="{_label(row["service"])}",status="{_label(row["status"])}"}} '
                f"{row['count']}"
            )
        lines.extend(
            [
                "# HELP sas_arena_patch_queue Patch candidates by status.",
                "# TYPE sas_arena_patch_queue gauge",
            ]
        )
        for row in db.execute(
            "SELECT service, status, COUNT(*) AS count FROM patch_queue "
            "GROUP BY service, status ORDER BY service, status"
        ):
            lines.append(
                "sas_arena_patch_queue"
                f'{{service="{_label(row["service"])}",status="{_label(row["status"])}"}} '
                f"{row['count']}"
            )
        lines.extend(
            [
                "# HELP sas_arena_node_heartbeat_seconds Last coordinator heartbeat by node.",
                "# TYPE sas_arena_node_heartbeat_seconds gauge",
            ]
        )
        for row in db.execute(
            "SELECT node, updated_at FROM node_heartbeats ORDER BY node"
        ):
            lines.append(
                "sas_arena_node_heartbeat_seconds"
                f'{{node="{_label(row["node"])}"}} {row["updated_at"]}'
            )
    return "\n".join(lines) + "\n"


def write_prometheus(store: ArenaStore, path: str | Path) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(render_prometheus(store))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
