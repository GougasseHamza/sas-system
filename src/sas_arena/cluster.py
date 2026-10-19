from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .specs import EventSpec


@dataclass(frozen=True, slots=True)
class NodeAssignment:
    id: str
    services: tuple[str, ...]
    address: str = ""


@dataclass(frozen=True, slots=True)
class ClusterSpec:
    descriptor: Path
    expected_nodes: int
    services_per_node: int
    nodes: tuple[NodeAssignment, ...]

    @classmethod
    def load(cls, path: str | Path) -> ClusterSpec:
        descriptor = Path(path).resolve()
        with descriptor.open("rb") as handle:
            value = tomllib.load(handle)
        cluster = value.get("cluster", {})
        if not isinstance(cluster, dict):
            raise ValueError("cluster must be a TOML table")
        expected_nodes = int(cluster.get("expected_nodes", 5))
        services_per_node = int(cluster.get("services_per_node", 2))
        raw_nodes = value.get("nodes", [])
        if not isinstance(raw_nodes, list):
            raise ValueError("nodes must be an array of tables")
        nodes: list[NodeAssignment] = []
        seen: set[str] = set()
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                raise ValueError("each node must be a table")
            node_id = str(raw.get("id", "")).strip()
            services = raw.get("services", [])
            address = str(raw.get("address", "")).strip()
            if not node_id or node_id in seen:
                raise ValueError(f"invalid or duplicate node id: {node_id!r}")
            if not isinstance(services, list) or not all(
                isinstance(item, str) and item for item in services
            ):
                raise ValueError(f"node {node_id!r} services must be strings")
            if len(services) != services_per_node or len(set(services)) != len(
                services
            ):
                raise ValueError(
                    f"node {node_id!r} must own exactly {services_per_node} "
                    "distinct services"
                )
            seen.add(node_id)
            nodes.append(NodeAssignment(node_id, tuple(services), address))
        if expected_nodes <= 0 or services_per_node <= 0:
            raise ValueError("cluster sizes must be positive")
        if len(nodes) != expected_nodes:
            raise ValueError(
                f"cluster requires exactly {expected_nodes} nodes; got {len(nodes)}"
            )
        return cls(descriptor, expected_nodes, services_per_node, tuple(nodes))

    def validate(self, event: EventSpec) -> dict[str, list[str]]:
        known = set(event.services)
        assigned = {service for node in self.nodes for service in node.services}
        unknown = sorted(assigned - known)
        missing = sorted(known - assigned)
        if unknown:
            raise ValueError("cluster references unknown services: " + ", ".join(unknown))
        if missing:
            raise ValueError("cluster leaves services unassigned: " + ", ".join(missing))
        return {
            node.id: list(node.services)
            for node in self.nodes
        }

    def node(self, node_id: str) -> NodeAssignment:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise ValueError(f"cluster has no node {node_id!r}")


def round_robin_plan(
    service_ids: list[str], node_ids: list[str], *, services_per_node: int = 2
) -> dict[str, list[str]]:
    if not service_ids:
        raise ValueError("cannot plan a cluster without services")
    if not node_ids:
        raise ValueError("cannot plan a cluster without nodes")
    if len(service_ids) < services_per_node:
        raise ValueError("not enough distinct services for each node assignment")
    if len(service_ids) > len(node_ids) * services_per_node:
        raise ValueError("cluster has fewer slots than services")
    plan: dict[str, list[str]] = {node: [] for node in node_ids}
    cursor = 0
    for service in service_ids:
        while len(plan[node_ids[cursor % len(node_ids)]]) >= services_per_node:
            cursor += 1
        plan[node_ids[cursor % len(node_ids)]].append(service)
        cursor += 1
    duplicate_cursor = 0
    for node in node_ids:
        while len(plan[node]) < services_per_node:
            candidate = service_ids[duplicate_cursor % len(service_ids)]
            duplicate_cursor += 1
            if candidate not in plan[node]:
                plan[node].append(candidate)
    return plan
