"""Run a task: one browser session, optional login with saved session, one pass per CSV row."""
from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, sync_playwright

from .config import Task, render
from .steps import REGISTRY, Context, StepFailed

log = logging.getLogger("runner")


@dataclass
class RowResult:
    index: int
    status: str
    attempts: int
    duration_ms: int
    captured: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class RunSummary:
    task: str
    total: int
    ok: int
    failed: int
    retried: int
    seconds: float
    results_csv: Path | None
    logged_in_fresh: bool
    log_file: Path | None


def setup_logging(root: Path, task_name: str, verbose: bool = False) -> Path:
    logs = root / "output" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_file = logs / f"{task_name}_{datetime.now():%Y%m%d_%H%M%S}.log"
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    logger = logging.getLogger("runner")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in (logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.propagate = False
    return log_file


def _read_rows(task: Task, limit: int | None) -> list[dict | None]:
    if not task.data:
        return [None]
    with task.data.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[:limit] if limit else rows


def _run_steps(page, steps: list[dict], ctx: Context, row: dict | None) -> None:
    for step in steps:
        kind, spec = next(iter(step.items()))
        REGISTRY[kind](page, render(spec, row), ctx)


def _is_logged_in(page, task: Task, ctx: Context) -> bool:
    page.goto(f"{task.base_url}/{task.login.check_url.lstrip('/')}", wait_until="domcontentloaded")
    try:
        page.locator(task.login.logged_in_selector).first.wait_for(state="visible", timeout=3000)
        return True
    except PlaywrightError:
        return False


def _open_context(browser: Browser, task: Task, ctx: Context) -> tuple[BrowserContext, bool]:
    """Reuse a saved session when it is still valid; otherwise log in and save it. Returns (context, logged_in_fresh)."""
    kwargs = {"viewport": {"width": 1440, "height": 900}, "accept_downloads": True}
    if task.login and task.storage_state and task.storage_state.exists():
        context = browser.new_context(storage_state=str(task.storage_state), **kwargs)
        context.set_default_timeout(task.timeout_ms)
        page = context.new_page()
        if _is_logged_in(page, task, ctx):
            log.info("session  reused saved login (%s)", task.storage_state.name)
            page.close()
            return context, False
        log.info("session  saved login expired, signing in again")
        context.close()

    context = browser.new_context(**kwargs)
    context.set_default_timeout(task.timeout_ms)
    if not task.login:
        return context, False
    page = context.new_page()
    _run_steps(page, task.login.steps, ctx, None)
    page.locator(task.login.logged_in_selector).first.wait_for(state="visible", timeout=task.timeout_ms)
    if task.storage_state:
        task.storage_state.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(task.storage_state))
    log.info("login    signed in, session saved for next run")
    page.close()
    return context, True


def run_task(task: Task, headless: bool = True, limit: int | None = None, slow_mo: int = 0,
             root: Path | None = None) -> RunSummary:
    root = root or task.path.resolve().parent.parent
    rows = _read_rows(task, limit)
    started = time.monotonic()
    results: list[RowResult] = []
    shots = root / "output" / "failures"
    log_file = next((Path(h.baseFilename) for h in log.handlers if isinstance(h, logging.FileHandler)), None)

    log.info("start    task=%s rows=%d retries=%d delay=%.2fs headless=%s",
             task.name, len(rows) if task.data else 1, task.retries, task.rate_limit_seconds, headless)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo)
        base_ctx = Context(base_url=task.base_url, root=root, timeout_ms=task.timeout_ms,
                           log=lambda m: log.info("extract  %s", m))
        context, fresh = _open_context(browser, task, base_ctx)
        page = context.new_page()

        for i, row in enumerate(rows, 1):
            t0 = time.monotonic()
            attempt, last_error = 0, ""
            while attempt <= task.retries:
                attempt += 1
                ctx = Context(base_url=task.base_url, root=root, timeout_ms=task.timeout_ms, log=base_ctx.log)
                try:
                    _run_steps(page, task.steps, ctx, row)
                    label = (row or {}).get("full_name") or (row or {}).get("email") or task.name
                    extra = "  ".join(f"{k}={v}" for k, v in ctx.captured.items())
                    log.info("row %2d   ok    %-22s %s%s", i, label[:22], extra,
                             f"  (attempt {attempt})" if attempt > 1 else "")
                    results.append(RowResult(i, "ok", attempt, int((time.monotonic() - t0) * 1000), ctx.captured))
                    break
                except (StepFailed, PlaywrightError) as exc:
                    last_error = str(exc).splitlines()[0][:200]
                    shots.mkdir(parents=True, exist_ok=True)
                    shot = shots / f"{task.name}_row{i:03d}_attempt{attempt}.png"
                    try:
                        page.screenshot(path=str(shot))
                    except PlaywrightError:
                        pass
                    if attempt <= task.retries:
                        backoff = min(2.0 * attempt, 6.0) if task.rate_limit_seconds else 0
                        log.warning("row %2d   retry attempt %d failed: %s (screenshot %s, waiting %.1fs)",
                                    i, attempt, last_error, shot.name, backoff)
                        time.sleep(backoff)
                    else:
                        log.error("row %2d   FAILED after %d attempts: %s", i, attempt, last_error)
                        results.append(RowResult(i, "failed", attempt, int((time.monotonic() - t0) * 1000),
                                                 ctx.captured, last_error))
            if i < len(rows) and task.rate_limit_seconds:
                time.sleep(task.rate_limit_seconds)

        context.close()
        browser.close()

    results_csv = None
    if task.output:
        task.output.parent.mkdir(parents=True, exist_ok=True)
        keys: list[str] = []
        for r in results:
            keys += [k for k in r.captured if k not in keys]
        with task.output.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["row", "status", "attempts", "duration_ms", *keys, "error"])
            for r in results:
                w.writerow([r.index, r.status, r.attempts, r.duration_ms, *[r.captured.get(k, "") for k in keys], r.error])
        results_csv = task.output

    ok = sum(r.status == "ok" for r in results)
    summary = RunSummary(task.name, len(results), ok, len(results) - ok, sum(r.attempts > 1 for r in results),
                         round(time.monotonic() - started, 1), results_csv, fresh, log_file)
    log.info("done     %d/%d ok, %d failed, %d needed a retry, %.1fs%s", summary.ok, summary.total,
             summary.failed, summary.retried, summary.seconds,
             f"  -> {results_csv.relative_to(root)}" if results_csv else "")
    return summary
