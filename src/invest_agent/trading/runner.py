"""Frozen-input pipeline with durable stage commits and resumable attempts."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from .contracts import digest, encode, validate_fixture
from .reporting import render_report
from .store import BusyError, Store, instance_lock, now

STEPS = ("snapshot", "summary", "report")
MORNING_STEPS = ("snapshot", "market", "portfolio", "valuation", "report")


def code_version() -> str:
    # Refuse silent resume across a change to executable pipeline semantics.
    return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextmanager
def writable_store(root: Path):
    with instance_lock(root):
        store = Store(root)
        try:
            store.recover()
            yield store
        finally:
            store.close()


class PipelineRunner:
    def __init__(self, instance: Path, *, market_collector=None, valuation_collector=None):
        self.root = instance.resolve()
        self.market_collector = market_collector
        self.valuation_collector = valuation_collector

    def run_morning(self, config_path: Path, report_date: str, stop_after: str | None = None, refresh: bool = False) -> dict:
        from .morning import prepare
        date.fromisoformat(report_date)
        snapshot,config=prepare(config_path.resolve(),datetime.now(timezone.utc).isoformat())
        from .plans import PlanLedger
        with writable_store(self.root) as store:
            snapshot["plan_records"]=PlanLedger(store).list_plans(active_only=True)
            from .risk_reporting import freeze
            snapshot["risk_policy"] = freeze(store)
            from .history import previous_morning, previous_portfolio
            snapshot['previous_market']=previous_morning(store,snapshot,report_date,code_version())
            snapshot['previous_portfolio']=previous_portfolio(store,snapshot,report_date,code_version())
        if refresh:
            config={**config,"refresh_id":uuid.uuid4().hex}
        return self.run_snapshot(snapshot,{"mode":"morning_bundle", **config},report_date,stop_after)

    def run_account(self, snapshot_path: Path, report_date: str, stop_after: str | None = None) -> dict:
        from .portfolio import review
        date.fromisoformat(report_date)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
        evaluated_at = datetime.now(timezone.utc)
        review(snapshot, evaluated_at)
        snapshot["evaluated_at"] = evaluated_at.isoformat()
        snapshot["workflow"] = "portfolio_review"
        return self.run_snapshot(snapshot,{"mode":"portfolio_review"},report_date,stop_after)

    def run(self, fixture: Path, report_date: str, markets: list[str], stop_after: str | None = None) -> dict:
        date.fromisoformat(report_date)
        markets = sorted(set(markets))
        if not markets or any(m not in ("KR", "US") for m in markets):
            raise ValueError("markets must contain KR and/or US")
        snapshot = validate_fixture(json.loads(fixture.read_text(encoding="utf-8-sig")), markets)
        config = {"mode": "fixture", "markets": markets, "schema_version": 1}
        return self.run_snapshot(snapshot, config, report_date, stop_after)

    def run_market(self, config_path: Path, report_date: str, stop_after: str | None = None) -> dict:
        from .market_provider import collect
        date.fromisoformat(report_date)
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        snapshot = collect(config)
        # Capture the previous comparable report into this run's immutable input.
        if (self.root / "state.sqlite3").is_file():
            with writable_store(self.root) as store:
                for row in store.db.execute("SELECT run_id,input_json FROM runs WHERE state IN ('succeeded','partial') AND report_date<? ORDER BY created_at DESC", (report_date,)):
                    prior = json.loads(row["input_json"])
                    if prior.get("source") == "live_public" and prior.get("profile") == snapshot["profile"]:
                        result = store.db.execute("SELECT payload_json FROM artifacts WHERE run_id=? AND step_key='summary'", (row["run_id"],)).fetchone()
                        if result:
                            snapshot["previous_screen"] = json.loads(result[0])["rows"]
                            snapshot["previous_run_id"] = row["run_id"]
                            break
        return self.run_snapshot(snapshot, {"mode":"market_review", **config}, report_date, stop_after)

    def run_snapshot(self, snapshot: dict, config: dict, report_date: str, stop_after: str | None = None) -> dict:
        date.fromisoformat(report_date)
        version = code_version()
        key = digest({"report_date": report_date, "config": config, "input": snapshot, "code": version})
        with writable_store(self.root) as store:
            previous = store.db.execute("SELECT run_id FROM runs WHERE run_key=?", (key,)).fetchone()
            if previous is None and snapshot.get("workflow")=="morning":
                # A daily re-invocation must not duplicate collection just because
                # the wall clock changed. Keep full input/hash integrity on reuse.
                identity={k:v for k,v in snapshot.items() if k!="evaluated_at"}
                for candidate in store.db.execute("SELECT run_id,input_json,config_json FROM runs WHERE report_date=? AND code_version=? ORDER BY created_at DESC",(report_date,version)):
                    old_input=json.loads(candidate["input_json"])
                    if json.loads(candidate["config_json"])==config and {k:v for k,v in old_input.items() if k!="evaluated_at"}==identity:
                        previous=candidate
                        break
            if previous:
                run_id = previous[0]
            else:
                run_id = uuid.uuid4().hex
                stamp = now()
                with store.db:
                    store.db.execute("INSERT INTO runs VALUES (?,?,?,'queued',?,?,?,?,?,NULL)", (run_id, key, report_date, encode(snapshot), encode(config), version, stamp, stamp))
            result=self._execute(store, run_id, stop_after)
        return self._after_execute(result)

    def resume(self, run_id: str, stop_after: str | None = None) -> dict:
        if not (self.root / "state.sqlite3").is_file():
            raise ValueError("No instance database; run a fixture first")
        with writable_store(self.root) as store:
            result=self._execute(store, run_id, stop_after)
        return self._after_execute(result)

    def _after_execute(self,result):
        if result['source']!='morning_bundle' or result['state'] not in ('succeeded','partial'):
            return result
        with closing(sqlite3.connect((self.root/'state.sqlite3').as_uri()+'?mode=ro',uri=True)) as connection:
            row=connection.execute('SELECT input_json FROM runs WHERE run_id=?',(result['run_id'],)).fetchone()
            enabled=json.loads(row[0]).get('backup_on_completion',True)
        if enabled:
            try:
                from .backup import create
                result['backup']=create(self.root,result['run_id'])
            except Exception as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
                result['backup']={'state':'unavailable','error_type':type(exc).__name__,
                                  'reason':'backup_failed_report_preserved'}
        else:
            result['backup']={'state':'disabled_by_config'}
        return result

    def _execute(self, store: Store, run_id: str, stop_after: str | None) -> dict:
        row = store.run(run_id)
        if row["code_version"] != code_version():
            raise ValueError("Engine changed since this run; use its original version or start a new run")
        snapshot = json.loads(row["input_json"])
        steps=MORNING_STEPS if snapshot.get("workflow")=="morning" else STEPS
        if stop_after is not None and stop_after not in steps:
            raise ValueError("Unknown stop-after stage for this workflow")
        expected_key = digest({"report_date": row["report_date"], "config": json.loads(row["config_json"]), "input": snapshot, "code": row["code_version"]})
        if expected_key != row["run_key"]:
            raise ValueError("Frozen input/config integrity check failed")
        store.state(run_id, "running")
        active = None
        try:
            for step in steps:
                previous = store.db.execute("SELECT * FROM artifacts WHERE run_id=? AND step_key=?", (run_id, step)).fetchone()
                if previous:
                    payload = json.loads(previous["payload_json"])
                    if digest(payload) != previous["payload_hash"]:
                        raise ValueError(f"Artifact integrity check failed: {step}")
                    self._verify_files(payload)
                    continue
                attempt = store.begin_step(run_id, step)
                active = (step, attempt)
                payload = self._step(step, run_id, row["report_date"], snapshot)
                if step == "portfolio" and snapshot.get("workflow") == "morning":
                    from .risk_monitoring import evaluate, persist
                    frozen = snapshot.get("risk_policy", {})
                    market = self._stored_result(run_id, "market").get("snapshot", {})
                    observed = evaluate(frozen, market, snapshot["evaluated_at"], snapshot.get("account_input", {}))
                    payload["risk_policy"] = persist(store, run_id, frozen, observed, snapshot["evaluated_at"])
                    if payload.get("state") == "available" and (
                        any(m.get("state") in {"UNSET", "DATA_UNAVAILABLE"} for m in observed.get("monitor", []))
                        or payload["risk_policy"].get("persistence", {}).get("state") == "VERSION_CONFLICT"
                    ):
                        payload["state"] = "partial"
                store.finish_step(run_id, step, attempt, payload, digest(payload))
                active = None
                if step == stop_after and step != steps[-1]:
                    store.state(run_id, "interrupted", "Stopped at requested checkpoint")
                    return store.result(run_id)
            partial = False
            if snapshot.get("workflow")=="morning":
                partial=any(self._stored_result(run_id,k).get("state")!="available" for k in ("market","portfolio","valuation"))
                partial=partial or self._stored_result(run_id,"report").get("charts",{}).get("state")!="available"
            if snapshot.get("source") == "live_public":
                from .market import screen
                result=screen(snapshot)
                partial = result["unavailable_count"] > 0 or result["unavailable_scope_count"] > 0
            store.state(run_id, "partial" if partial else "succeeded")
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
            state = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            # Avoid echoing raw provider data/secrets as the pipeline grows.
            error = f"{type(exc).__name__}: pipeline stage failed; inspect local inputs/artifacts"
            if active:
                store.fail_step(run_id, *active, state, error)
            store.state(run_id, state, error)
        return store.result(run_id)

    def _step(self, step: str, run_id: str, report_date: str, snapshot: dict) -> dict:
        if step == "snapshot":
            path = Path("runs") / run_id / "input.json"
            return self._save({str(path): encode(snapshot) + "\n"})
        if snapshot.get("workflow")=="morning":
            from . import morning
            if step=="market":
                return morning.market_component(snapshot, collector=self.market_collector)
            if step=="portfolio":
                return morning.portfolio_component(snapshot, self._stored_result(run_id,'market'))
            if step=="valuation":
                return morning.valuation_component(snapshot,self._stored_result(run_id,"market"), collector=self.valuation_collector)
            folder=Path("reports")/report_date/run_id
            results={k:self._stored_result(run_id,k) for k in ("market","portfolio","valuation")}
            files,refs,chart_state=morning.render(run_id,report_date,snapshot,results,self.root,folder)
            result=self._save({str(folder/name):value for name,value in files.items()})
            result["files"].extend(refs)
            result["charts"]=chart_state
            return result
        if step == "summary":
            if snapshot.get("workflow") == "portfolio_review":
                from .portfolio import review
                return review(snapshot,datetime.fromisoformat(snapshot["evaluated_at"]))
            if snapshot.get("source") == "live_public":
                from .market import screen
                return screen(snapshot)
            return {"schema_version": 1, "source": "synthetic", "observation_count": len(snapshot["observations"]),
                    "market_sessions": sorted({r["market"] + ":" + r["session_date"] for r in snapshot["observations"]}),
                    "input_hash": digest(snapshot), "candidate_screen": "not_implemented"}
        folder = Path("reports") / report_date / run_id
        if snapshot.get("workflow") == "portfolio_review":
            from .reporting import render_portfolio_report
            return self._save({str(folder / name): value for name,value in render_portfolio_report(run_id,report_date,snapshot).items()})
        if snapshot.get("source") == "live_public":
            from .charts import generate
            from .reporting import render_market_report
            chart_refs = generate(snapshot, self.root, folder)
            result = self._save({str(folder / name): value for name, value in render_market_report(run_id, report_date, snapshot, chart_refs).items()})
            result["files"].extend(chart_refs)
            return result
        return self._save({str(folder / name): value for name, value in render_report(run_id, report_date, snapshot).items()})

    def _stored_result(self, run_id: str, step: str) -> dict:
        connection=sqlite3.connect((self.root/"state.sqlite3").as_uri()+"?mode=ro",uri=True)
        try:
            row=connection.execute("SELECT payload_json,payload_hash FROM artifacts WHERE run_id=? AND step_key=?",(run_id,step)).fetchone()
            if row is None:
                raise ValueError(f"Missing dependency result: {step}")
            payload=json.loads(row[0])
            if digest(payload)!=row[1]:
                raise ValueError(f"Changed dependency result: {step}")
            return payload
        finally:
            connection.close()

    def _save(self, files: dict[str, str]) -> dict:
        references = []
        for relative, contents in files.items():
            target = self.root / relative
            atomic_write(target, contents)
            references.append({"path": relative, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        return {"schema_version": 1, "files": references}

    def _verify_files(self, payload: dict):
        for ref in payload.get("files", []):
            path = (self.root / ref["path"]).resolve()
            if not path.is_relative_to(self.root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
                raise ValueError("Missing or changed artifact file; restore it before resuming")

    def status(self, run_id: str | None = None) -> dict:
        if not (self.root / "state.sqlite3").is_file():
            raise ValueError("No instance database")
        try:
            # Recover only when the OS proves no writer is alive.
            with writable_store(self.root) as store:
                return self._status(store, run_id)
        except BusyError:
            connection = sqlite3.connect((self.root / "state.sqlite3").as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            store = object.__new__(Store)
            store.root, store.db = self.root, connection
            try:
                return self._status(store, run_id)
            finally:
                store.close()

    @staticmethod
    def _status(store: Store, run_id: str | None) -> dict:
        if run_id:
            return store.result(run_id)
        return {"schema_version": 1, "runs": [dict(r) for r in store.db.execute("SELECT run_id,report_date,state,updated_at FROM runs ORDER BY created_at DESC")]}
