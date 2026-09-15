"""Transactional state and an OS-owned, crash-released instance lock."""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .contracts import encode


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BusyError(RuntimeError):
    pass


@contextmanager
def instance_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".writer.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BusyError("Another pipeline owns this instance; inspect status and retry") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.db = sqlite3.connect(self.root / "state.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3):
            self.db.close()
            raise ValueError(f"Unsupported DB schema {version}; use the matching engine")
        if version == 0:
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE runs (
                    run_id TEXT PRIMARY KEY, run_key TEXT NOT NULL UNIQUE,
                    report_date TEXT NOT NULL, state TEXT NOT NULL,
                    input_json TEXT NOT NULL, config_json TEXT NOT NULL,
                    code_version TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, error TEXT
                );
                CREATE TABLE step_attempts (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    step_key TEXT NOT NULL, attempt_no INTEGER NOT NULL,
                    state TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, error TEXT,
                    PRIMARY KEY(run_id,step_key,attempt_no)
                );
                CREATE TABLE artifacts (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    step_key TEXT NOT NULL, payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    PRIMARY KEY(run_id,step_key)
                );
                PRAGMA user_version=1;
                COMMIT;
            """)
        if version < 2:
            try:
                self._upgrade_plans(backup=version == 1)
            except Exception:
                self.db.close()
                raise
        if version < 3:
            self._upgrade_risk_contract(backup=version == 2)

    def _upgrade_risk_contract(self, *, backup: bool):
        if backup:
            folder = self.root / "backups"
            folder.mkdir(exist_ok=True)
            with closing(sqlite3.connect(folder / f"state-before-v3-{uuid.uuid4().hex}.sqlite3")) as target:
                self.db.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Pre-migration backup integrity check failed")
        try:
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS risk_entities (
                    kind TEXT NOT NULL, entity_id TEXT NOT NULL, version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    PRIMARY KEY(kind,entity_id)
                );
                CREATE TABLE IF NOT EXISTS risk_events (
                    event_id TEXT PRIMARY KEY, command_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    external_ref TEXT UNIQUE, recorded_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS risk_events_no_update BEFORE UPDATE ON risk_events
                    BEGIN SELECT RAISE(ABORT, 'risk events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS risk_events_no_delete BEFORE DELETE ON risk_events
                    BEGIN SELECT RAISE(ABORT, 'risk events are append-only'); END;
                PRAGMA user_version=3;
                COMMIT;
            """)
        except Exception:
            self.db.rollback()
            self.db.close()
            raise

    def _upgrade_plans(self, *, backup: bool):
        if backup:
            folder=self.root/"backups"
            folder.mkdir(exist_ok=True)
            destination=folder/f"state-before-v2-{uuid.uuid4().hex}.sqlite3"
            with closing(sqlite3.connect(destination)) as target:
                self.db.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise ValueError("Pre-migration backup integrity check failed")
        try:
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE adoptions (
                    plan_id TEXT PRIMARY KEY, account_alias TEXT NOT NULL,
                    market TEXT NOT NULL, symbol TEXT NOT NULL, source TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active','closed')),
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX one_active_position_plan
                    ON adoptions(account_alias,market,symbol) WHERE state='active';
                CREATE TABLE plan_events (
                    event_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL REFERENCES adoptions(plan_id),
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    external_ref TEXT UNIQUE, recorded_at TEXT NOT NULL
                );
                PRAGMA user_version=2;
                COMMIT;
            """)
        except Exception:
            self.db.rollback()
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def recover(self):
        # Caller holds the OS lock: no other live writer can still own these rows.
        with self.db:
            self.db.execute("UPDATE runs SET state='interrupted',updated_at=? WHERE state='running'", (now(),))
            self.db.execute("UPDATE step_attempts SET state='interrupted',finished_at=? WHERE state='running'", (now(),))

    def run(self, run_id: str):
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown run: {run_id}")
        return dict(row)

    def state(self, run_id: str, state: str, error: str | None = None):
        with self.db:
            self.db.execute("UPDATE runs SET state=?,error=?,updated_at=? WHERE run_id=?", (state, error, now(), run_id))

    def begin_step(self, run_id: str, step: str) -> int:
        with self.db:
            attempt = self.db.execute("SELECT COALESCE(MAX(attempt_no),0)+1 FROM step_attempts WHERE run_id=? AND step_key=?", (run_id, step)).fetchone()[0]
            self.db.execute("INSERT INTO step_attempts VALUES (?,?,?,'running',?,NULL,NULL)", (run_id, step, attempt, now()))
        return attempt

    def finish_step(self, run_id: str, step: str, attempt: int, payload: dict, payload_hash: str):
        with self.db:
            self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (run_id, step, encode(payload), payload_hash))
            self.db.execute("UPDATE step_attempts SET state='succeeded',finished_at=? WHERE run_id=? AND step_key=? AND attempt_no=?", (now(), run_id, step, attempt))

    def fail_step(self, run_id: str, step: str, attempt: int, state: str, error: str):
        with self.db:
            self.db.execute("UPDATE step_attempts SET state=?,finished_at=?,error=? WHERE run_id=? AND step_key=? AND attempt_no=?", (state, now(), error, run_id, step, attempt))

    def result(self, run_id: str) -> dict:
        row = self.run(run_id)
        return {
            "schema_version": 1, "run_id": run_id, "state": row["state"],
            "report_date": row["report_date"], "source": json.loads(row["input_json"])["source"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "error": row["error"],
            "steps": [dict(r) for r in self.db.execute("SELECT step_key,attempt_no,state,error FROM step_attempts WHERE run_id=? ORDER BY started_at,step_key,attempt_no", (run_id,))],
            "artifacts": [{"step": r["step_key"], "payload": json.loads(r["payload_json"])} for r in self.db.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY step_key", (run_id,))],
            "next_action": "none" if row["state"] == "succeeded" else f"resume {run_id}",
        }
