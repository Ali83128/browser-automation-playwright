"""Command line: python -m runner run tasks/<task>.yaml [--headed] [--limit N] [--base-url URL]"""
from __future__ import annotations

import argparse
import sys

from .config import TaskError, load_task
from .engine import run_task, setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner", description="Run a browser automation task defined in YAML.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run a task file")
    run.add_argument("task", help="path to a task YAML file")
    run.add_argument("--headed", action="store_true", help="show the browser window")
    run.add_argument("--limit", type=int, help="only process the first N CSV rows")
    run.add_argument("--base-url", help="override the task's base_url")
    run.add_argument("--slow-mo", type=int, default=0, help="slow every browser action by N ms (for demos)")
    run.add_argument("-v", "--verbose", action="store_true")
    check = sub.add_parser("check", help="validate a task file without opening a browser")
    check.add_argument("task")
    args = parser.parse_args(argv)

    try:
        task = load_task(args.task, base_url=getattr(args, "base_url", None))
    except TaskError as exc:
        print(f"task error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "check":
        print(f"ok: {task.name} ({len(task.steps)} steps, login={'yes' if task.login else 'no'}, data={task.data})")
        return 0

    setup_logging(task.path.resolve().parent.parent, task.name, args.verbose)
    summary = run_task(task, headless=not args.headed, limit=args.limit, slow_mo=args.slow_mo)
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
