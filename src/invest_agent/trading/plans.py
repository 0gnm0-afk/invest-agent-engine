"""Immutable user plan records and a local, idempotent tranche ledger.

Reservations are local planning commitments, never broker orders. Recorded fills
describe observed execution; they are retained even if they breach plan limits.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from .contracts import digest, encode
from .portfolio import number
from .store import now


def text(value):
    if not isinstance(value,str) or not value.strip():
        raise ValueError("A nonempty identifier is required")
    return value


def stamp(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return result


def validate_plan(plan):
    if plan.get("schema_version")!=1 or plan.get("source") not in ("synthetic","user_adopted"):
        raise ValueError("Plan must be synthetic or explicitly user_adopted")
    if plan.get("source")=="user_adopted" and plan.get("acknowledgement")!="record_my_plan":
        raise ValueError("Explicit record_my_plan acknowledgement is required")
    for key in ("plan_id","account_alias","symbol","currency","adoption_ref"):
        text(plan[key])
    if plan["market"] not in ("KR","US") or plan["price_basis"]!="executable_raw":
        raise ValueError("Plan requires a supported market and explicit raw execution price basis")
    if stamp(plan["adopted_at"])>datetime.now(timezone.utc):
        raise ValueError("Future adoption timestamp")
    qty=number(plan["initial_quantity"])
    number(plan["initial_average_cost"])
    initial_price=number(plan["initial_price"],positive=True)
    stop=number(plan["stop_price"],positive=True)
    step=number(plan["quantity_step"],positive=True)
    stress=number(plan["slippage_per_share"])+number(plan["cost_per_share"])
    number(plan["max_context_age_minutes"],positive=True)
    caps={k:number(plan[k]) for k in ("max_purchase_cash","max_plan_loss_budget","max_position_quantity")}
    tranches=plan["tranches"]
    if not isinstance(tranches,list) or not tranches:
        raise ValueError("At least one tranche is required")
    ids=set(); buys=cash=risk=sells=Decimal(0)
    for tranche in tranches:
        ident=text(tranche["tranche_id"])
        if ident in ids:
            raise ValueError("Duplicate tranche ID")
        ids.add(ident)
        amount=number(tranche["quantity"],positive=True)
        price=number(tranche["price"],positive=True)
        if amount%step:
            raise ValueError("Tranche quantity violates the quantity step")
        if tranche["side"]=="buy":
            if price<=stop:
                raise ValueError("Buy tranche price must exceed the adopted stop")
            buys+=amount; cash+=amount*(price+stress); risk+=amount*(price-stop+stress)
        elif tranche["side"]=="sell":
            sells+=amount
        else:
            raise ValueError("Tranche side must be buy or sell")
    initial_risk=max(initial_price-stop,Decimal(0))*qty
    if buys and (cash>caps["max_purchase_cash"] or risk+initial_risk>caps["max_plan_loss_budget"] or qty+buys>caps["max_position_quantity"]):
        raise ValueError("Planned purchases exceed explicit plan budgets")
    if sells>qty+buys:
        raise ValueError("Planned sales exceed initial and planned purchased quantity")


class PlanLedger:
    """Caller must hold the instance writer lock for each mutation."""
    def __init__(self,store):
        self.store=store
        self.db=store.db

    def _plan(self,plan_id):
        row=self.db.execute("SELECT * FROM adoptions WHERE plan_id=?",(plan_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown plan")
        value=json.loads(row["payload_json"])
        if digest(value)!=row["payload_hash"]:
            raise ValueError("Plan integrity check failed")
        return dict(row),value

    def _events(self,plan_id):
        output=[]
        for row in self.db.execute("SELECT * FROM plan_events WHERE plan_id=? ORDER BY rowid",(plan_id,)):
            value=json.loads(row["payload_json"])
            if digest(value)!=row["payload_hash"]:
                raise ValueError("Event integrity check failed")
            output.append(value)
        return output

    def record(self,plan):
        validate_plan(plan)
        existing=self.db.execute("SELECT payload_hash FROM adoptions WHERE plan_id=?",(plan["plan_id"],)).fetchone()
        if existing:
            if existing[0]!=digest(plan):
                raise ValueError("Plans are immutable; use a new plan ID")
            return self.status(plan["plan_id"])
        active=self.db.execute("SELECT plan_id FROM adoptions WHERE account_alias=? AND market=? AND symbol=? AND state='active'",
            (plan["account_alias"],plan["market"],plan["symbol"])).fetchone()
        if active:
            raise ValueError("Close the prior plan before recording another stop for this position")
        with self.db:
            self.db.execute("INSERT INTO adoptions VALUES (?,?,?,?,?,'active',?,?,?)",
                (plan["plan_id"],plan["account_alias"],plan["market"],plan["symbol"],plan["source"],encode(plan),digest(plan),now()))
        return self.status(plan["plan_id"])

    def _state(self,plan,events):
        tranches={t["tranche_id"]:t for t in plan["tranches"]}
        reservations={}
        filled={key:Decimal(0) for key in tranches}
        initial=number(plan["initial_quantity"])
        inventory=initial
        stop=number(plan["stop_price"])
        stress=number(plan["slippage_per_share"])+number(plan["cost_per_share"])
        spent=buy_loss=Decimal(0)
        alerts=[]
        for event in events:
            kind=event["kind"]
            if kind=="reserve":
                reservations[event["event_id"]]={"tranche_id":event["tranche_id"],"quantity":number(event["quantity"]),"filled":Decimal(0),"released":Decimal(0)}
            elif kind in ("fill","release"):
                reservation=reservations[event["reservation_id"]]
                q=number(event["quantity"])
                if kind=="release":
                    reservation["released"]+=q
                else:
                    reservation["filled"]+=q
                    tranche=tranches[reservation["tranche_id"]]
                    filled[tranche["tranche_id"]]+=q
                    if tranche["side"]=="buy":
                        inventory+=q
                        price=number(event["price"])
                        spent+=q*(price+stress)
                        buy_loss+=q*(max(price-stop,Decimal(0))+stress)
                    else:
                        inventory-=q
        open_by_tranche={key:Decimal(0) for key in tranches}
        open_cash=open_risk=open_buys=open_sells=Decimal(0)
        for key,reservation in reservations.items():
            remaining=reservation["quantity"]-reservation["filled"]-reservation["released"]
            if remaining<0:
                alerts.append("execution_exceeds_reservation:"+key)
            reservation["remaining"]=max(remaining,Decimal(0))
            q=reservation["remaining"]
            tranche=tranches[reservation["tranche_id"]]
            open_by_tranche[tranche["tranche_id"]]+=q
            price=number(tranche["price"])
            if tranche["side"]=="buy":
                open_buys+=q;open_cash+=q*(price+stress);open_risk+=q*(price-stop+stress)
            else:
                open_sells+=q
        used_loss=max(number(plan["initial_price"])-stop,Decimal(0))*initial+buy_loss+open_risk
        if spent+open_cash>number(plan["max_purchase_cash"]): alerts.append("purchase_cash_budget_exceeded")
        if used_loss>number(plan["max_plan_loss_budget"]): alerts.append("cumulative_plan_loss_budget_exceeded")
        if inventory+open_buys>number(plan["max_position_quantity"]): alerts.append("position_quantity_cap_exceeded")
        if inventory<0: alerts.append("negative_inventory_requires_reconciliation")
        if open_sells>inventory: alerts.append("reserved_sales_exceed_inventory")
        for key,tranche in tranches.items():
            if filled[key]+open_by_tranche[key]>number(tranche["quantity"]): alerts.append("tranche_quantity_exceeded:"+key)
        return {"tranches":tranches,"reservations":reservations,"filled":filled,"inventory":inventory,
                "spent":spent,"open_cash":open_cash,"open_buys":open_buys,"open_sells":open_sells,
                "open_by_tranche":open_by_tranche,"used_loss":used_loss,"alerts":alerts}

    def status(self,plan_id):
        row,plan=self._plan(plan_id)
        events=self._events(plan_id)
        s=self._state(plan,events)
        return {"schema_version":1,"plan_id":plan_id,"source":plan["source"],"account_alias":plan["account_alias"],
            "market":plan["market"],"symbol":plan["symbol"],"currency":plan["currency"],"stop_price":plan["stop_price"],
            "cost_per_share":plan['cost_per_share'],"slippage_per_share":plan['slippage_per_share'],
            "record_state":row["state"],"authority":"local_plan_only_no_orders","broker_reconciled":False,
            "adoption_ref":plan["adoption_ref"],"adopted_at":plan["adopted_at"],"event_count":len(events),
            "inventory_from_records":str(s["inventory"]),"purchase_cash_budget_used_by_fills":str(s["spent"]),"open_purchase_cash":str(s["open_cash"]),
            "cumulative_plan_loss_budget_used":str(s["used_loss"]),"alerts":s["alerts"],
            "tranches":[{**t,"filled_quantity":str(s["filled"][t["tranche_id"]]),"reserved_quantity":str(s["open_by_tranche"][t["tranche_id"]]),
                         "uncommitted_quantity":str(max(number(t["quantity"])-s["filled"][t["tranche_id"]]-s["open_by_tranche"][t["tranche_id"]],Decimal(0)))} for t in plan["tranches"]],
            "reservations":{key:{k:str(v) if isinstance(v,Decimal) else v for k,v in value.items()} for key,value in s["reservations"].items()},
            "note":"Cumulative plan budgets include assumed per-share costs/slippage, even for recorded fills; they are not current portfolio loss or actual cash balance. Sales do not replenish these budgets."}

    def list_plans(self,active_only=False):
        query="SELECT plan_id FROM adoptions"+(" WHERE state='active'" if active_only else "")+" ORDER BY created_at,plan_id"
        return [self.status(row["plan_id"]) for row in self.db.execute(query)]

    def record_event(self,event):
        event=json.loads(encode(event))
        for key in ("event_id","plan_id","kind","source_ref"):
            text(event[key])
        if event.get("schema_version")!=1 or event.get("source") not in ("synthetic","user_input","broker_export"):
            raise ValueError("Unsupported event source")
        row,plan=self._plan(event["plan_id"])
        if (event["source"]=="synthetic") != (plan["source"]=="synthetic"):
            raise ValueError("Synthetic and real plan/event sources cannot be mixed")
        timestamp=stamp(event["occurred_at"])
        if timestamp>datetime.now(timezone.utc) or timestamp<stamp(plan["adopted_at"]):
            raise ValueError("Event timestamp outside the adopted plan timeline")
        previous=self.db.execute("SELECT payload_hash FROM plan_events WHERE event_id=?",(event["event_id"],)).fetchone()
        if previous:
            if previous[0]!=digest(event): raise ValueError("Event ID already exists with different contents")
            return self.status(event["plan_id"])
        external_ref=None
        if event["kind"]=="fill":
            external_ref=encode([plan["account_alias"],text(event["execution_id"])])
            match=self.db.execute("SELECT payload_json FROM plan_events WHERE external_ref=?",(external_ref,)).fetchone()
            if match:
                old=json.loads(match[0])
                if {k:v for k,v in old.items() if k!="event_id"}!={k:v for k,v in event.items() if k!="event_id"}:
                    raise ValueError("Execution ID already exists with different contents")
                return self.status(event["plan_id"])
        events=self._events(plan["plan_id"])
        s=self._state(plan,events)
        kind=event["kind"]
        if kind not in ("reserve","fill","release","close"):
            raise ValueError("Unsupported plan event")
        if row["state"]=="closed" and kind!="fill":
            raise ValueError("Plan is closed")
        if kind=="close":
            if any(r["remaining"] for r in s["reservations"].values()):
                raise ValueError("Release or reconcile outstanding reservations before closing")
        else:
            quantity=number(event["quantity"],positive=True)
            if kind!="fill" and quantity%number(plan["quantity_step"]):
                raise ValueError("Quantity violates plan step")
            if kind=="reserve":
                self._validate_reserve(event,plan,s)
            else:
                reservation=s["reservations"].get(event["reservation_id"])
                if reservation is None: raise ValueError("Unknown reservation")
                if kind=="release" and quantity>reservation["remaining"]:
                    raise ValueError("Release exceeds outstanding quantity")
                if kind=="fill": number(event["price"],positive=True)
        with self.db:
            self.db.execute("INSERT INTO plan_events VALUES (?,?,?,?,?,?,?)",(event["event_id"],event["plan_id"],kind,encode(event),digest(event),external_ref,now()))
            if kind=="close": self.db.execute("UPDATE adoptions SET state='closed' WHERE plan_id=?",(plan["plan_id"],))
        return self.status(plan["plan_id"])

    def _validate_reserve(self,event,plan,s):
        tranche=s["tranches"].get(event["tranche_id"])
        if tranche is None: raise ValueError("Unknown tranche")
        quantity=number(event["quantity"],positive=True)
        key=tranche["tranche_id"]
        if quantity+s["filled"][key]+s["open_by_tranche"][key]>number(tranche["quantity"]):
            raise ValueError("Tranche has insufficient remaining quantity")
        context=event["context"]
        if context.get("account_alias")!=plan["account_alias"] or context.get("currency")!=plan["currency"] or context.get("price_basis")!="executable_raw":
            raise ValueError("Reservation context does not match the plan")
        text(context["snapshot_ref"])
        age=(datetime.now(timezone.utc)-stamp(context["as_of"])).total_seconds()
        if age<0 or Decimal(str(age))>number(plan["max_context_age_minutes"])*60:
            raise ValueError("Reservation context is stale or in the future")
        if context.get("open_orders_reconciled") is not True or number(context["held_quantity"])!=s["inventory"]:
            raise ValueError("Account inventory and working orders must be reconciled first")
        reflected=set(context["broker_reflected_reservations"])
        if not reflected.issubset(s["reservations"]): raise ValueError("Unknown reflected reservation")
        pending_cash=pending_sell=Decimal(0)
        stress=number(plan["cost_per_share"])+number(plan["slippage_per_share"])
        for rid,reservation in s["reservations"].items():
            if rid in reflected: continue
            item=s["tranches"][reservation["tranche_id"]]
            if item["side"]=="buy": pending_cash+=reservation["remaining"]*(number(item["price"])+stress)
            else: pending_sell+=reservation["remaining"]
        if tranche["side"]=="sell":
            if quantity+s["open_sells"]>s["inventory"] or quantity+pending_sell>number(context["available_to_sell"]):
                raise ValueError("Insufficient unreserved sale quantity")
            return  # Risk-reducing sales are not blocked by a buy-budget breach.
        if s["alerts"]: raise ValueError("Resolve ledger alerts before new purchase commitments")
        current=number(context["price"],positive=True)
        if current<=number(plan["stop_price"]): raise ValueError("Adopted stop has been reached")
        if s["inventory"]>0 and current<number(context["average_cost"]):
            raise ValueError("Loss-position addition blocked; Q02B exception not enabled")
        price=number(tranche["price"])
        cash=quantity*(price+stress)
        risk=quantity*(price-number(plan["stop_price"])+stress)
        if cash+pending_cash>number(context["orderable_cash"]): raise ValueError("Insufficient unreserved cash")
        if cash+s["spent"]+s["open_cash"]>number(plan["max_purchase_cash"]): raise ValueError("Purchase cash budget exceeded")
        if risk+s["used_loss"]>number(plan["max_plan_loss_budget"]): raise ValueError("Plan loss budget exceeded")
        if quantity+s["inventory"]+s["open_buys"]>number(plan["max_position_quantity"]): raise ValueError("Position quantity cap exceeded")
        # Version-1 plan budgets do not carry R3 approval locks or the R4
        # strategy equity/portfolio budget. Keep historical fills/releases,
        # but never authorize a new purchase through this legacy path.
        raise ValueError("R3_R4_REQUIRED: create and approve a policy buy plan; legacy buy reservations are read-only")
