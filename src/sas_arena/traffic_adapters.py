from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
from typing import Any


def normalize_file(
    input_path: str | Path,
    *,
    source_format: str,
    cursor_path: str | Path | None = None,
    checker_cidrs: tuple[str, ...] = (),
    max_detections: int = 50,
) -> dict[str, Any]:
    source = Path(input_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    cursor = None if cursor_path is None else Path(cursor_path).resolve()
    offset = _load_offset(cursor, source)
    networks = tuple(ipaddress.ip_network(value) for value in checker_cidrs)
    detections: dict[str, dict[str, Any]] = {}
    with source.open("r", errors="replace") as handle:
        handle.seek(offset)
        line_number = 0
        while len(detections) < max_detections:
            line = handle.readline()
            if not line:
                break
            line_number += 1
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            detection = (
                _suricata(value, source, line_number, networks)
                if source_format == "suricata"
                else _generic(value, source, line_number, networks)
            )
            if detection is None:
                continue
            fingerprint = str(detection["fingerprint"])
            existing = detections.get(fingerprint)
            if existing is None:
                detections[fingerprint] = detection
            else:
                existing["count"] = int(existing.get("count", 1)) + 1
                existing["last_seen"] = detection.get("timestamp")
        new_offset = handle.tell()
    if cursor is not None:
        cursor.parent.mkdir(parents=True, exist_ok=True)
        temporary = cursor.with_suffix(cursor.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"path": str(source), "offset": new_offset}) + "\n"
        )
        os.replace(temporary, cursor)
    return {"detections": list(detections.values())}


def _suricata(
    value: dict[str, Any],
    source: Path,
    line_number: int,
    checker_networks: tuple[ipaddress._BaseNetwork, ...],
) -> dict[str, Any] | None:
    if _is_checker(value.get("src_ip"), checker_networks):
        return None
    event_type = str(value.get("event_type", ""))
    alert = value.get("alert")
    if event_type != "alert" or not isinstance(alert, dict):
        return None
    signature = str(alert.get("signature") or "Suricata alert")
    severity = int(alert.get("severity", 3))
    score = {1: 0.99, 2: 0.92, 3: 0.82}.get(severity, 0.75)
    protocol = str(value.get("app_proto") or value.get("proto") or "unknown")
    destination_port = value.get("dest_port")
    http = value.get("http") if isinstance(value.get("http"), dict) else {}
    path = str(http.get("url") or "")
    signature_id = str(alert.get("signature_id") or signature)
    fingerprint = _fingerprint(signature_id, protocol, destination_port, path)
    return {
        "fingerprint": fingerprint,
        "summary": signature[:1000],
        "score": score,
        "source": "suricata",
        "src_ip": value.get("src_ip"),
        "src_port": value.get("src_port"),
        "dest_ip": value.get("dest_ip"),
        "dest_port": destination_port,
        "protocol": protocol,
        "http_method": http.get("http_method"),
        "http_path": path,
        "timestamp": value.get("timestamp"),
        "count": 1,
        "artifact": f"{source}:{line_number}",
    }


def _generic(
    value: dict[str, Any],
    source: Path,
    line_number: int,
    checker_networks: tuple[ipaddress._BaseNetwork, ...],
) -> dict[str, Any] | None:
    if _is_checker(value.get("src_ip"), checker_networks):
        return None
    summary = value.get("summary")
    try:
        score = float(value.get("score", 0))
    except (TypeError, ValueError):
        return None
    if not isinstance(summary, str) or not summary.strip() or not 0 <= score <= 1:
        return None
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        fingerprint = _fingerprint(
            summary,
            value.get("protocol"),
            value.get("dest_port"),
            value.get("path"),
        )
    return {
        **{
            str(key)[:128]: item
            for key, item in list(value.items())[:100]
            if key not in {"reasoning", "thinking"}
        },
        "fingerprint": str(fingerprint)[:256],
        "summary": summary.strip()[:1000],
        "score": score,
        "artifact": str(value.get("artifact") or f"{source}:{line_number}"),
    }


def _load_offset(cursor: Path | None, source: Path) -> int:
    if cursor is None or not cursor.is_file():
        return 0
    try:
        value = json.loads(cursor.read_text())
        if value.get("path") != str(source):
            return 0
        offset = int(value.get("offset", 0))
        return offset if 0 <= offset <= source.stat().st_size else 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _is_checker(
    address: object, networks: tuple[ipaddress._BaseNetwork, ...]
) -> bool:
    if not isinstance(address, str) or not networks:
        return False
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def _fingerprint(*values: object) -> str:
    canonical = "|".join(str(value or "") for value in values)
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]
