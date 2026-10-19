from __future__ import annotations

import json
import os
import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    service_id: str
    source_root: Path
    descriptor: Path
    compose_file: str | None
    checker_candidates: tuple[str, ...]
    languages: tuple[str, ...]


def bootstrap_service(
    event_file: str | Path,
    source_root: str | Path,
    service_id: str,
    *,
    name: str | None = None,
) -> BootstrapReport:
    event_file = Path(event_file).resolve()
    source_root = Path(source_root).resolve()
    if not event_file.is_file():
        raise FileNotFoundError(event_file)
    if not source_root.is_dir():
        raise NotADirectoryError(source_root)
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", service_id) is None:
        raise ValueError("service id must use lowercase letters, digits, _ or -")

    compose = _first_existing(
        source_root,
        ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"),
    )
    checker_candidates = tuple(_checker_candidates(source_root)[:20])
    languages = tuple(_languages(source_root))
    service_dir = event_file.parent / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    descriptor = service_dir / f"{service_id}.toml"
    if descriptor.exists():
        raise FileExistsError(descriptor)

    generated = event_file.parent / ".arena-generated" / service_id
    generated.mkdir(parents=True, exist_ok=True)
    gate = generated / "unconfigured_gate.py"
    gate.write_text(
        "import sys\n"
        "print('bootstrap gate is not configured:', sys.argv[1], file=sys.stderr)\n"
        "raise SystemExit(2)\n"
    )
    traffic = generated / "traffic_adapter.py"
    traffic.write_text("import json\nprint(json.dumps({'detections': []}))\n")

    root_relative = os.path.relpath(source_root, service_dir)
    gate_relative = os.path.relpath(gate, source_root)
    traffic_relative = os.path.relpath(traffic, source_root)
    health_argv = (
        ["docker", "compose", "-f", str(compose.name), "ps", "--services", "--status", "running"]
        if compose is not None
        else ["python3", "-c", "from pathlib import Path; assert Path('.').is_dir()"]
    )
    lines = [
        "schema_version = 1",
        f"id = {json.dumps(service_id)}",
        f"name = {json.dumps(name or service_id)}",
        f"root = {json.dumps(root_relative)}",
        f"tags = {json.dumps(list(languages))}",
        "",
        "[targets]",
        "hosts = []",
        "",
        "[context]",
        'revision = "bootstrap-unhashed"',
        f"checker_candidates = {json.dumps(list(checker_candidates))}",
        f"compose_file = {json.dumps('' if compose is None else compose.name)}",
        "",
        "[cell]",
        "workers = 10",
        "provider_instances = []",
        "offense_enabled = false",
        "",
        "[hooks.health]",
        f"argv = {json.dumps(health_argv)}",
        "timeout_seconds = 15",
        "",
    ]
    if compose is not None:
        lines.extend(
            [
                "[hooks.build]",
                f"argv = {json.dumps(['docker', 'compose', '-f', compose.name, 'build'])}",
                "timeout_seconds = 900",
                "",
                "[hooks.start]",
                f"argv = {json.dumps(['docker', 'compose', '-f', compose.name, 'up', '-d'])}",
                "timeout_seconds = 300",
                "",
            ]
        )
    for hook, timeout in (
        ("checker", 120),
        ("verify_exploit", 300),
        ("verify_patch", 600),
        ("deploy", 300),
        ("rollback", 300),
    ):
        lines.extend(
            [
                f"[hooks.{hook}]",
                f"argv = {json.dumps(['python3', gate_relative, hook])}",
                f"timeout_seconds = {timeout}",
                "",
            ]
        )
    lines.extend(
        [
            "[hooks.traffic]",
            f"argv = {json.dumps(['python3', traffic_relative])}",
            "timeout_seconds = 5",
            "",
            "[monitors.availability]",
            'hook = "health"',
            "interval_seconds = 5",
            "failure_threshold = 2",
            "recovery_threshold = 1",
            'task_kind = "incident_response"',
            'lane = "defense"',
            'risk = "critical"',
            "auto_rollback = false",
            "rollback_window_seconds = 300",
            "",
            "[traffic]",
            'hook = "traffic"',
            "interval_seconds = 2",
            "min_score = 0.7",
            "max_detections = 50",
            "dedupe_window_seconds = 300",
            "deadline_seconds = 300",
            'risk = "high"',
            "",
        ]
    )
    descriptor.write_text("\n".join(lines))
    _append_service(event_file, descriptor)
    report = BootstrapReport(
        service_id,
        source_root,
        descriptor,
        None if compose is None else compose.name,
        checker_candidates,
        languages,
    )
    (generated / "bootstrap-report.json").write_text(
        json.dumps(
            {
                "service_id": report.service_id,
                "source_root": str(report.source_root),
                "descriptor": str(report.descriptor),
                "compose_file": report.compose_file,
                "checker_candidates": list(report.checker_candidates),
                "languages": list(report.languages),
                "required_manual_gates": [
                    "checker",
                    "verify_exploit",
                    "verify_patch",
                    "deploy",
                    "rollback",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return report


def _append_service(event_file: Path, descriptor: Path) -> None:
    text = event_file.read_text()
    with event_file.open("rb") as handle:
        value = tomllib.load(handle)
    services = value.get("services", [])
    if not isinstance(services, list) or not all(isinstance(item, str) for item in services):
        raise ValueError("event services must be a string array")
    relative = os.path.relpath(descriptor, event_file.parent)
    if relative in services:
        return
    updated = services + [relative]
    replacement = "services = " + json.dumps(updated)
    if re.search(r"(?m)^services\s*=.*$", text):
        text = re.sub(r"(?m)^services\s*=.*$", replacement, text, count=1)
    else:
        text += "\n" + replacement + "\n"
    event_file.write_text(text)


def _checker_candidates(root: Path) -> list[str]:
    values = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.casefold()
        if "checker" in name or "checklib" in name:
            values.append(str(path.relative_to(root)))
    return sorted(values, key=lambda value: (value.count("/"), value))


def _languages(root: Path) -> list[str]:
    names = {
        ".py": "python",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "c",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".hs": "haskell",
        ".js": "javascript",
        ".ts": "typescript",
        ".php": "php",
        ".rb": "ruby",
    }
    counts = Counter(
        names[path.suffix.casefold()]
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in names
    )
    return [name for name, _ in counts.most_common(8)]


def _first_existing(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None
