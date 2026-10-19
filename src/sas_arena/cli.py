from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
from ipaddress import ip_network
from pathlib import Path

from .cells import CellSupervisor, ServiceCell, shard_targets
from .bootstrap import bootstrap_service
from .backup import create_backup
from .cluster import ClusterSpec, NodeAssignment, round_robin_plan
from .config import ArenaConfig
from .context import ContextBuilder
from .dashboard import phase_json, run_dashboard
from .defense import DefenseController
from .flags import FlagBroker
from .event_adapters import HttpEventAdapter
from .hooks import HookRunner
from .hub import CoordinatorHub, NodeSynchronizer
from .metrics import render_prometheus, write_prometheus
from .models import Risk, Task, TaskKind, TaskState
from .monitoring import MonitorEngine
from .observation import EventObserver
from .offense import OffenseController
from .orchestrator import Orchestrator
from .phases import PhaseController, PhasePolicy
from .prompts import PromptComposer
from .promotion import ArtifactStore, PromotionManager
from .providers import (
    ClaudeCliProvider,
    CodexCliProvider,
    CodexSdkProvider,
    DeepSeekProvider,
    MockProvider,
    OpenCodeCliProvider,
)
from .providers.command import resolve_executable
from .router import LatencyRouter
from .scope import ScopePolicy
from .shadow import ShadowDispatcher
from .specs import EventSpec
from .store import ArenaStore
from .traffic import TrafficEngine
from .traffic_adapters import normalize_file
from .verifier import ArenaVerifier, CommandVerifier
from .workspace import WorkspaceManager


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sas-arena")
    root.add_argument(
        "--config",
        default="config/arena.example.toml",
        help="Path to arena TOML configuration",
    )
    root.add_argument(
        "--event",
        help="Optional event TOML used for service context and runtime scope",
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("task_file")
    commands.add_parser("run-once")
    run = commands.add_parser("run")
    run.add_argument("--workers", type=int, default=6)
    run.add_argument("--idle-exit-seconds", type=float, default=0)
    commands.add_parser("status")
    agents = commands.add_parser("agents")
    agents.add_argument("--all", action="store_true", dest="include_all")
    agents.add_argument("--limit", type=int, default=50)
    watch = commands.add_parser("agent-watch")
    watch.add_argument("task_id")
    watch.add_argument("--follow", action="store_true")
    watch.add_argument("--interval", type=float, default=0.5)
    message = commands.add_parser("agent-message")
    message.add_argument("task_id")
    message.add_argument("message")
    message.add_argument("--provider", default="codex")
    metrics = commands.add_parser("metrics")
    metrics.add_argument("--output")
    commands.add_parser("doctor")
    commands.add_parser("demo")
    validate = commands.add_parser("event-validate")
    validate.add_argument("event_file")
    monitor_once = commands.add_parser("monitor-once")
    monitor_once.add_argument("event_file")
    monitor_once.add_argument("--service", action="append", dest="services")
    monitor = commands.add_parser("monitor")
    monitor.add_argument("event_file")
    monitor.add_argument("--service", action="append", dest="services")
    traffic_once = commands.add_parser("traffic-once")
    traffic_once.add_argument("event_file")
    traffic_once.add_argument("--service", action="append", dest="services")
    traffic = commands.add_parser("traffic")
    traffic.add_argument("event_file")
    traffic.add_argument("--service", action="append", dest="services")
    observe_once = commands.add_parser("observe-once")
    observe_once.add_argument("event_file")
    observe = commands.add_parser("observe")
    observe.add_argument("event_file")
    deploy = commands.add_parser("defense-deploy")
    deploy.add_argument("event_file")
    deploy.add_argument("service")
    deploy.add_argument("candidate")
    deploy.add_argument("--revision", required=True)
    attack = commands.add_parser("attack-once")
    attack.add_argument("event_file")
    attack.add_argument("service")
    attack.add_argument("target")
    attack.add_argument("--no-submit", action="store_true")
    submit_flags = commands.add_parser("flags-submit-once")
    submit_flags.add_argument("event_file")
    cells_once = commands.add_parser("cells-once")
    cells_once.add_argument("event_file")
    cells_once.add_argument("--cluster")
    cells_once.add_argument("--node")
    cells_once.add_argument("--hub")
    cells_once.add_argument("--hub-token-env", default="SAS_ARENA_HUB_TOKEN")
    cells = commands.add_parser("cells")
    cells.add_argument("event_file")
    cells.add_argument("--cluster")
    cells.add_argument("--node")
    cells.add_argument("--hub")
    cells.add_argument("--hub-token-env", default="SAS_ARENA_HUB_TOKEN")
    phase_status = commands.add_parser("phase-status")
    phase_status.add_argument("event_file")
    phase_grace = commands.add_parser("phase-grace")
    phase_grace.add_argument("event_file")
    phase_grace.add_argument("--duration", type=int)
    phase_live = commands.add_parser("phase-live")
    phase_live.add_argument("event_file")
    phase_freeze = commands.add_parser("phase-freeze")
    phase_freeze.add_argument("event_file")
    phase_end = commands.add_parser("phase-end")
    phase_end.add_argument("event_file")
    tui = commands.add_parser("tui")
    tui.add_argument("event_file")
    tui.add_argument("--cluster")
    tui.add_argument("--node")
    tui.add_argument("--read-only", action="store_true")
    cluster_validate = commands.add_parser("cluster-validate")
    cluster_validate.add_argument("event_file")
    cluster_validate.add_argument("cluster_file")
    cluster_plan = commands.add_parser("cluster-plan")
    cluster_plan.add_argument("event_file")
    cluster_plan.add_argument("--nodes", required=True)
    cluster_plan.add_argument("--services-per-node", type=int, default=2)
    exploits = commands.add_parser("exploits")
    exploits.add_argument("--service")
    exploit_promote = commands.add_parser("exploit-promote")
    exploit_promote.add_argument("exploit_id")
    patches = commands.add_parser("patches")
    patches.add_argument("--service")
    actions = commands.add_parser("actions")
    actions.add_argument("--service")
    actions.add_argument("--status")
    patch_promote = commands.add_parser("patch-promote")
    patch_promote.add_argument("event_file")
    patch_promote.add_argument("patch_id")
    action_execute = commands.add_parser("action-execute")
    action_execute.add_argument("event_file")
    action_execute.add_argument("action_id")
    service_bootstrap = commands.add_parser("service-bootstrap")
    service_bootstrap.add_argument("event_file")
    service_bootstrap.add_argument("source_root")
    service_bootstrap.add_argument("--id", required=True, dest="service_id")
    service_bootstrap.add_argument("--name")
    service_bootstrap.add_argument("--no-enqueue", action="store_true")
    hub_serve = commands.add_parser("hub-serve")
    hub_serve.add_argument("event_file")
    hub_serve.add_argument("cluster_file")
    hub_serve.add_argument("--bind", default="0.0.0.0")
    hub_serve.add_argument("--port", type=int, default=8765)
    hub_serve.add_argument("--token-env", default="SAS_ARENA_HUB_TOKEN")
    traffic_normalize = commands.add_parser("traffic-normalize")
    traffic_normalize.add_argument("input_file")
    traffic_normalize.add_argument(
        "--format", choices=("suricata", "generic"), default="suricata"
    )
    traffic_normalize.add_argument("--cursor")
    traffic_normalize.add_argument("--checker-cidr", action="append", default=[])
    traffic_normalize.add_argument("--max-detections", type=int, default=50)
    event_observe = commands.add_parser("event-http-observe")
    event_observe.add_argument("adapter_file")
    event_submit = commands.add_parser("event-http-submit")
    event_submit.add_argument("adapter_file")
    event_submit.add_argument("flag")
    event_submit_batch = commands.add_parser("event-http-submit-batch")
    event_submit_batch.add_argument("adapter_file")
    event_submit_batch.add_argument("flags_json")
    backup = commands.add_parser("backup")
    backup.add_argument("output")
    return root


def providers_from_config(
    config: ArenaConfig,
    *,
    dispatch_database: Path | None = None,
    control_database: Path | None = None,
    shadow_values: dict | None = None,
    prompt_composer: PromptComposer | None = None,
):
    configured = config.section("providers")
    providers = {}
    codex = configured.get("codex", {})
    if _provider_enabled("codex", codex):
        shadow_values = shadow_values or {}
        providers["codex"] = _codex_provider(
            codex,
            config=config,
            name=None,
            dispatch_database=dispatch_database,
            control_database=control_database,
            shadow_values=shadow_values,
            prompt_composer=prompt_composer,
        )
    claude = configured.get("claude", {})
    if _provider_enabled("claude", claude):
        providers["claude"] = ClaudeCliProvider(
            command=claude.get("command", "claude"),
            model=claude.get("model", "sonnet"),
            max_concurrency=int(claude.get("max_concurrency", 0)),
            timeout_seconds=int(claude.get("timeout_seconds", 600)),
            dispatch_database=dispatch_database,
            control_database=control_database,
            dispatch_max_children=int(
                (shadow_values or {}).get("max_children_per_parent", 8)
            ),
            dispatch_child_deadline_seconds=int(
                (shadow_values or {}).get("default_deadline_seconds", 180)
            ),
            prompt_composer=prompt_composer,
        )
    deepseek = configured.get("deepseek", {})
    if _provider_enabled("deepseek", deepseek):
        providers["deepseek"] = _deepseek_provider(
            deepseek,
            name=None,
            prompt_composer=prompt_composer,
        )
    instances = config.values.get("provider_instances", [])
    if not isinstance(instances, list):
        raise TypeError("provider_instances must be an array of tables")
    for values in instances:
        if not isinstance(values, dict):
            continue
        name = str(values.get("name", "")).strip()
        family = str(values.get("family", "")).strip()
        if not _provider_enabled(name or family or "provider", values):
            continue
        if not name or name in providers:
            raise ValueError(f"invalid or duplicate provider instance name: {name!r}")
        if family == "codex":
            shadow_values = shadow_values or {}
            providers[name] = _codex_provider(
                values,
                config=config,
                name=name,
                dispatch_database=dispatch_database,
                control_database=control_database,
                shadow_values=shadow_values,
                prompt_composer=prompt_composer,
            )
        elif family == "claude":
            providers[name] = ClaudeCliProvider(
                command=values.get("command", "claude"),
                model=values.get("model", "sonnet"),
                max_concurrency=int(values.get("max_concurrency", 0)),
                timeout_seconds=int(values.get("timeout_seconds", 600)),
                dispatch_database=dispatch_database,
                control_database=control_database,
                dispatch_max_children=int(
                    (shadow_values or {}).get("max_children_per_parent", 8)
                ),
                dispatch_child_deadline_seconds=int(
                    (shadow_values or {}).get("default_deadline_seconds", 180)
                ),
                prompt_composer=prompt_composer,
                instance_id=name,
            )
        elif family == "deepseek":
            providers[name] = _deepseek_provider(
                values,
                name=name,
                prompt_composer=prompt_composer,
            )
        else:
            raise ValueError(f"unsupported provider family for {name!r}: {family!r}")
    return providers


def _deepseek_provider(
    values: dict,
    *,
    name: str | None,
    prompt_composer: PromptComposer | None,
):
    transport = str(values.get("transport", "opencode")).strip().lower()
    if transport == "opencode":
        return OpenCodeCliProvider(
            command=values.get("command", "opencode"),
            model=values.get("model", "deepseek/deepseek-v4-flash"),
            agent=values.get("agent", ""),
            max_concurrency=int(
                values.get("max_concurrency", 4 if name is None else 1)
            ),
            timeout_seconds=int(values.get("timeout_seconds", 600)),
            standalone=bool(values.get("standalone", False)),
            prompt_composer=prompt_composer,
            instance_id=name,
        )
    if transport == "api":
        return DeepSeekProvider(
            base_url=values.get("base_url", "https://api.deepseek.com"),
            model=values.get("model", "deepseek-chat"),
            max_concurrency=int(
                values.get("max_concurrency", 4 if name is None else 1)
            ),
            timeout_seconds=int(values.get("timeout_seconds", 600)),
            prompt_composer=prompt_composer,
            instance_id=name,
            api_key_env=values.get("api_key_env", "DEEPSEEK_API_KEY"),
        )
    raise ValueError(f"unsupported DeepSeek transport: {transport!r}")


def _availability(values: dict) -> str:
    value = values.get("availability")
    if value is None:
        return "required" if values.get("enabled", False) else "disabled"
    mode = str(value).strip().lower()
    if mode not in {"required", "optional", "disabled"}:
        raise ValueError(f"invalid provider availability: {value!r}")
    return mode


def _provider_readiness(family: str, values: dict) -> tuple[bool, str]:
    if family == "codex" and values.get("transport", "sdk") == "sdk":
        ok = importlib.util.find_spec("openai_codex") is not None
        return ok, "openai-codex SDK + existing Codex login"
    if family == "claude":
        command = str(values.get("command", "claude"))
        resolved = resolve_executable(
            command, "~/.local/bin/claude", "~/.claude/local/claude"
        )
        if not resolved:
            return False, command
        try:
            auth = subprocess.run(
                [resolved, "auth", "status"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=ClaudeCliProvider.isolated_environment("claude"),
            )
            payload = json.loads(auth.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return False, f"{resolved}; could not inspect Claude subscription auth"
        logged_in = bool(payload.get("loggedIn"))
        method = str(payload.get("authMethod") or "none")
        api_billed = "api" in method.lower()
        if not logged_in or api_billed:
            return False, (
                f"{resolved}; Claude subscription OAuth missing "
                f"(authMethod={method}); run `claude auth login`"
            )
        return True, f"{resolved} + Claude subscription auth ({method})"
    if family == "deepseek" and values.get("transport", "opencode") == "opencode":
        command = str(values.get("command", "opencode"))
        resolved = resolve_executable(
            command, "~/.opencode/bin/opencode", "~/.local/bin/opencode"
        )
        if not resolved:
            return False, command
        if os.environ.get(str(values.get("api_key_env", "DEEPSEEK_API_KEY"))):
            return True, f"{resolved} + DeepSeek environment credential"
        try:
            auth = subprocess.run(
                [resolved, "auth", "list"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, f"{resolved}; could not inspect OpenCode auth"
        has_auth = "deepseek" in (auth.stdout + auth.stderr).lower()
        detail = f"{resolved} + OpenCode DeepSeek auth"
        return has_auth, detail if has_auth else f"{resolved}; DeepSeek auth missing"
    if family == "deepseek":
        env_name = str(values.get("api_key_env", "DEEPSEEK_API_KEY"))
        return bool(os.environ.get(env_name)), f"environment {env_name}"
    command = str(values.get("command", family))
    resolved = resolve_executable(command)
    return bool(resolved), resolved or command


def _provider_enabled(label: str, values: dict) -> bool:
    mode = _availability(values)
    if mode == "disabled":
        return False
    family = str(values.get("family") or label.split("-", 1)[0])
    ready, detail = _provider_readiness(family, values)
    if ready:
        return True
    if mode == "optional":
        return False
    raise RuntimeError(f"required provider {label!r} is unavailable: {detail}")


def _configured_provider_names(config: ArenaConfig) -> set[str]:
    names: set[str] = set()
    for family, values in config.section("providers").items():
        if isinstance(values, dict) and _availability(values) != "disabled":
            names.add(str(family))
    for values in config.values.get("provider_instances", []):
        if not isinstance(values, dict) or _availability(values) == "disabled":
            continue
        name = str(values.get("name", "")).strip()
        if name:
            names.add(name)
    return names


def _configured_provider_family(config: ArenaConfig, name: str) -> str | None:
    values = config.section("providers").get(name)
    if isinstance(values, dict):
        return name
    for item in config.values.get("provider_instances", []):
        if isinstance(item, dict) and str(item.get("name", "")) == name:
            return str(item.get("family", "")) or None
    return None


def _codex_provider(
    values: dict,
    *,
    config: ArenaConfig,
    name: str | None,
    dispatch_database: Path | None,
    control_database: Path | None,
    shadow_values: dict,
    prompt_composer: PromptComposer | None,
):
    common = {
        "model": values.get("model", ""),
        "reasoning_effort": values.get("reasoning_effort", ""),
        "max_concurrency": int(values.get("max_concurrency", 0)),
        "timeout_seconds": int(values.get("timeout_seconds", 900)),
        "dispatch_database": dispatch_database,
        "dispatch_max_children": int(shadow_values.get("max_children_per_parent", 8)),
        "dispatch_child_deadline_seconds": int(
            shadow_values.get("default_deadline_seconds", 180)
        ),
        "prompt_composer": prompt_composer,
        "instance_id": name,
    }
    transport = str(values.get("transport", "sdk")).strip().lower()
    if transport == "sdk":
        return CodexSdkProvider(
            codex_bin=values.get("codex_bin", ""),
            auth_mode=values.get("auth_mode", "chatgpt"),
            service_tier=values.get("service_tier", ""),
            skills_root=config.project_root / ".agents/skills",
            control_database=control_database,
            **common,
        )
    if transport == "cli":
        return CodexCliProvider(
            command=values.get("command", "codex"),
            **common,
        )
    raise ValueError(f"unsupported Codex transport: {transport!r}")


def build_scope(config: ArenaConfig, event: EventSpec | None = None) -> ScopePolicy:
    scope_values = config.section("scope")
    roots = list(scope_values.get("allowed_roots", ["."]))
    filesystem_scope = ScopePolicy.from_values(config.project_root, roots, False, [])
    if event is not None:
        filesystem_scope.require_workdir(event.root)
        for service in event.services.values():
            filesystem_scope.require_workdir(service.root)
    config_network = bool(scope_values.get("network_enabled", False))
    network_enabled = config_network and bool(event and event.network_enabled)
    event_cidrs = tuple(event.allowed_cidrs if event else ())
    config_cidrs = tuple(scope_values.get("allowed_cidrs", []))
    if event is None:
        allowed_cidrs = list(config_cidrs)
    elif config_cidrs:
        allowed_cidrs = _cidr_intersection(config_cidrs, event_cidrs)
    else:
        allowed_cidrs = list(event_cidrs)
    return ScopePolicy(
        filesystem_scope.allowed_roots,
        network_enabled,
        tuple(allowed_cidrs),
    )


def _cidr_intersection(left: tuple[str, ...], right: tuple[str, ...]) -> list[str]:
    intersections: set[str] = set()
    for first_value in left:
        first = ip_network(first_value)
        for second_value in right:
            second = ip_network(second_value)
            if first.version != second.version or not first.overlaps(second):
                continue
            intersections.add(
                str(first if first.prefixlen >= second.prefixlen else second)
            )
    return sorted(intersections)


def build_orchestrator(
    config: ArenaConfig,
    event: EventSpec | None = None,
    *,
    service_filter: str | None = None,
    providers: dict | None = None,
) -> Orchestrator:
    state = config.section("state")
    routing = config.section("routing")
    store = ArenaStore(config.project_root / state["database"])
    store.initialize()
    scope = build_scope(config, event)
    prompt_values = config.section("prompts")
    prompt_composer = PromptComposer(
        config.project_root / prompt_values.get("root", "prompts"),
        config.project_root / ".agents/skills",
    )
    shadow_values = config.section("shadow")
    promotion_values = config.section("promotion")
    shadow = None
    if shadow_values.get("enabled", True):
        shadow = ShadowDispatcher(
            store,
            max_children_per_parent=int(
                shadow_values.get("max_children_per_parent", 8)
            ),
            default_deadline_seconds=int(
                shadow_values.get("default_deadline_seconds", 180)
            ),
        )
    hook_runner = HookRunner(scope)
    return Orchestrator(
        store=store,
        providers=(
            providers
            if providers is not None
            else providers_from_config(
                config,
                dispatch_database=store.path if shadow is not None else None,
                control_database=store.path,
                shadow_values=shadow_values,
                prompt_composer=prompt_composer,
            )
        ),
        router=LatencyRouter(float(routing.get("codex_hedge_delay_seconds", 90))),
        verifier=(
            ArenaVerifier(event, hook_runner)
            if event is not None
            else CommandVerifier()
        ),
        workspace_manager=WorkspaceManager(config.project_root / state["run_root"]),
        scope=scope,
        schema_path=config.project_root / "schemas/agent_result.schema.json",
        max_parallel_candidates=int(routing.get("max_parallel_candidates", 3)),
        shadow_dispatcher=shadow,
        context_builder=ContextBuilder(event, store) if event is not None else None,
        service_filter=service_filter,
        promotion_manager=PromotionManager(
            store,
            ArtifactStore(
                config.project_root / state.get("artifact_root", ".arena/artifacts")
            ),
            auto_promote_exploits=bool(
                promotion_values.get("auto_promote_exploits", True)
            ),
        ),
    )


def build_cell_supervisor(
    config: ArenaConfig,
    event: EventSpec,
    *,
    service_ids: set[str] | None = None,
    node: NodeAssignment | None = None,
    hub_url: str | None = None,
    hub_token: str | None = None,
) -> CellSupervisor:
    state = config.section("state")
    store = ArenaStore(config.project_root / state["database"])
    store.initialize()
    prompt_values = config.section("prompts")
    prompt_composer = PromptComposer(
        config.project_root / prompt_values.get("root", "prompts"),
        config.project_root / ".agents/skills",
    )
    shadow_values = config.section("shadow")
    providers = providers_from_config(
        config,
        dispatch_database=store.path if shadow_values.get("enabled", True) else None,
        control_database=store.path,
        shadow_values=shadow_values,
        prompt_composer=prompt_composer,
    )
    scope = build_scope(config, event)
    runner = HookRunner(scope)
    observer = EventObserver(event, store, runner)
    phase_policy = PhasePolicy.from_config(config)
    phase_controller = PhaseController(
        event, store, phase_policy, service_ids=service_ids
    )
    farm = config.section("farm")
    shard_index = int(farm.get("shard_index", 0))
    shard_count = int(farm.get("shard_count", 1))
    broker = FlagBroker(
        event,
        store,
        runner,
        worker_id=str(farm.get("submission_worker_id", "flag-broker")),
    )
    cells: list[ServiceCell] = []
    configured_provider_names = _configured_provider_names(config)
    for service in event.services.values():
        if service_ids is not None and service.id not in service_ids:
            continue
        if service.provider_instances:
            unknown = sorted(
                set(service.provider_instances) - configured_provider_names
            )
            if unknown:
                raise ValueError(
                    f"service {service.id!r} references unknown providers: "
                    + ", ".join(unknown)
                )
            service_providers = {
                name: providers[name]
                for name in service.provider_instances
                if name in providers
            }
            if not service_providers:
                raise RuntimeError(
                    f"service {service.id!r} has no available provider instances"
                )
        else:
            service_providers = providers
        cells.append(
            ServiceCell(
                service,
                store,
                build_orchestrator(
                    config,
                    event,
                    service_filter=service.id,
                    providers=service_providers,
                ),
                MonitorEngine(event, store, runner),
                TrafficEngine(event, store, runner),
                OffenseController(event, store, runner, broker),
                tick_seconds=event.tick_seconds,
                targets=shard_targets(
                    service.id,
                    service.targets,
                    shard_index=shard_index,
                    shard_count=shard_count,
                ),
                tick_driven=event.observation_hook is not None,
                max_workers=max(
                    service.worker_count,
                    phase_policy.setup.cell_workers,
                    phase_policy.grace.cell_workers,
                    phase_policy.live.cell_workers,
                    phase_policy.frozen.cell_workers,
                ),
            )
        )
    synchronizer = (
        NodeSynchronizer(hub_url, hub_token or "", node, phase_controller, store)
        if hub_url is not None and node is not None
        else None
    )
    return CellSupervisor(
        cells,
        broker,
        observer if event.observation_hook else None,
        phase_controller,
        synchronizer,
    )


def doctor(config: ArenaConfig) -> int:
    required: dict[str, bool] = {}
    display: list[tuple[str, str, str]] = []
    for tool in ("git", "docker"):
        value = shutil.which(tool)
        status = "READY" if value else "OPTIONAL-MISSING"
        detail = value or "optional for some services"
        if tool == "docker" and value:
            try:
                daemon = subprocess.run(
                    [value, "info", "--format", "{{.ServerVersion}}"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                status = "DAEMON-DOWN"
                detail = str(error)[-500:]
            else:
                if daemon.returncode != 0:
                    status = "DAEMON-DOWN"
                    detail = (daemon.stderr or daemon.stdout).strip()[-500:]
                else:
                    detail = f"{value} server={daemon.stdout.strip()}"
        display.append((tool, status, detail))
    configured = config.section("providers")
    for family in ("codex", "claude", "deepseek"):
        values = configured.get(family, {})
        mode = _availability(values)
        if mode == "disabled":
            display.append((family, "DISABLED", "disabled by configuration"))
            continue
        ok, detail = _provider_readiness(family, values)
        status = (
            "READY" if ok else ("OPTIONAL-SKIP" if mode == "optional" else "MISSING")
        )
        if mode == "required":
            required[family] = ok
        display.append((family, status, detail))
    for values in config.values.get("provider_instances", []):
        if not isinstance(values, dict):
            continue
        name = str(values.get("name", "unnamed"))
        family = str(values.get("family", ""))
        mode = _availability(values)
        if mode == "disabled":
            display.append((name, "DISABLED", "disabled by configuration"))
            continue
        ok, detail = _provider_readiness(family, values)
        status = (
            "READY" if ok else ("OPTIONAL-SKIP" if mode == "optional" else "MISSING")
        )
        if mode == "required":
            required[name] = ok
        display.append((name, status, detail))
    for name, status, detail in display:
        print(f"{name:16} {status:16} {detail}")
    print("provider calls: disabled (doctor never spends model quota)")
    return 0 if all(required.values()) else 1


async def demo(project_root: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="sas-arena-demo-") as temp:
        temp_path = Path(temp)
        source = temp_path / "source"
        source.mkdir()
        (source / "README.txt").write_text("authorized demo workspace\n")
        store = ArenaStore(temp_path / "arena.db")
        store.initialize()
        task = Task(
            service="demo",
            kind=TaskKind.EXPLOIT_BUILD,
            goal="Race two candidates and accept only a verified marker",
            workdir=str(source),
            risk=Risk.HIGH,
            expected_value=0.9,
            context={
                "write_access": True,
                "verification": {
                    "commands": [
                        [
                            "python3",
                            "-c",
                            "from pathlib import Path; assert Path('candidate.ok').is_file()",
                        ]
                    ]
                },
            },
        )
        store.enqueue(task)
        orchestrator = Orchestrator(
            store=store,
            providers={
                "deepseek": MockProvider("deepseek", delay_seconds=0.20, valid=False),
                "claude": MockProvider("claude", delay_seconds=0.05, valid=True),
                "codex": MockProvider("codex", delay_seconds=0.50, valid=True),
            },
            router=LatencyRouter(codex_hedge_delay_seconds=0),
            verifier=CommandVerifier(),
            workspace_manager=WorkspaceManager(temp_path / "worktrees"),
            scope=ScopePolicy((temp_path.resolve(),)),
            schema_path=project_root / "schemas/agent_result.schema.json",
            max_parallel_candidates=3,
        )
        await orchestrator.run_once("demo-controller")
        print(json.dumps(store.status_counts(), indent=2, sort_keys=True))
        print(json.dumps(store.recent_results(), indent=2, sort_keys=True))
        return 0 if store.status_counts().get("accepted") == 1 else 1


def _selected_node(
    event: EventSpec, cluster_file: str | None, node_id: str | None
) -> NodeAssignment | None:
    if cluster_file is None and node_id is None:
        return None
    if not cluster_file or not node_id:
        raise ValueError("--cluster and --node must be supplied together")
    cluster = ClusterSpec.load(cluster_file)
    cluster.validate(event)
    return cluster.node(node_id)


def _cluster_toml(plan: dict[str, list[str]], services_per_node: int) -> str:
    lines = [
        "[cluster]",
        f"expected_nodes = {len(plan)}",
        f"services_per_node = {services_per_node}",
        "",
    ]
    for node, services in plan.items():
        lines.extend(
            [
                "[[nodes]]",
                f"id = {json.dumps(node)}",
                "services = [" + ", ".join(json.dumps(item) for item in services) + "]",
                "address = \"\"",
                "",
            ]
        )
    return "\n".join(lines)


def _hub_token(hub_url: str | None, env_name: str) -> str | None:
    if hub_url is None:
        return None
    token = os.environ.get(env_name, "")
    if not token:
        raise RuntimeError(f"hub token environment variable is empty: {env_name}")
    return token


async def _execute_operator_action(
    event: EventSpec,
    store: ArenaStore,
    runner: HookRunner,
    action: dict,
) -> tuple[dict, bool]:
    service = event.service(str(action["service"]))
    payload = dict(action.get("payload", {}))
    kind = str(action["kind"])
    if kind == "patch_deploy":
        patch_id = str(payload.get("patch_id", ""))
        patch = store.get_patch(patch_id)
        if patch is None or patch["service"] != service.id:
            raise ValueError("requested patch is missing or belongs to another service")
        if patch["status"] != "verified":
            raise ValueError("requested patch is no longer verified")
        result = await DefenseController(store, runner).deploy(
            service,
            Path(str(patch["candidate_root"])),
            str(patch["revision"]),
        )
        store.mark_patch(
            patch_id,
            result.status,
            {
                "deployment_id": result.deployment_id,
                "operator_action": action["id"],
                "rolled_back": result.rolled_back,
            },
        )
        return (
            {
                "action_id": action["id"],
                "kind": kind,
                "patch_id": patch_id,
                "deployment_id": result.deployment_id,
                "status": result.status,
                "rolled_back": result.rolled_back,
                "checks": [check.packet() for check in result.checks],
            },
            result.status == "active",
        )
    if kind == "rollback":
        deployment = store.active_deployment(service.id)
        if deployment is None:
            raise ValueError("service no longer has an active arena deployment")
        if str(payload.get("deployment_id", "")) != str(deployment["id"]):
            raise ValueError("active deployment changed; request a fresh rollback")
        outcome = await runner.run_service(
            service,
            "rollback",
            {
                "candidate_root": str(deployment["candidate_root"]),
                "revision": str(deployment["revision"]),
            },
        )
        status = "rolled_back_by_operator" if outcome.passed else "rollback_failed"
        store.record_deployment(
            service.id,
            str(deployment["revision"]),
            str(deployment["candidate_root"]),
            status,
            {
                **dict(deployment.get("metadata", {})),
                "operator_action": action["id"],
                "rollback": outcome.packet(),
            },
            deployment_id=str(deployment["id"]),
        )
        store.record_service_event(
            service.id,
            "deployment.operator_rollback",
            {"action_id": action["id"], "outcome": outcome.packet()},
            severity="warning" if outcome.passed else "critical",
        )
        return (
            {
                "action_id": action["id"],
                "kind": kind,
                "status": status,
                "outcome": outcome.packet(),
            },
            outcome.passed,
        )
    if kind == "monitor_check":
        monitor_name = str(payload.get("monitor", ""))
        result = await MonitorEngine(event, store, runner).run_named(
            service.id, monitor_name
        )
        return (
            {
                "action_id": action["id"],
                "kind": kind,
                "monitor": monitor_name,
                "passed": result.outcome.passed,
                "state": result.state,
                "outcome": result.outcome.packet(),
            },
            result.outcome.passed,
        )
    raise ValueError(f"unsupported operator action: {kind}")


def main() -> int:
    args = parser().parse_args()
    if args.command == "doctor":
        return doctor(ArenaConfig.load(args.config))
    if args.command == "event-validate":
        event = EventSpec.load(args.event_file)
        messages = event.validate()
        print(json.dumps({"event": event.id, "services": messages}, indent=2))
        return 0
    config = ArenaConfig.load(args.config)
    if args.command == "demo":
        return asyncio.run(demo(config.project_root))
    state = config.section("state")
    store = ArenaStore(config.project_root / state["database"])
    store.initialize()
    if args.command == "metrics":
        if args.output:
            print(write_prometheus(store, args.output))
        else:
            print(render_prometheus(store), end="")
        return 0
    if args.command == "exploits":
        print(json.dumps(store.list_exploits(args.service), indent=2, sort_keys=True))
        return 0
    if args.command == "exploit-promote":
        store.promote_exploit(args.exploit_id)
        print(json.dumps({"exploit_id": args.exploit_id, "status": "active"}))
        return 0
    if args.command == "patches":
        print(json.dumps(store.list_patches(args.service), indent=2, sort_keys=True))
        return 0
    if args.command == "actions":
        print(
            json.dumps(
                store.list_operator_actions(args.service, status=args.status),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "service-bootstrap":
        report = bootstrap_service(
            args.event_file,
            args.source_root,
            args.service_id,
            name=args.name,
        )
        task_ids: list[str] = []
        if not args.no_enqueue:
            for kind, provider, goal in (
                (
                    TaskKind.SOURCE_MAP,
                    "claude",
                    "Map the newly released service source, trust boundaries, build, "
                    "runtime, data stores, and high-risk entry points.",
                ),
                (
                    TaskKind.CHECKER_MAP,
                    "deepseek",
                    "Map every detected checker action, flag store, saved credential, "
                    "attack-info value, and required availability behavior.",
                ),
            ):
                task = Task(
                    report.service_id,
                    kind,
                    goal,
                    str(report.source_root),
                    expected_value=0.9,
                    deadline_seconds=300,
                    preferred_provider=provider,
                    context={
                        "write_access": False,
                        "service_revision": "bootstrap-unhashed",
                        "verification": {"commands": []},
                    },
                    dedupe_key=f"bootstrap:{report.service_id}:{kind.value}",
                )
                task_id, _ = store.enqueue(task)
                task_ids.append(task_id)
        print(
            json.dumps(
                {
                    "service": report.service_id,
                    "descriptor": str(report.descriptor),
                    "compose_file": report.compose_file,
                    "checker_candidates": list(report.checker_candidates),
                    "languages": list(report.languages),
                    "tasks": task_ids,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "traffic-normalize":
        print(
            json.dumps(
                normalize_file(
                    args.input_file,
                    source_format=args.format,
                    cursor_path=args.cursor,
                    checker_cidrs=tuple(args.checker_cidr),
                    max_detections=args.max_detections,
                ),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "event-http-observe":
        print(json.dumps(HttpEventAdapter.load(args.adapter_file).observe()))
        return 0
    if args.command == "event-http-submit":
        result = HttpEventAdapter.load(args.adapter_file).submit(args.flag)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["accepted"] else 2
    if args.command == "event-http-submit-batch":
        flags = json.loads(args.flags_json)
        if not isinstance(flags, list) or not all(
            isinstance(flag, str) for flag in flags
        ):
            raise ValueError("flags_json must be a JSON string array")
        result = HttpEventAdapter.load(args.adapter_file).submit_many(flags)
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "backup":
        destination = create_backup(
            store.path,
            config.project_root
            / state.get("artifact_root", ".arena/artifacts"),
            args.output,
        )
        print(str(destination))
        return 0
    if args.command == "agents":
        print(
            json.dumps(
                store.task_activity(
                    active_only=not args.include_all, limit=max(1, args.limit)
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "agent-watch":
        return _watch_task_events(
            store,
            args.task_id,
            follow=args.follow,
            interval_seconds=max(0.1, args.interval),
        )
    if args.command == "agent-message":
        family = _configured_provider_family(config, args.provider)
        if store.task_state(args.task_id) in {
            TaskState.ACCEPTED.value,
            TaskState.REJECTED.value,
            TaskState.FAILED.value,
        }:
            print(json.dumps({"error": "task is already terminal"}))
            return 2
        message_id = store.queue_agent_message(
            args.task_id, args.provider, args.message
        )
        print(
            json.dumps(
                {
                    "message_id": message_id,
                    "status": "queued",
                    "provider": args.provider,
                    "delivery": (
                        "live Codex turn steering"
                        if family == "codex"
                        else f"next resumed {family} turn"
                    ),
                }
            )
        )
        return 0
    if args.command in {"cluster-validate", "cluster-plan"}:
        event = EventSpec.load(args.event_file)
        event.validate()
        if args.command == "cluster-validate":
            cluster = ClusterSpec.load(args.cluster_file)
            assignments = cluster.validate(event)
            print(
                json.dumps(
                    {
                        "nodes": cluster.expected_nodes,
                        "services_per_node": cluster.services_per_node,
                        "assignments": assignments,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        node_ids = [item.strip() for item in args.nodes.split(",") if item.strip()]
        plan = round_robin_plan(
            sorted(event.services),
            node_ids,
            services_per_node=args.services_per_node,
        )
        print(_cluster_toml(plan, args.services_per_node), end="")
        return 0
    if args.command in {
        "monitor-once",
        "monitor",
        "traffic-once",
        "traffic",
        "observe-once",
        "observe",
        "defense-deploy",
        "attack-once",
        "flags-submit-once",
        "cells-once",
        "cells",
        "phase-status",
        "phase-grace",
        "phase-live",
        "phase-freeze",
        "phase-end",
        "tui",
        "patch-promote",
        "action-execute",
        "hub-serve",
    }:
        event = EventSpec.load(args.event_file)
        event.validate()
        runner = HookRunner(build_scope(config, event))
        phase_controller = PhaseController(
            event, store, PhasePolicy.from_config(config)
        )
        if args.command == "hub-serve":
            cluster = ClusterSpec.load(args.cluster_file)
            token = os.environ.get(args.token_env, "")
            if not token:
                raise RuntimeError(
                    f"hub token environment variable is empty: {args.token_env}"
                )
            CoordinatorHub(phase_controller, store, cluster, token).serve(
                args.bind, args.port
            )
            return 0
        if args.command == "phase-status":
            print(phase_json(phase_controller))
            return 0
        if args.command == "phase-grace":
            print(
                json.dumps(
                    phase_controller.start_grace(duration_seconds=args.duration),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "phase-live":
            print(
                json.dumps(
                    phase_controller.start_live(), indent=2, sort_keys=True
                )
            )
            return 0
        if args.command == "phase-freeze":
            print(
                json.dumps(phase_controller.freeze(), indent=2, sort_keys=True)
            )
            return 0
        if args.command == "phase-end":
            print(json.dumps(phase_controller.end(), indent=2, sort_keys=True))
            return 0
        if args.command == "tui":
            node = _selected_node(event, args.cluster, args.node)
            phase_controller = PhaseController(
                event,
                store,
                PhasePolicy.from_config(config),
                service_ids=None if node is None else set(node.services),
            )
            run_dashboard(
                phase_controller,
                store,
                node=node,
                read_only=args.read_only,
            )
            return 0
        if args.command == "patch-promote":
            patch = store.get_patch(args.patch_id)
            if patch is None:
                raise KeyError(f"unknown patch: {args.patch_id}")
            service = event.service(str(patch["service"]))
            result = asyncio.run(
                DefenseController(store, runner).deploy(
                    service,
                    Path(str(patch["candidate_root"])),
                    str(patch["revision"]),
                )
            )
            store.mark_patch(
                args.patch_id,
                result.status,
                {
                    "deployment_id": result.deployment_id,
                    "rolled_back": result.rolled_back,
                },
            )
            print(
                json.dumps(
                    {
                        "patch_id": args.patch_id,
                        "deployment_id": result.deployment_id,
                        "status": result.status,
                        "rolled_back": result.rolled_back,
                    },
                    indent=2,
                )
            )
            return 0 if result.status == "active" else 2
        if args.command == "action-execute":
            action = store.get_operator_action(args.action_id)
            if action is None:
                raise KeyError(f"unknown operator action: {args.action_id}")
            if action["status"] != "requested":
                raise ValueError(
                    f"operator action is not pending: {action['status']}"
                )
            store.finish_operator_action(args.action_id, "executing", {})
            try:
                result_payload, passed = asyncio.run(
                    _execute_operator_action(event, store, runner, action)
                )
            except Exception as error:
                store.finish_operator_action(
                    args.action_id, "failed", {"error": str(error)[-4000:]}
                )
                raise
            store.finish_operator_action(
                args.action_id,
                "completed" if passed else "failed",
                result_payload,
            )
            print(json.dumps(result_payload, indent=2, sort_keys=True))
            return 0 if passed else 2
        if args.command == "observe-once":
            result = asyncio.run(EventObserver(event, store, runner).poll_once())
            print(
                json.dumps(
                    None
                    if result is None
                    else {
                        "tick": result.tick,
                        "changed": result.changed,
                        "payload": result.payload,
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "observe":
            try:
                asyncio.run(EventObserver(event, store, runner).run_forever())
            except KeyboardInterrupt:
                return 130
            return 0
        if args.command == "flags-submit-once":
            outcomes = asyncio.run(FlagBroker(event, store, runner).submit_once())
            print(
                json.dumps(
                    {
                        "attempted": len(outcomes),
                        "accepted": sum(item.passed for item in outcomes),
                    },
                    indent=2,
                )
            )
            return 0 if all(item.passed for item in outcomes) else 2
        if args.command == "cells-once":
            node = _selected_node(event, args.cluster, args.node)
            if args.hub and node is None:
                raise ValueError("--hub requires --cluster and --node")
            results = asyncio.run(
                _run_cells_once(
                    build_cell_supervisor(
                        config,
                        event,
                        service_ids=None if node is None else set(node.services),
                        node=node,
                        hub_url=args.hub,
                        hub_token=_hub_token(args.hub, args.hub_token_env),
                    )
                )
            )
            print(
                json.dumps(
                    [
                        {
                            "service": item.service,
                            "monitors": len(item.monitors),
                            "traffic_detections": sum(
                                len(result.detections) for result in item.traffic
                            ),
                            "traffic_tasks": sum(
                                result.new_tasks for result in item.traffic
                            ),
                            "processed_task": item.processed_task,
                            "attacks": len(item.attacks),
                        }
                        for item in results
                    ],
                    indent=2,
                )
            )
            return 0
        if args.command == "cells":
            node = _selected_node(event, args.cluster, args.node)
            if args.hub and node is None:
                raise ValueError("--hub requires --cluster and --node")
            try:
                asyncio.run(
                    _run_cells_forever(
                        build_cell_supervisor(
                            config,
                            event,
                            service_ids=None if node is None else set(node.services),
                            node=node,
                            hub_url=args.hub,
                            hub_token=_hub_token(args.hub, args.hub_token_env),
                        )
                    )
                )
            except KeyboardInterrupt:
                return 130
            return 0
        if args.command == "monitor-once":
            results = asyncio.run(
                MonitorEngine(event, store, runner).run_once(services=args.services)
            )
            print(
                json.dumps(
                    [
                        {
                            "service": item.service,
                            "monitor": item.monitor,
                            "passed": item.outcome.passed,
                            "state": item.state,
                            "task_id": item.task_id,
                        }
                        for item in results
                    ],
                    indent=2,
                )
            )
            return 0 if all(item.outcome.passed for item in results) else 2
        if args.command == "monitor":
            try:
                asyncio.run(
                    MonitorEngine(event, store, runner).run_forever(
                        services=args.services
                    )
                )
            except KeyboardInterrupt:
                return 130
            return 0
        if args.command == "traffic-once":
            results = asyncio.run(
                TrafficEngine(event, store, runner).run_once(services=args.services)
            )
            print(
                json.dumps(
                    [
                        {
                            "service": item.service,
                            "collector_passed": item.outcome.passed,
                            "detections": [
                                {
                                    "fingerprint": detection.fingerprint,
                                    "summary": detection.summary,
                                    "score": detection.score,
                                }
                                for detection in item.detections
                            ],
                            "task_ids": list(item.task_ids),
                            "new_tasks": item.new_tasks,
                            "rejected": item.rejected,
                        }
                        for item in results
                    ],
                    indent=2,
                )
            )
            return 0 if all(item.outcome.passed for item in results) else 2
        if args.command == "traffic":
            try:
                asyncio.run(
                    TrafficEngine(event, store, runner).run_forever(
                        services=args.services
                    )
                )
            except KeyboardInterrupt:
                return 130
            return 0
        if args.command == "defense-deploy":
            result = asyncio.run(
                DefenseController(store, runner).deploy(
                    event.service(args.service),
                    Path(args.candidate).resolve(),
                    args.revision,
                )
            )
            print(
                json.dumps(
                    {
                        "deployment_id": result.deployment_id,
                        "status": result.status,
                        "rolled_back": result.rolled_back,
                        "checks": [check.packet() for check in result.checks],
                    },
                    indent=2,
                )
            )
            return 0 if result.status == "active" else 2
        if args.command == "attack-once":
            result = asyncio.run(
                OffenseController(event, store, runner).attack(
                    event.service(args.service),
                    args.target,
                    submit=not args.no_submit,
                )
            )
            print(
                json.dumps(
                    {
                        "service": result.service,
                        "target": result.target,
                        "passed": result.outcome.passed,
                        "flags_found": result.flags_found,
                        "flags_new": result.flags_new,
                        "submissions": len(result.submissions),
                    },
                    indent=2,
                )
            )
            return 0 if result.outcome.passed else 2
    if args.command == "init":
        print(f"initialized {store.path}")
        return 0
    if args.command == "enqueue":
        data = json.loads(Path(args.task_file).read_text())
        task = Task.from_dict(data)
        task.workdir = str((config.project_root / task.workdir).resolve())
        task_id, created = store.enqueue(task)
        print(json.dumps({"task_id": task_id, "created": created}))
        return 0
    if args.command == "status":
        output = {
            "tasks": store.status_counts(),
            "recent_results": store.recent_results(),
        }
        if args.event:
            event = EventSpec.load(args.event)
            output["services"] = [
                store.service_snapshot(service_id)
                for service_id in sorted(event.services)
            ]
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    if args.command == "run-once":
        event = EventSpec.load(args.event) if args.event else None
        return (
            0
            if asyncio.run(_run_orchestrator_once(build_orchestrator(config, event)))
            else 2
        )
    if args.command == "run":
        processed = asyncio.run(
            _run_orchestrator_pool(
                build_orchestrator(
                    config, EventSpec.load(args.event) if args.event else None
                ),
                args.workers,
                args.idle_exit_seconds,
            )
        )
        print(json.dumps({"processed": processed}))
        return 0
    raise AssertionError(args.command)


def _watch_task_events(
    store: ArenaStore,
    task_id: str,
    *,
    follow: bool,
    interval_seconds: float,
) -> int:
    last_id = 0
    terminal = {
        TaskState.ACCEPTED.value,
        TaskState.REJECTED.value,
        TaskState.FAILED.value,
    }
    while True:
        events = store.recent_task_events(task_id, after_id=last_id)
        for event in events:
            print(json.dumps(event, sort_keys=True), flush=True)
            last_id = max(last_id, int(event["id"]))
        state = store.task_state(task_id)
        if state is None:
            print(json.dumps({"error": f"unknown task: {task_id}"}))
            return 2
        if not follow or (state in terminal and not events):
            return 0
        time.sleep(interval_seconds)


async def _run_orchestrator_once(orchestrator: Orchestrator) -> bool:
    try:
        return await orchestrator.run_once()
    finally:
        await orchestrator.close()


async def _run_orchestrator_pool(
    orchestrator: Orchestrator, workers: int, idle_exit_seconds: float
) -> int:
    try:
        return await orchestrator.run_pool(
            workers=workers, idle_exit_seconds=idle_exit_seconds
        )
    finally:
        await orchestrator.close()


async def _run_cells_once(supervisor: CellSupervisor):
    try:
        return await supervisor.run_once()
    finally:
        await supervisor.close()


async def _run_cells_forever(supervisor: CellSupervisor) -> None:
    try:
        await supervisor.run_forever()
    finally:
        await supervisor.close()


if __name__ == "__main__":
    raise SystemExit(main())
