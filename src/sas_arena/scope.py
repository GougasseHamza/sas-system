from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from pathlib import Path


@dataclass(slots=True)
class ScopePolicy:
    allowed_roots: tuple[Path, ...]
    network_enabled: bool = False
    allowed_cidrs: tuple[str, ...] = ()

    @classmethod
    def from_values(
        cls,
        project_root: Path,
        roots: list[str],
        network_enabled: bool,
        cidrs: list[str],
    ) -> ScopePolicy:
        resolved = tuple((project_root / root).resolve() for root in roots)
        return cls(resolved, network_enabled, tuple(cidrs))

    def require_workdir(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if not any(
            resolved == root or root in resolved.parents for root in self.allowed_roots
        ):
            raise PermissionError(
                f"workdir is outside the authorized roots: {resolved}"
            )
        return resolved

    def require_target(self, host: str) -> None:
        if not self.network_enabled:
            raise PermissionError(
                "network execution is disabled by the engagement scope"
            )
        address = ip_address(host)
        if not any(address in ip_network(cidr) for cidr in self.allowed_cidrs):
            raise PermissionError(f"target is outside the authorized CIDRs: {host}")
