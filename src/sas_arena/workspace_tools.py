from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class WorkspaceTools:
    """Small, bounded filesystem toolset for API models in an isolated workspace."""

    def __init__(
        self,
        root: Path,
        *,
        write_access: bool = False,
        max_read_chars: int = 32_000,
    ) -> None:
        self.root = root.resolve()
        self.write_access = write_access
        self.max_read_chars = max_read_chars

    def definitions(self) -> list[dict[str, Any]]:
        tools = [
            _tool(
                "list_files",
                "List files under the isolated workspace using a glob.",
                {
                    "pattern": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                ["pattern"],
            ),
            _tool(
                "read_file",
                "Read a bounded line range from a UTF-8 text file.",
                {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                ["path"],
            ),
            _tool(
                "search_text",
                "Search text files for a literal string and return bounded line matches.",
                {
                    "query": {"type": "string"},
                    "pattern": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "case_sensitive": {"type": "boolean"},
                },
                ["query"],
            ),
        ]
        if self.write_access:
            tools.extend(
                [
                    _tool(
                        "replace_text",
                        "Replace one exact text occurrence in an existing workspace file.",
                        {
                            "path": {"type": "string"},
                            "old": {"type": "string"},
                            "new": {"type": "string"},
                        },
                        ["path", "old", "new"],
                    ),
                    _tool(
                        "write_new_file",
                        "Create a new text file; refuses to overwrite an existing path.",
                        {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        ["path", "content"],
                    ),
                ]
            )
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            method = getattr(self, f"_call_{name}")
        except AttributeError:
            return _result(False, error=f"unknown tool: {name}")
        try:
            return _result(True, data=method(arguments))
        except (KeyError, OSError, TypeError, UnicodeError, ValueError) as error:
            return _result(False, error=str(error))

    def _call_list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments.get("pattern", "**/*"))
        _safe_pattern(pattern)
        limit = min(max(int(arguments.get("limit", 100)), 1), 200)
        paths: list[str] = []
        for path in sorted(self.root.glob(pattern)):
            if (
                not path.is_file()
                or not _inside(path, self.root)
                or _hidden_runtime_path(path, self.root)
            ):
                continue
            paths.append(str(path.relative_to(self.root)))
            if len(paths) >= limit:
                break
        return {"files": paths, "truncated": len(paths) >= limit}

    def _call_read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._path(str(arguments["path"]))
        if path.stat().st_size > 1_000_000:
            raise ValueError("file exceeds the 1 MB read limit")
        lines = path.read_text(errors="replace").splitlines()
        start = max(int(arguments.get("start_line", 1)), 1)
        end = min(int(arguments.get("end_line", start + 399)), start + 399)
        selected = "\n".join(lines[start - 1 : end])
        return {
            "path": str(path.relative_to(self.root)),
            "start_line": start,
            "end_line": min(end, len(lines)),
            "content": selected[: self.max_read_chars],
            "truncated": len(selected) > self.max_read_chars or end < len(lines),
        }

    def _call_search_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments["query"])
        if not query or len(query) > 500:
            raise ValueError("query must contain 1 to 500 characters")
        pattern = str(arguments.get("pattern", "**/*"))
        _safe_pattern(pattern)
        limit = min(max(int(arguments.get("limit", 50)), 1), 100)
        case_sensitive = bool(arguments.get("case_sensitive", True))
        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        for path in sorted(self.root.glob(pattern)):
            if (
                not path.is_file()
                or not _inside(path, self.root)
                or _hidden_runtime_path(path, self.root)
                or path.stat().st_size > 1_000_000
            ):
                continue
            try:
                lines = path.read_text(errors="strict").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                haystack = line if case_sensitive else line.casefold()
                if needle in haystack:
                    matches.append(
                        {
                            "path": str(path.relative_to(self.root)),
                            "line": number,
                            "text": line[:1000],
                        }
                    )
                    if len(matches) >= limit:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def _call_replace_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._require_write()
        path = self._path(str(arguments["path"]))
        old = str(arguments["old"])
        new = str(arguments["new"])
        if not old:
            raise ValueError("old text must not be empty")
        if len(new) > 100_000:
            raise ValueError("replacement exceeds 100 KB")
        content = path.read_text()
        count = content.count(old)
        if count != 1:
            raise ValueError(f"old text must occur exactly once; found {count}")
        path.write_text(content.replace(old, new, 1))
        return {"path": str(path.relative_to(self.root)), "replacements": 1}

    def _call_write_new_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._require_write()
        path = self._path(str(arguments["path"]), must_exist=False)
        content = str(arguments["content"])
        if path.exists():
            raise ValueError("path already exists")
        if len(content) > 100_000:
            raise ValueError("new file exceeds 100 KB")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return {
            "path": str(path.relative_to(self.root)),
            "bytes": len(content.encode()),
        }

    def _path(self, relative: str, *, must_exist: bool = True) -> Path:
        requested = Path(relative)
        if requested.is_absolute():
            raise ValueError("absolute paths are not allowed")
        path = (self.root / requested).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("path escapes the workspace")
        if must_exist and not path.is_file():
            raise ValueError("path is not an existing file")
        return path

    def _require_write(self) -> None:
        if not self.write_access:
            raise PermissionError("this task has no write access")


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            },
        },
    }


def _result(ok: bool, **payload: Any) -> str:
    return json.dumps({"ok": ok, **payload}, sort_keys=True)


def _safe_pattern(pattern: str) -> None:
    path = Path(pattern)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("glob must stay inside the workspace")


def _hidden_runtime_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return any(part in {".git", ".arena", ".venv"} for part in relative.parts)


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved == root or root in resolved.parents
