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
            run_id, stage, "UPDATE stage_run SET status = ?, key = ?,"
            " message = COALESCE(?, message), finished = ? WHERE run_id = ? AND stage = ?",
            (status, key, message, _now()),
        )

    def latest_unfinished(self, project: str) -> int | None:
        """The newest run of `project` that did not end `done` (it failed or was interrupted)."""
        row = self._db.execute(
            "SELECT id FROM run WHERE project = ? AND status != 'done' ORDER BY id DESC LIMIT 1",
            (project,),
        ).fetchone()
        return int(row["id"]) if row else None

    def reopen_run(self, run_id: int, stages: list[str]) -> dict[str, dict]:
        """Continue an earlier run. Stages that finished (done or cached) keep their rows; every
        other stage goes back to pending, keeping its attempt count. Returns the kept rows."""
        with self._db:
            self._db.execute(
                "UPDATE run SET status = 'running', finished = NULL WHERE id = ?", (run_id,)
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO stage_run (run_id, stage, status) VALUES (?, ?, 'pending')",
                [(run_id, stage) for stage in stages],
            )
            self._db.execute(
                "UPDATE stage_run SET status = 'pending', started = NULL, finished = NULL,"
                " message = NULL WHERE run_id = ? AND status NOT IN ('done', 'cached')",
                (run_id,),
            )
        return {
            row["stage"]: row
            for row in self.stage_runs(run_id)
            if row["status"] in ("done", "cached")
        }

    def _message(self, run_id: int, stage: str) -> str | None:
        row = self._db.execute(
            "SELECT message FROM stage_run WHERE run_id = ? AND stage = ?", (run_id, stage)
        ).fetchone()
        if row is None:
            raise KeyError(f"run {run_id} has no stage {stage!r}")
        return row["message"]

    def stage_repair(self, run_id: int, stage: str, *, round_number: int, outcome: str) -> None:
        """Record one repair round of `stage`: an attempt, and a line in its message. Rounds
        add to the message, so the row tells the whole story. The status is left alone."""
        note = f"repair round {round_number}: {outcome}"
        earlier = self._message(run_id, stage)
        self._update(
            run_id, stage, "UPDATE stage_run SET attempts = attempts + 1, message = ?"
            " WHERE run_id = ? AND stage = ?", ("; ".join(filter(None, [earlier, note])),),
        )

    def stage_note(self, run_id: int, stage: str, note: str, *, status: str) -> None:
        """Set a stage's status and add a line to its message (attempts are not touched)."""
        if status not in STAGE_STATUSES:
            raise ValueError(f"unknown status: {status}")
        earlier = self._message(run_id, stage)
        self._update(
            run_id, stage, "UPDATE stage_run SET status = ?, message = ?, finished = ?"
            " WHERE run_id = ? AND stage = ?",
            (status, "; ".join(filter(None, [earlier, note])), _now()),
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
