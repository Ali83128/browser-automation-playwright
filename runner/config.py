"""Load and validate task files (YAML)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

STEP_TYPES = {
    "goto", "fill", "click", "select", "check", "uncheck", "upload", "wait_for",
    "expect_text", "extract", "extract_all", "download", "screenshot",
}

_TEMPLATE = re.compile(r"\{\{\s*(row|env)\.([A-Za-z0-9_]+)(?:\|([^}]*))?\s*\}\}")


class TaskError(ValueError):
    """Raised when a task file is invalid."""


@dataclass
class LoginSpec:
    check_url: str
    logged_in_selector: str
    steps: list[dict]


@dataclass
class Task:
    name: str
    base_url: str
    steps: list[dict]
    path: Path
    login: LoginSpec | None = None
    data: Path | None = None
    output: Path | None = None
    storage_state: Path | None = None
    retries: int = 2
    rate_limit_seconds: float = 0.5
    timeout_ms: int = 15000
    extra: dict[str, Any] = field(default_factory=dict)


def render(value: Any, row: dict | None = None) -> Any:
    """Replace {{row.col}} and {{env.NAME|default}} placeholders inside strings (recursively)."""
    if isinstance(value, dict):
        return {k: render(v, row) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, row) for v in value]
    if not isinstance(value, str):
        return value

    def sub(m: re.Match) -> str:
        scope, key, default = m.group(1), m.group(2), m.group(3)
        if scope == "row":
            if row is None or key not in row:
                raise TaskError(f"Placeholder row.{key} has no matching CSV column")
            return str(row[key])
        got = os.environ.get(key)
        if got is None:
            if default is None:
                raise TaskError(f"Environment variable {key} is not set and has no default")
            return default
        return got

    return _TEMPLATE.sub(sub, value)


def _check_steps(steps: Any, where: str) -> list[dict]:
    if not isinstance(steps, list) or not steps:
        raise TaskError(f"{where}: 'steps' must be a non-empty list")
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or len(step) != 1:
            raise TaskError(f"{where} step {i}: each step must be a single-key mapping, e.g. '- click: \"#save\"'")
        kind = next(iter(step))
        if kind not in STEP_TYPES:
            raise TaskError(f"{where} step {i}: unknown step '{kind}'. Allowed: {', '.join(sorted(STEP_TYPES))}")
    return steps


def load_task(path: str | Path, base_url: str | None = None) -> Task:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaskError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise TaskError(f"{path}: top level must be a mapping")
    for key in ("name", "base_url", "steps"):
        if key not in raw:
            raise TaskError(f"{path}: missing required key '{key}'")

    root = path.resolve().parent.parent  # tasks/ lives one level below the project root
    def rel(p: str | None) -> Path | None:
        return (root / p) if p else None

    login = None
    if raw.get("login"):
        lg = raw["login"]
        for key in ("check_url", "logged_in_selector", "steps"):
            if key not in lg:
                raise TaskError(f"{path}: login block missing '{key}'")
        login = LoginSpec(lg["check_url"], lg["logged_in_selector"], _check_steps(lg["steps"], "login"))

    task = Task(
        name=str(raw["name"]),
        base_url=(base_url or raw["base_url"]).rstrip("/"),
        steps=_check_steps(raw["steps"], "task"),
        path=path,
        login=login,
        data=rel(raw.get("data")),
        output=rel(raw.get("output")),
        storage_state=rel(raw.get("storage_state")),
        retries=int(raw.get("retries", 2)),
        rate_limit_seconds=float(raw.get("rate_limit_seconds", 0.5)),
        timeout_ms=int(raw.get("timeout_ms", 15000)),
    )
    if task.retries < 0 or task.rate_limit_seconds < 0:
        raise TaskError(f"{path}: retries and rate_limit_seconds must be >= 0")
    if task.data and not task.data.exists():
        raise TaskError(f"{path}: data file not found: {task.data}")
    return task
