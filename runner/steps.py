"""Step implementations. Each step gets the page, its (already rendered) spec and the run context."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Page

TRUTHY = {"1", "true", "yes", "y", "on"}


class StepFailed(RuntimeError):
    """A step ran but the page did not end up in the expected state."""


@dataclass
class Context:
    base_url: str
    root: Path
    timeout_ms: int
    captured: dict[str, Any] = field(default_factory=dict)
    log: Callable[[str], None] = print


def _url(ctx: Context, target: str) -> str:
    return target if target.startswith(("http://", "https://")) else f"{ctx.base_url}/{target.lstrip('/')}"


def _sel(spec: Any) -> str:
    return spec if isinstance(spec, str) else spec["selector"]


def goto(page: Page, spec: Any, ctx: Context) -> None:
    resp = page.goto(_url(ctx, spec if isinstance(spec, str) else spec["url"]), wait_until="domcontentloaded")
    if resp is not None and resp.status >= 500:
        raise StepFailed(f"server returned HTTP {resp.status} for {page.url}")


def fill(page: Page, spec: dict, ctx: Context) -> None:
    page.locator(spec["selector"]).fill(str(spec.get("value", "")))


def click(page: Page, spec: Any, ctx: Context) -> None:
    page.locator(_sel(spec)).click()
    page.wait_for_load_state("domcontentloaded")
    title = page.title()
    if title.startswith("503") or "Service Unavailable" in title:
        raise StepFailed(f"server answered 503 Service Unavailable after clicking {_sel(spec)}")


def select(page: Page, spec: dict, ctx: Context) -> None:
    page.locator(spec["selector"]).select_option(label=str(spec["value"]))


def check(page: Page, spec: Any, ctx: Context) -> None:
    sel = _sel(spec)
    wanted = True if isinstance(spec, str) or "if" not in spec else str(spec["if"]).strip().lower() in TRUTHY
    page.locator(sel).set_checked(wanted)


def uncheck(page: Page, spec: Any, ctx: Context) -> None:
    page.locator(_sel(spec)).set_checked(False)


def upload(page: Page, spec: dict, ctx: Context) -> None:
    file = (ctx.root / spec["file"]).resolve()
    if not file.exists():
        raise StepFailed(f"upload file not found: {file}")
    page.locator(spec["selector"]).set_input_files(str(file))


def wait_for(page: Page, spec: Any, ctx: Context) -> None:
    state = "visible" if isinstance(spec, str) else spec.get("state", "visible")
    timeout = ctx.timeout_ms if isinstance(spec, str) else int(spec.get("timeout_ms", ctx.timeout_ms))
    page.locator(_sel(spec)).first.wait_for(state=state, timeout=timeout)


def expect_text(page: Page, spec: dict, ctx: Context) -> None:
    loc = page.locator(spec["selector"]).first
    loc.wait_for(state="visible", timeout=ctx.timeout_ms)
    text = loc.inner_text().strip()
    if spec["contains"] not in text:
        raise StepFailed(f"expected {spec['selector']!r} to contain {spec['contains']!r}, got {text[:120]!r}")


def extract(page: Page, spec: dict, ctx: Context) -> None:
    loc = page.locator(spec["selector"]).first
    loc.wait_for(state="visible", timeout=ctx.timeout_ms)
    value = loc.get_attribute(spec["attr"]) if spec.get("attr") else loc.inner_text().strip()
    ctx.captured[spec["name"]] = value


def extract_all(page: Page, spec: dict, ctx: Context) -> None:
    """Collect repeated items across pages and save them as CSV and JSON."""
    rows: list[dict] = []
    max_pages = int(spec.get("max_pages", 50))
    for page_no in range(1, max_pages + 1):
        page.locator(spec["item"]).first.wait_for(timeout=ctx.timeout_ms)
        for item in page.locator(spec["item"]).all():
            row = {}
            for name, fspec in spec["fields"].items():
                if isinstance(fspec, dict):
                    sub = item.locator(fspec["selector"])
                    if fspec.get("all"):
                        row[name] = ", ".join(t.strip() for t in sub.all_inner_texts())
                    elif fspec.get("attr"):
                        row[name] = sub.first.get_attribute(fspec["attr"]) if sub.count() else ""
                    else:
                        row[name] = sub.first.inner_text().strip() if sub.count() else ""
                else:
                    sub = item.locator(fspec)
                    row[name] = sub.first.inner_text().strip() if sub.count() else ""
            rows.append(row)
        ctx.log(f"page {page_no:>2}: {len(rows)} items so far")
        nxt = spec.get("next")
        if not nxt or page.locator(nxt).count() == 0:
            break
        page.locator(nxt).first.click()
        page.wait_for_load_state("domcontentloaded")

    save = ctx.root / spec["save"]
    save.parent.mkdir(parents=True, exist_ok=True)
    with save.with_suffix(".csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(spec["fields"]))
        writer.writeheader()
        writer.writerows(rows)
    save.with_suffix(".json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    ctx.captured["items_exported"] = len(rows)
    ctx.captured["export_file"] = str(save.with_suffix(".csv").relative_to(ctx.root))


def download(page: Page, spec: dict, ctx: Context) -> None:
    save_dir = ctx.root / spec.get("save_dir", "output/downloads")
    save_dir.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=ctx.timeout_ms) as info:
        page.locator(spec["click"]).first.click()
    dl = info.value
    target = save_dir / dl.suggested_filename
    dl.save_as(str(target))
    ctx.captured["downloaded_file"] = dl.suggested_filename
    ctx.captured["downloaded_bytes"] = target.stat().st_size


def screenshot(page: Page, spec: Any, ctx: Context) -> None:
    target = ctx.root / (spec if isinstance(spec, str) else spec["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target), full_page=False)


REGISTRY: dict[str, Callable[[Page, Any, Context], None]] = {
    "goto": goto, "fill": fill, "click": click, "select": select, "check": check,
    "uncheck": uncheck, "upload": upload, "wait_for": wait_for, "expect_text": expect_text,
    "extract": extract, "extract_all": extract_all, "download": download, "screenshot": screenshot,
}
