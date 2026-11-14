"""`engenia status`: the latest run of a project, read from .engenia/run.db.

Shows, per stage of the newest run, its status, attempts and duration, then the result of the
last `engenia gate` (build/gate/report.json) and the number of open change requests. Read only:
it never creates run.db, never runs a stage and never writes anything.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from engenia.core.board import REQUESTS_DIR
from engenia.core.runstate import db_path


def _seconds(started: str | None, finished: str | None) -> float | None:
    if not started or not finished:
        return None
    try:
        return (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return None


def _duration(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds:.1f}s"


def _gate(root: Path, project: str) -> str:
    path = root / "projects" / project / "build" / "gate" / "report.json"
    if not path.is_file():
        return "not run"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        passed = bool(report["passed"])
        failed = [name for name, gate in report["gates"].items() if not gate["passed"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return "unreadable (build/gate/report.json)"
    return "pass" if passed else "FAIL (" + ", ".join(failed or ["no detail"]) + ")"


def _open_requests(root: Path, project: str) -> list[str]:
    directory = root / "projects" / project / "contract" / REQUESTS_DIR
    found = []
    for path in sorted(directory.glob("CR-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("status") == "open":
            found.append(f"{data.get('id', path.stem)} ({data.get('path')})")
    return found


def status_lines(project: str, root: Path) -> list[str]:
    """The report for `engenia status <project>`, one string per line."""
    root = Path(root)
    run: sqlite3.Row | None = None
    stages: list[sqlite3.Row] = []
    path = db_path(root)
    if path.is_file():
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            run = db.execute(
                "SELECT * FROM run WHERE project = ? ORDER BY id DESC LIMIT 1", (project,)
            ).fetchone()
            if run is not None:
                stages = db.execute(
                    "SELECT * FROM stage_run WHERE run_id = ? ORDER BY rowid", (run["id"],)
                ).fetchall()
        finally:
            db.close()
    lines: list[str] = []
    if run is None:
        lines.append(f"{project}: no runs yet (engenia run {project})")
    else:
        total = _seconds(run["started"], run["finished"])
        lines.append(
            f"{project}: run {run['id']} {run['status']}, started {run['started']},"
            f" duration {_duration(total)}"
        )
        lines.append(f"  {'stage':<12} {'status':<8} {'attempts':>8} {'duration':>9}")
        for row in stages:
            lines.append(
                f"  {row['stage']:<12} {row['status']:<8} {row['attempts']:>8}"
                f" {_duration(_seconds(row['started'], row['finished'])):>9}"
            )
            if row["status"] in ("failed", "blocked") and row["message"]:
                lines.append(f"    {row['message'].splitlines()[0]}")
    lines.append(f"gate: {_gate(root, project)}")
    requests = _open_requests(root, project)
    lines.append(f"open change requests: {', '.join(requests) if requests else 'none'}")
    return lines
