"""Rebuild docs/screenshots from real runs.

Needs the practice site running (python -m mock_site) and at least one finished bulk run
(python -m runner run tasks/bulk_lead_entry.yaml). Run from the project root:
    python scripts/capture_screenshots.py
"""
from __future__ import annotations

import csv
import html
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from runner.config import load_task  # noqa: E402
from runner.engine import run_task  # noqa: E402

SHOTS = ROOT / "docs" / "screenshots"
VIEW = {"width": 1440, "height": 900}

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{width:1440px;height:900px;background:#0d1117;font-family:'Segoe UI',system-ui,sans-serif;color:#c9d1d9;padding:28px}
.win{border:1px solid #30363d;border-radius:12px;overflow:hidden;background:#010409;height:100%;display:flex;flex-direction:column}
.bar{background:#161b22;padding:11px 16px;display:flex;gap:8px;align-items:center;border-bottom:1px solid #30363d}
.bar i{width:12px;height:12px;border-radius:50%;display:inline-block}
.bar span{margin-left:14px;font:13px 'DejaVu Sans Mono',monospace;color:#8b949e}
pre{font:13.4px/1.55 'DejaVu Sans Mono','Consolas',monospace;padding:16px 20px;white-space:pre-wrap;word-break:break-all;overflow:hidden;flex:1}
.p{color:#7ee787}.w{color:#e3b341}.e{color:#ff7b72}.d{color:#58a6ff;font-weight:700}.m{color:#8b949e}.ok{color:#7ee787;font-weight:700}
"""


def term_html(title: str, body: str) -> str:
    return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style><div class=win><div class=bar>"
            "<i style='background:#ff5f56'></i><i style='background:#ffbd2e'></i><i style='background:#27c93f'></i>"
            f"<span>{html.escape(title)}</span></div><pre>{body}</pre></div>")


def colour_log(lines: list[str]) -> str:
    out = []
    for raw in lines:
        line = html.escape(raw.rstrip("\n"))
        line = re.sub(r"^(\d\d:\d\d:\d\d)", r"<span class=m>\1</span>", line)
        line = line.replace("WARNING", "<span class=w>WARNING</span>").replace("ERROR", "<span class=e>ERROR</span>")
        line = re.sub(r"\b(ok)\b(?= )", r"<span class=ok>\1</span>", line)
        line = re.sub(r"(done .*)$", r"<span class=d>\1</span>", line)
        out.append(line)
    return "\n".join(out)


def shot_log(page) -> None:
    logs = sorted((ROOT / "output" / "logs").glob("bulk_lead_entry_*.log"))
    if not logs:
        raise SystemExit("no bulk_lead_entry log found - run the bulk task first")
    lines = logs[-1].read_text(encoding="utf-8").splitlines()
    body = "<span class=p>$</span> python -m runner run tasks/bulk_lead_entry.yaml\n" + colour_log(lines)
    page.set_content(term_html("browser-automation-playwright - bulk lead entry (real run log)", body))
    page.screenshot(path=str(SHOTS / "01_bulk_run_log.png"))


def shot_mid_automation() -> None:
    """Run row 1 of the real task with a screenshot step just before the form is submitted."""
    task = load_task(ROOT / "tasks" / "bulk_lead_entry.yaml")
    submit = next(i for i, s in enumerate(task.steps) if s.get("click") == "#submit-btn")
    steps = task.steps[:submit] + [{"screenshot": "docs/screenshots/02_form_mid_automation.png"}] + task.steps[submit:]
    out = ROOT / "output" / "results" / "_screenshot_run.csv"
    summary = run_task(replace(task, steps=steps, output=out, rate_limit_seconds=0), limit=1, root=ROOT)
    out.unlink(missing_ok=True)
    if summary.ok != 1:
        raise SystemExit("mid-automation capture run failed")


def shot_results(page) -> None:
    pytest = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider"], cwd=ROOT,
                            capture_output=True, text=True)
    test_out = (pytest.stdout + pytest.stderr).strip().splitlines()
    with (ROOT / "output" / "results" / "bulk_lead_entry.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    head = "".join(f"<th>{h}</th>" for h in ["row", "status", "attempts", "duration_ms", "reference"])
    body = "".join(
        f"<tr><td>{r['row']}</td><td class={'ok' if r['status']=='ok' else 'e'}>{r['status']}</td>"
        f"<td class={'w' if r['attempts']!='1' else ''}>{r['attempts']}</td><td>{r['duration_ms']}</td><td>{r['reference']}</td></tr>"
        for r in rows)
    ok = sum(r["status"] == "ok" for r in rows)
    retried = sum(r["attempts"] != "1" for r in rows)
    lines = [re.sub(r'\s*\[\s*\d+%\]$', '', l.replace('tests/', '')).strip(' =')
             for l in test_out if '::' in l or ' passed' in l or ' failed' in l]
    tests = colour_log(lines).replace('PASSED', '<span class=ok>PASSED</span>').replace("passed", "<span class=ok>passed</span>")
    page.set_content(f"""<!doctype html><meta charset=utf-8><style>{CSS}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:22px;height:100%}}
    table{{width:100%;border-collapse:collapse;font:13.5px 'DejaVu Sans Mono',monospace}}
    th{{text-align:left;color:#8b949e;font-weight:600;border-bottom:1px solid #30363d;padding:7px 10px}}
    td{{padding:4px 10px;border-bottom:1px solid #161b22}}
    .sum{{padding:12px 20px;color:#8b949e;font-size:14px;border-top:1px solid #30363d}}
    </style><div class=grid>
    <div class=win><div class=bar><i style='background:#ff5f56'></i><i style='background:#ffbd2e'></i><i style='background:#27c93f'></i>
    <span>output/results/bulk_lead_entry.csv ({len(rows)} rows)</span></div>
    <div style='padding:10px 12px;flex:1'><table><tr>{head}</tr>{body}</table></div>
    <div class=sum>{ok}/{len(rows)} rows confirmed by the site &nbsp;|&nbsp; {retried} recovered after a retry</div></div>
    <div class=win><div class=bar><i style='background:#ff5f56'></i><i style='background:#ffbd2e'></i><i style='background:#27c93f'></i>
    <span>python -m pytest -v</span></div><pre>{tests}</pre></div></div>""")
    page.screenshot(path=str(SHOTS / "03_results_and_tests.png"))


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    shot_mid_automation()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEW, device_scale_factor=1)
        shot_log(page)
        shot_results(page)
        browser.close()
    print("screenshots written to", SHOTS)


if __name__ == "__main__":
    main()
