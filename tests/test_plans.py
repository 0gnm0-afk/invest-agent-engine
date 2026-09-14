import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.plans import PlanLedger
from invest_agent.trading.runner import writable_store
from invest_agent.trading.store import Store

FIXTURE=Path(__file__).resolve().parents[1]/"examples"/"plan_fixture.json"


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.plan=json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.plan["adopted_at"]=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        self.call("record",self.plan)

    def call(self,method,value):
        with writable_store(self.root) as store:
            return getattr(PlanLedger(store),method)(value)

    def status(self): return self.call("status",self.plan["plan_id"])

    def legacy_reserve(self, event=None):
        from legacy_fixture import seed_legacy_reservation
        with writable_store(self.root) as store:
            seed_legacy_reservation(store, event or self.reserve())

    def event(self,kind,ident,**kwargs):
        return {"schema_version":1,"source":"synthetic","source_ref":"test-input","event_id":ident,
                "plan_id":self.plan["plan_id"],"kind":kind,"occurred_at":datetime.now(timezone.utc).isoformat(),**kwargs}

    def context(self,held="0",cash="1000",sell="0",price="100",cost="0",reflected=None):
        return {"account_alias":self.plan["account_alias"],"currency":"USD","price_basis":"executable_raw","snapshot_ref":"test-snapshot",
                "as_of":datetime.now(timezone.utc).isoformat(),"held_quantity":held,"orderable_cash":cash,"available_to_sell":sell,
                "price":price,"average_cost":cost,"broker_reflected_reservations":reflected or [],"open_orders_reconciled":True}

    def reserve(self,ident="r1",tranche="B1",quantity="5",context=None):
        return self.event("reserve",ident,tranche_id=tranche,quantity=quantity,context=context or self.context())

    def fill(self,ident="f1",reservation="r1",quantity="2",price="100",execution="exec-1"):
        return self.event("fill",ident,reservation_id=reservation,quantity=quantity,price=price,execution_id=execution)

    def test_plan_is_immutable_and_one_active_stop(self):
        repeated=self.call("record",self.plan)
        self.assertEqual(repeated["event_count"],0)
        changed=copy.deepcopy(self.plan);changed["stop_price"]="91"
        with self.assertRaisesRegex(ValueError,"immutable"): self.call("record",changed)
        changed["plan_id"]="other"
        with self.assertRaisesRegex(ValueError,"prior plan"): self.call("record",changed)
        self.assertEqual(self.status()["stop_price"],"90")

    def test_reservation_and_execution_are_idempotent(self):
        reserve=self.reserve()
        self.legacy_reserve(reserve);self.call("record_event",reserve)
        fill=self.fill()
        self.call("record_event",fill);self.call("record_event",fill)
        renamed={**fill,"event_id":"same-execution-new-id"}
        state=self.call("record_event",renamed)
        self.assertEqual(state["event_count"],2)
        self.assertEqual(state["inventory_from_records"],"2")
        self.assertEqual(state["tranches"][0]["reserved_quantity"],"3")
        self.assertEqual(state["tranches"][0]["filled_quantity"],"2")
        changed={**renamed,"quantity":"3"}
        with self.assertRaisesRegex(ValueError,"Execution ID"): self.call("record_event",changed)

    def test_reserved_quantity_cannot_exceed_tranche(self):
        self.legacy_reserve()
        with self.assertRaisesRegex(ValueError,"remaining quantity"):
            self.call("record_event",self.reserve("r2",quantity="1"))
        self.assertEqual(self.status()["event_count"],1)

    def test_release_frees_only_outstanding_quantity(self):
        self.legacy_reserve()
        self.call("record_event",self.fill())
        with self.assertRaisesRegex(ValueError,"Release exceeds"):
            self.call("record_event",self.event("release","release-too-many",reservation_id="r1",quantity="4"))
        self.call("record_event",self.event("release","release-1",reservation_id="r1",quantity="3"))
        with self.assertRaisesRegex(ValueError,"R3_R4_REQUIRED"):
            self.call("record_event",self.reserve("r2",quantity="3",context=self.context(held="2",cash="800",sell="2",cost="100")))
        result=self.status()
        self.assertEqual(result["tranches"][0]["filled_quantity"],"2")
        self.assertEqual(result["tranches"][0]["reserved_quantity"],"0")

    def test_broker_reflected_reservation_is_not_double_charged(self):
        self.legacy_reserve()
        with self.assertRaisesRegex(ValueError,"unreserved cash"):
            self.call("record_event",self.reserve("r2",tranche="B2",context=self.context(cash="500")))
        with self.assertRaisesRegex(ValueError,"R3_R4_REQUIRED"):
            self.call("record_event",self.reserve("r2",tranche="B2",context=self.context(cash="500",reflected=["r1"])))
        self.assertEqual(self.status()["open_purchase_cash"],"500")

    def test_stale_unreconciled_stop_and_loss_add_are_blocked(self):
        context=self.context();context["as_of"]=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        with self.assertRaisesRegex(ValueError,"stale"): self.call("record_event",self.reserve(context=context))
        context=self.context();context["open_orders_reconciled"]=False
        with self.assertRaisesRegex(ValueError,"reconciled"): self.call("record_event",self.reserve(context=context))
        with self.assertRaisesRegex(ValueError,"stop has been reached"): self.call("record_event",self.reserve(context=self.context(price="89")))
        self.legacy_reserve();self.call("record_event",self.fill())
        with self.assertRaisesRegex(ValueError,"Loss-position"):
            self.call("record_event",self.reserve("r2",tranche="B2",context=self.context(held="2",cost="105")))

    def test_real_execution_breach_is_retained_and_risk_reduction_allowed(self):
        self.legacy_reserve()
        result=self.call("record_event",self.fill(quantity="8",price="110"))
        self.assertEqual(result["inventory_from_records"],"8")
        self.assertIn("execution_exceeds_reservation:r1",result["alerts"])
        self.assertIn("cumulative_plan_loss_budget_exceeded",result["alerts"])
        with self.assertRaisesRegex(ValueError,"Resolve ledger alerts"):
            self.call("record_event",self.reserve("r2",tranche="B2",context=self.context(held="8",cash="120",sell="8",price="110",cost="110")))
        sale=self.reserve("s1",tranche="S1",quantity="5",context=self.context(held="8",cash="120",sell="8",price="85",cost="110"))
        state=self.call("record_event",sale)
        self.assertEqual(state["tranches"][2]["reserved_quantity"],"5")

    def test_sales_cannot_use_unfilled_buys(self):
        self.legacy_reserve()
        with self.assertRaisesRegex(ValueError,"sale quantity"):
            self.call("record_event",self.reserve("s1",tranche="S1",context=self.context(sell="0")))
        self.assertEqual(self.status()["inventory_from_records"],"0")

    def test_close_preserves_history_and_late_execution(self):
        self.legacy_reserve()
        with self.assertRaisesRegex(ValueError,"outstanding"):
            self.call("record_event",self.event("close","close1"))
        self.call("record_event",self.event("release","rel1",reservation_id="r1",quantity="5"))
        self.call("record_event",self.event("close","close1"))
        result=self.call("record_event",self.fill())
        self.assertEqual(result["record_state"],"closed")
        self.assertEqual(result["inventory_from_records"],"2")
        self.assertIn("execution_exceeds_reservation:r1",result["alerts"])

    def test_concurrent_cli_cannot_overreserve(self):
        paths=[]
        for i in (1,2):
            path=self.root/f"request-{i}.json"
            path.write_text(json.dumps(self.reserve(f"r{i}")),encoding="utf-8")
            paths.append(path)
        processes=[subprocess.Popen([sys.executable,"-m","invest_agent.cli","plan","event","--instance",str(self.root),"--input",str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for path in paths]
        results=[p.communicate(timeout=30) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes),[1,1],results)
        self.assertEqual(self.status()["event_count"],0)
        self.assertEqual(self.status()["open_purchase_cash"],"0")


class MigrationTests(unittest.TestCase):
    def legacy(self,root):
        store=Store(root)
        with store.db:
            store.db.execute("DROP TABLE plan_events")
            store.db.execute("DROP TABLE adoptions")
            store.db.execute("PRAGMA user_version=1")
            store.db.execute("INSERT INTO runs VALUES ('old','key','2026-09-11','succeeded','{}','{}','code','created','updated',NULL)")
        store.close()

    def test_v1_backup_and_data_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.legacy(root)
            store=Store(root)
            self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0],3)
            self.assertEqual(store.run("old")["state"],"succeeded")
            self.assertEqual(store.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            store.close()
            backups=list((root/"backups").glob("*.sqlite3"))
            self.assertEqual(len(backups),1)
            with closing(sqlite3.connect(backups[0])) as backup:
                self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0],1)
                self.assertEqual(backup.execute("SELECT run_id FROM runs").fetchone()[0],"old")
            Store(root).close()
            self.assertEqual(len(list((root/"backups").glob("*.sqlite3"))),1)

    def test_failed_migration_rolls_back_new_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.legacy(root)
            connect=sqlite3.connect
            class BrokenConnection(sqlite3.Connection):
                def executescript(self,script):
                    return super().executescript(script.replace("CREATE TABLE plan_events","CREATE INVALID TABLE plan_events"))
            with patch('invest_agent.trading.store.sqlite3.connect', side_effect=lambda *a, **kw: connect(*a, **{**kw, 'factory': BrokenConnection})), self.assertRaises(sqlite3.OperationalError):
                Store(root)
            with closing(connect(root/"state.sqlite3")) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT run_id FROM runs").fetchone()[0],"old")
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='adoptions'").fetchone())


if __name__=="__main__": unittest.main()
