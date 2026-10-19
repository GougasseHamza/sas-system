from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Risk, TaskKind, TaskLane

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_FLAG_PATTERN = r"[A-Z][A-Z0-9_]*\{[^\r\n{}]{1,256}\}"


class SpecError(ValueError):
    """Raised when an event or service descriptor is unsafe or inconsistent."""


@dataclass(frozen=True, slots=True)
class HookSpec:
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 30
    expected_returncodes: tuple[int, ...] = (0,)
    env: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, location: str) -> HookSpec:
        argv = value.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
        ):
            raise SpecError(f"{location}.argv must be a non-empty string array")
        cwd = value.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd:
            raise SpecError(f"{location}.cwd must be a non-empty string")
        timeout = int(value.get("timeout_seconds", 30))
        if timeout <= 0 or timeout > 3600:
            raise SpecError(f"{location}.timeout_seconds must be between 1 and 3600")
        codes = value.get("expected_returncodes", [0])
        if (
            not isinstance(codes, list)
            or not codes
            or not all(isinstance(item, int) for item in codes)
        ):
            raise SpecError(
                f"{location}.expected_returncodes must be a non-empty integer array"
            )
        env = value.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in env.items()
        ):
            raise SpecError(f"{location}.env must be a string table")
        return cls(tuple(argv), cwd, timeout, tuple(codes), dict(env))


@dataclass(frozen=True, slots=True)
class MonitorSpec:
    hook: str
    interval_seconds: float = 10.0
    failure_threshold: int = 1
    recovery_threshold: int = 1
    task_kind: TaskKind = TaskKind.INCIDENT_RESPONSE
    lane: TaskLane = TaskLane.DEFENSE
    risk: Risk = Risk.HIGH
    auto_rollback: bool = False
    rollback_window_seconds: int = 300

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, location: str) -> MonitorSpec:
        hook = value.get("hook")
        if not isinstance(hook, str) or not hook:
            raise SpecError(f"{location}.hook must be a non-empty string")
        interval = float(value.get("interval_seconds", 10))
        failures = int(value.get("failure_threshold", 1))
        recoveries = int(value.get("recovery_threshold", 1))
        rollback_window = int(value.get("rollback_window_seconds", 300))
        if interval <= 0 or failures <= 0 or recoveries <= 0 or rollback_window <= 0:
            raise SpecError(f"{location} intervals and thresholds must be positive")
        try:
            task_kind = TaskKind(value.get("task_kind", TaskKind.INCIDENT_RESPONSE))
            lane = TaskLane(value.get("lane", TaskLane.DEFENSE))
            risk = Risk(value.get("risk", Risk.HIGH))
        except ValueError as error:
            raise SpecError(
                f"{location} contains an invalid enum value: {error}"
            ) from error
        return cls(
            hook=hook,
            interval_seconds=interval,
            failure_threshold=failures,
            recovery_threshold=recoveries,
            task_kind=task_kind,
            lane=lane,
            risk=risk,
            auto_rollback=bool(value.get("auto_rollback", False)),
            rollback_window_seconds=rollback_window,
        )


@dataclass(frozen=True, slots=True)
class TrafficSpec:
    """Service-local adapter from traffic tooling into bounded triage tasks."""

    hook: str
    interval_seconds: float = 2.0
    min_score: float = 0.7
    max_detections: int = 50
    dedupe_window_seconds: int = 300
    deadline_seconds: int = 300
    risk: Risk = Risk.HIGH

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, location: str) -> TrafficSpec:
        hook = value.get("hook")
        if not isinstance(hook, str) or not hook:
            raise SpecError(f"{location}.hook must be a non-empty string")
        interval = float(value.get("interval_seconds", 2))
        min_score = float(value.get("min_score", 0.7))
        max_detections = int(value.get("max_detections", 50))
        dedupe_window = int(value.get("dedupe_window_seconds", 300))
        deadline = int(value.get("deadline_seconds", 300))
        if interval <= 0:
            raise SpecError(f"{location}.interval_seconds must be positive")
        if not 0 <= min_score <= 1:
            raise SpecError(f"{location}.min_score must be between 0 and 1")
        if max_detections <= 0 or max_detections > 1000:
            raise SpecError(
                f"{location}.max_detections must be between 1 and 1000"
            )
        if dedupe_window <= 0 or deadline <= 0:
            raise SpecError(
                f"{location}.dedupe_window_seconds and deadline_seconds "
                "must be positive"
            )
        try:
            risk = Risk(value.get("risk", Risk.HIGH))
        except ValueError as error:
            raise SpecError(f"{location}.risk is invalid: {error}") from error
        return cls(
            hook=hook,
            interval_seconds=interval,
            min_score=min_score,
            max_detections=max_detections,
            dedupe_window_seconds=dedupe_window,
            deadline_seconds=deadline,
            risk=risk,
        )


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    id: str
    name: str
    root: Path
    descriptor: Path
    hooks: Mapping[str, HookSpec]
    monitors: Mapping[str, MonitorSpec] = field(default_factory=dict)
    traffic: TrafficSpec | None = None
    tags: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)
    worker_count: int = 4
    provider_instances: tuple[str, ...] = ()
    offense_enabled: bool = False

    @classmethod
    def load(cls, path: str | Path) -> ServiceSpec:
        descriptor = Path(path).resolve()
        with descriptor.open("rb") as handle:
            value = tomllib.load(handle)
        if int(value.get("schema_version", 1)) != 1:
            raise SpecError(f"unsupported service schema in {descriptor}")
        service_id = _require_identifier(value.get("id"), f"{descriptor}: id")
        root_value = value.get("root", ".")
        if not isinstance(root_value, str):
            raise SpecError(f"{descriptor}: root must be a string")
        root = (descriptor.parent / root_value).resolve()
        hooks_value = value.get("hooks", {})
        if not isinstance(hooks_value, dict):
            raise SpecError(f"{descriptor}: hooks must be a table")
        hooks = {
            _require_identifier(name, f"{descriptor}: hook name"): HookSpec.from_dict(
                hook, location=f"hooks.{name}"
            )
            for name, hook in hooks_value.items()
        }
        monitors_value = value.get("monitors", {})
        if not isinstance(monitors_value, dict):
            raise SpecError(f"{descriptor}: monitors must be a table")
        monitors = {
            _require_identifier(
                name, f"{descriptor}: monitor name"
            ): MonitorSpec.from_dict(monitor, location=f"monitors.{name}")
            for name, monitor in monitors_value.items()
        }
        missing_hooks = sorted(
            monitor.hook for monitor in monitors.values() if monitor.hook not in hooks
        )
        if missing_hooks:
            raise SpecError(
                f"{descriptor}: monitors reference missing hooks: {', '.join(missing_hooks)}"
            )
        rollback_monitors = [
            name
            for name, monitor in monitors.items()
            if monitor.auto_rollback and "rollback" not in hooks
        ]
        if rollback_monitors:
            raise SpecError(
                f"{descriptor}: auto-rollback monitors require a rollback hook: "
                + ", ".join(rollback_monitors)
            )
        traffic_value = value.get("traffic")
        if traffic_value is not None and not isinstance(traffic_value, dict):
            raise SpecError(f"{descriptor}: traffic must be a table")
        traffic = (
            None
            if traffic_value is None
            else TrafficSpec.from_dict(traffic_value, location="traffic")
        )
        if traffic is not None and traffic.hook not in hooks:
            raise SpecError(
                f"{descriptor}: traffic references missing hook {traffic.hook!r}"
            )
        tags = _string_tuple(value.get("tags", []), f"{descriptor}: tags")
        targets_table = value.get("targets", {})
        if not isinstance(targets_table, dict):
            raise SpecError(f"{descriptor}: targets must be a table")
        targets = _string_tuple(
            targets_table.get("hosts", []), f"{descriptor}: targets.hosts"
        )
        context = value.get("context", {})
        if not isinstance(context, dict):
            raise SpecError(f"{descriptor}: context must be a table")
        cell = value.get("cell", {})
        if not isinstance(cell, dict):
            raise SpecError(f"{descriptor}: cell must be a table")
        worker_count = int(cell.get("workers", 4))
        if worker_count <= 0 or worker_count > 64:
            raise SpecError(f"{descriptor}: cell.workers must be between 1 and 64")
        provider_instances = _string_tuple(
            cell.get("provider_instances", []),
            f"{descriptor}: cell.provider_instances",
        )
        name = value.get("name", service_id)
        if not isinstance(name, str) or not name:
            raise SpecError(f"{descriptor}: name must be a non-empty string")
        return cls(
            id=service_id,
            name=name,
            root=root,
            descriptor=descriptor,
            hooks=hooks,
            monitors=monitors,
            traffic=traffic,
            tags=tags,
            targets=targets,
            context=dict(context),
            worker_count=worker_count,
            provider_instances=provider_instances,
            offense_enabled=bool(cell.get("offense_enabled", False)),
        )

    def hook(self, name: str) -> HookSpec:
        try:
            return self.hooks[name]
        except KeyError as error:
            raise SpecError(f"service {self.id!r} has no {name!r} hook") from error

    def public_context(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "root": str(self.root),
            "tags": list(self.tags),
            "targets": list(self.targets),
            "available_hooks": sorted(self.hooks),
            "traffic": (
                None
                if self.traffic is None
                else {
                    "hook": self.traffic.hook,
                    "interval_seconds": self.traffic.interval_seconds,
                    "min_score": self.traffic.min_score,
                    "max_detections": self.traffic.max_detections,
                }
            ),
            "declared_context": dict(self.context),
            "cell": {
                "workers": self.worker_count,
                "provider_instances": list(self.provider_instances),
                "offense_enabled": self.offense_enabled,
            },
        }


@dataclass(frozen=True, slots=True)
class EventSpec:
    id: str
    name: str
    descriptor: Path
    services: Mapping[str, ServiceSpec]
    tick_seconds: float = 120.0
    flag_pattern: str = DEFAULT_FLAG_PATTERN
    hooks: Mapping[str, HookSpec] = field(default_factory=dict)
    network_enabled: bool = False
    allowed_cidrs: tuple[str, ...] = ()
    flag_lifetime_seconds: int = 300
    submission_interval_seconds: float = 5.0
    submission_batch_size: int = 100
    submission_lease_seconds: int = 30
    observation_hook: str | None = None
    observation_interval_seconds: float = 2.0

    @classmethod
    def load(cls, path: str | Path) -> EventSpec:
        descriptor = Path(path).resolve()
        with descriptor.open("rb") as handle:
            value = tomllib.load(handle)
        if int(value.get("schema_version", 1)) != 1:
            raise SpecError(f"unsupported event schema in {descriptor}")
        event_id = _require_identifier(value.get("id"), f"{descriptor}: id")
        service_paths = value.get("services", [])
        if not isinstance(service_paths, list) or not all(
            isinstance(item, str) for item in service_paths
        ):
            raise SpecError(f"{descriptor}: services must be a string array")
        services: dict[str, ServiceSpec] = {}
        for relative in service_paths:
            service = ServiceSpec.load(descriptor.parent / relative)
            if service.id in services:
                raise SpecError(f"{descriptor}: duplicate service id {service.id!r}")
            services[service.id] = service
        hooks_value = value.get("hooks", {})
        if not isinstance(hooks_value, dict):
            raise SpecError(f"{descriptor}: hooks must be a table")
        hooks = {
            _require_identifier(name, f"{descriptor}: hook name"): HookSpec.from_dict(
                hook, location=f"hooks.{name}"
            )
            for name, hook in hooks_value.items()
        }
        scope = value.get("scope", {})
        if not isinstance(scope, dict):
            raise SpecError(f"{descriptor}: scope must be a table")
        allowed_cidrs = _string_tuple(
            scope.get("allowed_cidrs", []), f"{descriptor}: scope.allowed_cidrs"
        )
        submission = value.get("submission", {})
        if not isinstance(submission, dict):
            raise SpecError(f"{descriptor}: submission must be a table")
        flag_lifetime = int(submission.get("flag_lifetime_seconds", 300))
        submission_interval = float(submission.get("interval_seconds", 5))
        submission_batch = int(submission.get("batch_size", 100))
        submission_lease = int(submission.get("lease_seconds", 30))
        if flag_lifetime <= 0:
            raise SpecError(
                f"{descriptor}: submission.flag_lifetime_seconds must be positive"
            )
        if submission_interval <= 0:
            raise SpecError(
                f"{descriptor}: submission.interval_seconds must be positive"
            )
        if submission_batch <= 0 or submission_batch > 10_000:
            raise SpecError(
                f"{descriptor}: submission.batch_size must be between 1 and 10000"
            )
        if submission_lease <= 0 or submission_lease > 3600:
            raise SpecError(
                f"{descriptor}: submission.lease_seconds must be between 1 and 3600"
            )
        observation = value.get("observation", {})
        if not isinstance(observation, dict):
            raise SpecError(f"{descriptor}: observation must be a table")
        observation_hook = observation.get("hook")
        if observation_hook is not None and (
            not isinstance(observation_hook, str) or not observation_hook
        ):
            raise SpecError(f"{descriptor}: observation.hook must be a string")
        if observation_hook is not None and observation_hook not in hooks:
            raise SpecError(
                f"{descriptor}: observation references missing hook "
                f"{observation_hook!r}"
            )
        observation_interval = float(observation.get("interval_seconds", 2))
        if observation_interval <= 0:
            raise SpecError(
                f"{descriptor}: observation.interval_seconds must be positive"
            )
        tick_seconds = float(value.get("tick_seconds", 120))
        if tick_seconds <= 0:
            raise SpecError(f"{descriptor}: tick_seconds must be positive")
        flag_pattern = value.get("flag_pattern", DEFAULT_FLAG_PATTERN)
        if not isinstance(flag_pattern, str):
            raise SpecError(f"{descriptor}: flag_pattern must be a string")
        try:
            re.compile(flag_pattern)
        except re.error as error:
            raise SpecError(f"{descriptor}: invalid flag_pattern: {error}") from error
        name = value.get("name", event_id)
        if not isinstance(name, str) or not name:
            raise SpecError(f"{descriptor}: name must be a non-empty string")
        return cls(
            id=event_id,
            name=name,
            descriptor=descriptor,
            services=services,
            tick_seconds=tick_seconds,
            flag_pattern=flag_pattern,
            hooks=hooks,
            network_enabled=bool(scope.get("network_enabled", False)),
            allowed_cidrs=allowed_cidrs,
            flag_lifetime_seconds=flag_lifetime,
            submission_interval_seconds=submission_interval,
            submission_batch_size=submission_batch,
            submission_lease_seconds=submission_lease,
            observation_hook=observation_hook,
            observation_interval_seconds=observation_interval,
        )

    @property
    def root(self) -> Path:
        return self.descriptor.parent

    def service(self, service_id: str) -> ServiceSpec:
        try:
            return self.services[service_id]
        except KeyError as error:
            raise SpecError(
                f"event {self.id!r} has no service {service_id!r}"
            ) from error

    def validate(self, *, require_roots: bool = True) -> list[str]:
        messages: list[str] = []
        if not self.services:
            messages.append("warning: event declares no services")
        for service in self.services.values():
            if require_roots and not service.root.is_dir():
                raise SpecError(
                    f"service {service.id!r} root does not exist: {service.root}"
                )
            if service.offense_enabled:
                missing = [
                    item
                    for item, present in (
                        ("attack hook", "attack" in service.hooks),
                        ("declared targets", bool(service.targets)),
                        ("event submit hook", "submit" in self.hooks),
                        ("event network scope", self.network_enabled),
                        ("allowed CIDRs", bool(self.allowed_cidrs)),
                    )
                    if not present
                ]
                if missing:
                    raise SpecError(
                        f"service {service.id!r} enables offense but lacks: "
                        + ", ".join(missing)
                    )
            messages.append(
                f"service {service.id}: {len(service.hooks)} hooks, "
                f"{len(service.monitors)} monitors, "
                f"traffic {'enabled' if service.traffic else 'disabled'}"
            )
        return messages


def _require_identifier(value: Any, location: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SpecError(f"{location} must match {_IDENTIFIER.pattern}")
    return value


def _string_tuple(value: Any, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SpecError(f"{location} must be a string array")
    return tuple(value)
