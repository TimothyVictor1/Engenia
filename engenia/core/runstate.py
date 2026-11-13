"""Run state in <root>/.engenia/run.db (SQLite).

    run(id, project, started, finished, status)
    stage_run(run_id, stage, key, status, attempts, started, finished, message)

stage_run.status is one of pending, running, done, cached, failed, blocked. `done` means the
stage ran (generate and/or build); `cached` means its build was restored and nothing ran.
`attempts` counts how many times the stage was started in that run. `key` is the stage key, when
one could be worked out. run.status is running, done or failed.

Only the scheduling thread writes to the database, so no connection is shared between threads.
Timestamps are UTC, ISO 8601. The file is not a build output: it never affects a stage key.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

STAGE_STATUSES = ("pending", "running", "done", "cached", "failed", "blocked")
RUN_STATUSES = ("running", "done", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    project  TEXT NOT NULL,
    started  TEXT NOT NULL,
    finished TEXT,
    status   TEXT NOT NULL CHECK (status IN ('running', 'done', 'failed'))
);
CREATE TABLE IF NOT EXISTS stage_run (
    run_id   INTEGER NOT NULL REFERENCES run(id),
    stage    TEXT NOT NULL,
    key      TEXT,
    status   TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'done', 'cached', 'failed', 'blocked')
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    started  TEXT,
    finished TEXT,
    message  TEXT,
    PRIMARY KEY (run_id, stage)
);
"""


def db_path(root: Path) -> Path:
    return Path(root) / ".engenia" / "run.db"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class RunStore:
    def __init__(self, root: Path) -> None:
        path = db_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def start_run(self, project: str, stages: list[str]) -> int:
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO run (project, started, status) VALUES (?, ?, 'running')",
                (project, _now()),
            )
            run_id = int(cursor.lastrowid or 0)
            self._db.executemany(
                "INSERT INTO stage_run (run_id, stage, status) VALUES (?, ?, 'pending')",
                [(run_id, stage) for stage in stages],
            )
        return run_id

    def stage_started(self, run_id: int, stage: str) -> None:
        self._update(
            run_id, stage, "UPDATE stage_run SET status = 'running', attempts = attempts + 1,"
            " started = ? WHERE run_id = ? AND stage = ?", (_now(),),
        )

    def stage_finished(
        self, run_id: int, stage: str, status: str, *, key: str | None, message: str | None = None
    ) -> None:
        if status not in ("done", "cached", "failed"):
            raise ValueError(f"not a finishing status: {status}")
        self._update(
            run_id, stage, "UPDATE stage_run SET status = ?, key = ?, message = ?, finished = ?"
            " WHERE run_id = ? AND stage = ?", (status, key, message, _now()),
        )

    def stage_blocked(self, run_id: int, stage: str, message: str) -> None:
        self._update(
            run_id, stage, "UPDATE stage_run SET status = 'blocked', message = ?, finished = ?"
            " WHERE run_id = ? AND stage = ?", (message, _now()),
        )

    def finish_run(self, run_id: int, status: str) -> None:
        if status not in ("done", "failed"):
            raise ValueError(f"not a finishing status: {status}")
        with self._db:
            self._db.execute(
                "UPDATE run SET status = ?, finished = ? WHERE id = ?", (status, _now(), run_id)
            )

    def _update(self, run_id: int, stage: str, sql: str, head: tuple) -> None:
        with self._db:
            cursor = self._db.execute(sql, (*head, run_id, stage))
            if cursor.rowcount != 1:
                raise KeyError(f"run {run_id} has no stage {stage!r}")

    def runs(self, project: str | None = None) -> list[dict]:
        sql = "SELECT * FROM run" + (" WHERE project = ?" if project else "") + " ORDER BY id"
        return [dict(r) for r in self._db.execute(sql, (project,) if project else ())]

    def stage_runs(self, run_id: int) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM stage_run WHERE run_id = ? ORDER BY rowid", (run_id,)
        )
        return [dict(r) for r in rows]
