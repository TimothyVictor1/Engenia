"""Read pipeline artifacts and scheduler state; never run checks or write files."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from engenia.stages.registry import REGISTRY


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


class ProjectReader:
    def __init__(self, root: Path, project: str):
        self.root = root.resolve()
        self.project = project
        self.directory = self.root / "projects" / project
        if (
            Path(project).name != project
            or project in {".", ".."}
            or not self.directory.resolve().is_relative_to(self.root / "projects")
            or not self.directory.is_dir()
        ):
            raise ValueError(f"unknown project: {project}")

    @property
    def database_paths(self) -> tuple[Path, ...]:
        return (
            self.directory / ".engenia" / "run.db",
            self.root / ".engenia" / "run.db",
        )

    def run_stages(self) -> dict[str, dict]:
        """Read the documented run/stage_run tables, scoped to this project's latest run.

        A project-local database may omit run.project. A shared database must include it.
        The scheduler has not shipped yet, so an absent or uninitialised DB falls back
        to artifacts. SQL uses a read-only connection and never initialises a database.
        """
        for path in self.database_paths:
            if not path.is_file():
                continue
            try:
                with closing(
                    sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.1)
                ) as db:
                    db.row_factory = sqlite3.Row
                    db.execute("PRAGMA query_only = ON")
                    columns = {row[1] for row in db.execute('PRAGMA table_info("run")')}
                    order = '"started" DESC, id DESC' if "started" in columns else "id DESC"
                    if "project" in columns:
                        latest = db.execute(
                            f'SELECT id FROM "run" WHERE project = ? ORDER BY {order} LIMIT 1',
                            (self.project,),
                        ).fetchone()
                    elif path == self.database_paths[0]:
                        latest = db.execute(
                            f'SELECT id FROM "run" ORDER BY {order} LIMIT 1'
                        ).fetchone()
                    else:
                        continue
                    if latest is None:
                        continue
                    rows = db.execute(
                        "SELECT * FROM stage_run WHERE run_id = ? ORDER BY rowid", (latest[0],)
                    ).fetchall()
                    return {row["stage"]: dict(row) for row in rows}
            except sqlite3.OperationalError:
                # The producer can be creating its tables or holding an exclusive lock.
                continue
        return {}

    def interface(self, stage: str) -> dict:
        return read_json(self.directory / "build" / stage / "interface.json", {})

    def verdicts(self, stage: str) -> list[dict]:
        return read_json(self.directory / "build" / stage / "verdict.json", [])

    def stage_status(self, name: str, run: dict[str, dict]) -> str:
        recorded = run.get(name, {}).get("status")
        if recorded in {"running", "blocked", "failed", "done"}:
            return recorded
        if not (self.directory / "build" / name).is_dir():
            return "not_run"
        if any(item["result"] in {"fail", "error"} for item in self.verdicts(name)):
            return "failed"
        if self.interface(name).get("facts") == {"stub": True}:
            return "stub"
        return "done"

    def pipeline(self) -> dict:
        run = self.run_stages()
        return {
            "project": self.project,
            "updated": datetime.now(UTC).isoformat(),
            "stages": [
                {
                    "name": name,
                    "needs": stage.needs,
                    "status": self.stage_status(name, run),
                    "key": run.get(name, {}).get("key", self.interface(name).get("key")),
                }
                for name, stage in REGISTRY.items()
            ],
        }

    def stage(self, name: str) -> dict:
        return {
            "name": name,
            "status": self.stage_status(name, self.run_stages()),
            "facts": self.interface(name).get("facts", {}),
            "verdicts": self.verdicts(name),
        }

    def requirements(self) -> dict:
        # Newest verdict file wins across stages; list order breaks ties within a file.
        latest: dict[str, dict] = {}
        paths = list((self.directory / "build").glob("*/verdict.json"))
        for path in sorted(paths, key=lambda p: (p.stat().st_mtime_ns, p.as_posix())):
            for item in read_json(path, []):
                if item.get("requirement") is not None:
                    latest[item["requirement"]] = {
                        **{
                            key: item.get(key)
                            for key in ("check", "measured", "result", "evidence_level", "message")
                        },
                        "stage": path.parent.name,
                    }
        requirements = []
        for path in sorted((self.directory / "spec").glob("R-*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            verdict = latest.get(data["id"])
            limit = data["verify"].get("limit") or {}
            requirements.append(
                {
                    "id": data["id"],
                    "statement": data["statement"],
                    "unit": data.get("unit"),
                    "limit": {key: limit.get(key) for key in ("min", "max", "equals")},
                    "affects": data["affects"],
                    "method": data["verify"]["method"],
                    "status": verdict["result"] if verdict else "unverified",
                    "verdict": verdict,
                }
            )
        return {"requirements": requirements}

    def contract(self) -> dict:
        project_path = self.directory / "contract" / "contract.json"
        default_path = self.root / "packs" / "buoy" / "default_contract.json"
        source, contract = "none", None
        for label, path in (("project", project_path), ("default", default_path)):
            if path.is_file():
                source, contract = label, read_json(path)
                break
        facts = {
            path.parent.name: read_json(path)["facts"]
            for path in sorted((self.directory / "build").glob("*/interface.json"))
        }
        requests = [
            read_json(path)
            for path in sorted((self.directory / "contract" / "change_requests").glob("*.json"))
        ]
        return {"source": source, "contract": contract, "facts": facts, "change_requests": requests}

    def scenarios(self) -> dict:
        return {
            "scenarios": [
                yaml.safe_load(path.read_text(encoding="utf-8"))
                for path in sorted(
                    (self.directory / "build" / "redteam" / "scenarios").glob("*.yaml")
                )
            ]
        }
