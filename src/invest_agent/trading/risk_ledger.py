"""R1-R4 versioned entities and append-only events in the existing Store.

The original PlanLedger remains readable for historical plans. A legacy stop
without trigger mode is never silently promoted to an R1 monitoring adoption.
"""
from __future__ import annotations

import json
from copy import deepcopy

from .buy_policy import approve_buy, buy_draft, buy_fill, trigger_buy
from .contracts import digest, encode
from .plans import PlanLedger, stamp, text
from .portfolio import number
from .protection import adopt_protection, monitor, validate_candidate
from .risk_budget import (
    RESERVING,
    equity_snapshot,
    plan_reservation,
    review_budget,
    risk_config,
    strategy_group,
)
from .sell_policy import (
    adopt_trend,
    approve_sell,
    evaluate_sell,
    proposed_trend,
    sell_fill,
    transition_tranche,
)
from .store import now

SPEC_VERSION = "R1-R4/1.0"


class RiskLedger(PlanLedger):
    """Mutation caller holds the existing instance lock; commits are atomic."""

    def execute_command(self, command: dict) -> dict:
        """Explicit operation allowlist; never expose arbitrary entity writes."""
        op, actor = command["operation"], command["actor"]
        request = {**command["request"], "command_envelope": deepcopy(command)}
        existing = self.db.execute("SELECT payload_json,payload_hash FROM risk_events WHERE event_id=?", (request["event_id"],)).fetchone()
        if existing:
            event = json.loads(existing[0])
            if digest(event) != existing[1] or event.get("command_envelope") != command:
                raise ValueError("Event ID reused with different command")
            primary = event["changes"][0]
            return self._required(primary["kind"], primary["entity_id"])
        if actor not in {"user", "broker", "engine", "llm"}:
            raise ValueError("Unknown actor")
        if op in {"observe_protection", "observe_sell"} and actor != "engine":
            raise ValueError("Only deterministic observation can update triggers")
        if op == "register_position":
            return self.register_position(command["position"], request, actor=actor)
        if op == "reconcile_position":
            return self.reconcile_position(command["position_id"], command["snapshot"], request, actor=actor)
        if op == "candidate":
            return self.candidate(command["candidate"], request, actor=actor)
        if op == "adopt_protection":
            return self.adopt(command["position_id"], request, actor=actor)
        if op == 'configure_approach':
            return self.configure_approach(command['position_id'], command['approach_range'], request, actor=actor)
        if op == "confirm_breach":
            return self.confirm_breach(command["position_id"], request, actor=actor)
        if op == "resolve_breach":
            return self.resolve_breach(command["position_id"], request, actor=actor)
        if op == "observe_protection":
            return self.observe(command["position_id"], command["observation"], request)
        if op == "propose_trend":
            return self.propose_trend(command["position_id"], command["evidence"], request, actor=actor)
        if op == "confirm_trend":
            return self.confirm_trend(command["position_id"], command["state"], request, actor=actor)
        if op == "save_sell_plan":
            return self.save_sell_plan(command["plan"], request, actor=actor)
        if op == "approve_sell_plan":
            return self.approve_sell_plan(command["plan_id"], request, actor=actor)
        if op == "activate_proactive":
            return self.activate_proactive(command["plan_id"], request, actor=actor)
        if op == "observe_sell":
            return self.observe_sell(command["plan_id"], command["observation"], request)
        if op == "confirm_sell_tranche":
            return self.confirm_sell_tranche(command["plan_id"], command["tranche_id"], request, actor=actor)
        if op == "cancel_sell":
            return self.cancel_sell(command["plan_id"], request, actor=actor, tranche_id=command.get("tranche_id"))
        if op == "sell_fill":
            return self.record_sell_fill(command["plan_id"], command["tranche_id"], command["fill"], request, actor=actor)
        if op == "configure_strategy":
            return self.configure_strategy(command["group"], request, actor=actor)
        if op == "configure_risk":
            return self.configure_risk(command["config"], request, actor=actor)
        if op == "equity_snapshot":
            return self.save_equity(command["strategy_group_id"], command["snapshot"], request, actor=actor)
        if op == "position_market":
            return self.position_market(command["position_id"], command["market_data"], request, actor=actor)
        if op == "save_buy_plan":
            return self.save_buy_plan(command["plan"], request, actor=actor)
        if op == "approve_buy_plan":
            return self.approve_buy_plan(command["plan_id"], request, actor=actor)
        if op == "trigger_buy":
            return self.trigger_buy_plan(command["plan_id"], command["tranche_id"], command["market_data"], request, actor=actor)
        if op == "cancel_buy":
            return self.cancel_buy(command["plan_id"], request, actor=actor, tranche_id=command.get("tranche_id"))
        if op == "buy_fill":
            return self.record_buy_fill(command["plan_id"], command["tranche_id"], command["fill"], request, actor=actor)
        raise ValueError("Unsupported risk policy operation")

    def get_entity(self, kind: str, entity_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT payload_json,payload_hash FROM risk_entities WHERE kind=? AND entity_id=?",
            (kind, entity_id),
        ).fetchone()
        if row is None:
            return None
        result = json.loads(row["payload_json"])
        if digest(result) != row["payload_hash"]:
            raise ValueError("Risk entity integrity check failed")
        return result

    def entities(self, kind: str) -> list[dict]:
        ids = self.db.execute("SELECT entity_id FROM risk_entities WHERE kind=? ORDER BY entity_id", (kind,))
        return [self._required(kind, row[0]) for row in ids]

    def audit(self) -> list[dict]:
        result = []
        for row in self.db.execute("SELECT * FROM risk_events ORDER BY rowid"):
            event = json.loads(row["payload_json"])
            if digest(event) != row["payload_hash"]:
                raise ValueError("Risk event integrity check failed")
            result.append(event)
        return result

    def _commit(self, kind: str, entity_id: str, value: dict, request: dict,
                *, actor: str, event_type: str, external_ref: str | None = None,
                updates: list[tuple[str, str, dict]] | None = None) -> dict:
        """One event and one entity transition, with optimistic version checks.

        Callers never receive a general CLI write-entity endpoint. All public
        commands validate authority and policy before entering this method.
        """
        text(request["event_id"])
        text(request["reason"])
        stamp(request["occurred_at"])
        text(request["data_as_of"])
        command_type = "ADOPT_PROTECTION" if event_type in {
            "INITIAL_STOP_ADOPTED", "PROTECTION_CHANGED", "RISK_EXPANSION_OVERRIDE"
        } else event_type
        command_hash = digest({"kind": kind, "id": entity_id, "request": request,
                               "actor": actor, "command_type": command_type})
        old_event = self.db.execute("SELECT command_hash FROM risk_events WHERE event_id=?", (request["event_id"],)).fetchone()
        if old_event:
            if old_event[0] != command_hash:
                raise ValueError("Event ID reused with different contents")
            return self._required(kind, entity_id)
        old = self.get_entity(kind, entity_id)
        version = old.get("version", 0) if old else 0
        if request.get("expected_version", version) != version:
            raise ValueError("Entity version changed; reload before retry")
        new = deepcopy(value)
        new["version"] = version + 1
        changes: list[dict] = [{"kind": kind, "entity_id": entity_id, "old_value": old, "new_value": new}]
        identities = {(kind, entity_id)}
        for linked_kind, linked_id, linked_value in updates or []:
            if (linked_kind, linked_id) in identities:
                raise ValueError("Duplicate entity in atomic transition")
            identities.add((linked_kind, linked_id))
            linked_old = self.get_entity(linked_kind, linked_id)
            linked_new = deepcopy(linked_value)
            linked_new["version"] = (linked_old or {}).get("version", 0) + 1
            changes.append({"kind": linked_kind, "entity_id": linked_id,
                            "old_value": linked_old, "new_value": linked_new})
        # Audit references come from the calculation, never a caller-supplied ID.
        # Missing post-trade inputs must not fall back to the old approval snapshot.
        if event_type == 'BUY_FILL_RECORDED':
            risk_snapshot_id = (new.get('post_trade_review') or {}).get('risk_budget_snapshot_id')
        elif event_type in {'BUY_TRIGGER_REVIEWED', 'BUY_PLAN_APPROVAL_REVIEWED'}:
            risk_snapshot_id = (new.get('risk_review') or {}).get('risk_budget_snapshot_id')
        elif kind == 'equity_snapshot':
            risk_snapshot_id = new.get('equity_snapshot_id')
        else:
            risk_snapshot_id = new.get('risk_budget_snapshot_id')
        # Candidate/sell/monitor payloads identify a position, not its strategy.
        # Resolve that relationship from the ledger, never request metadata.
        position_id = new.get('position_id')
        related_position = (new if kind == 'position' else self.get_entity('position', position_id)) if position_id else None
        group_id = (related_position or {}).get('strategy_group_id') or new.get('strategy_group_id')
        related_sells = [change['entity_id'] for change in changes if change['kind'] == 'sell_plan']
        related_candidate = (new.get('candidate_id') if kind == 'candidate' else
                             new.get('adopted_candidate_id') if command_type == 'ADOPT_PROTECTION' else None)
        event = {
            "event_id": request["event_id"], "event_type": event_type,
            "position_id": position_id, "strategy_group_id": group_id,
            "buy_plan_id": new.get("buy_plan_id"), "tranche_id": request.get("tranche_id"),
            "related_sell_plan_id": related_sells[0] if len(related_sells) == 1 else None,
            "related_sell_plan_ids": related_sells,
            "related_candidate_id": related_candidate,
            "old_value": old, "new_value": new, "reason": request["reason"], "actor": actor,
            "occurred_at": request["occurred_at"], "data_as_of": request["data_as_of"],
            "risk_budget_snapshot_id": risk_snapshot_id,
            "spec_version": SPEC_VERSION,
            "changes": changes,
            "execution": request.get("execution"),
            "command_envelope": request.get("command_envelope"),
        }
        if kind == 'morning_observation':
            observed = new['result']
            budgets = {b['strategy_group_id']: b for b in observed.get('budgets', [])}
            observed_positions = observed.get('position', [])
            groups = sorted(set(budgets) | {p['strategy_group_id'] for p in observed_positions})
            event['observed_strategy_contexts'] = [
                {'strategy_group_id': group, 'risk_budget_snapshot_id': budgets.get(group, {}).get('risk_budget_snapshot_id'),
                 'position_ids': [p['position_id'] for p in observed_positions if p['strategy_group_id'] == group],
                 'equity_data_as_of': budgets.get(group, {}).get('equity_data_as_of'),
                 'block_reasons': budgets.get(group, {}).get('block_reasons', [])}
                for group in groups]
            event['observation_application_state'] = observed.get('persistence', {}).get('state')
            event['applied_entity_refs'] = [{'kind': c['kind'], 'entity_id': c['entity_id']}
                                            for c in changes if c['kind'] != 'morning_observation']
            # Preserve scalar compatibility only when it is unambiguous. A
            # multi-strategy observation has no single authoritative snapshot.
            if len(groups) == 1:
                event['strategy_group_id'] = groups[0]
                event['risk_budget_snapshot_id'] = budgets.get(groups[0], {}).get('risk_budget_snapshot_id')
            if len(observed_positions) == 1:
                event['position_id'] = observed_positions[0]['position_id']
        with self.db:
            self.db.execute("INSERT INTO risk_events VALUES (?,?,?,?,?,?)",
                            (request["event_id"], command_hash, encode(event), digest(event), external_ref, now()))
            for change in changes:
                state = change["new_value"]
                self.db.execute("INSERT INTO risk_entities VALUES (?,?,?,?,?) ON CONFLICT(kind,entity_id) "
                                "DO UPDATE SET version=excluded.version,payload_json=excluded.payload_json,payload_hash=excluded.payload_hash",
                                (change["kind"], change["entity_id"], state["version"], encode(state), digest(state)))
        return new

    def register_position(self, position: dict, request: dict, *, actor: str) -> dict:
        if actor not in {"user", "broker"}:
            raise ValueError("Only observed holdings can establish a position")
        ident = text(position["position_id"])
        if self.get_entity("position", ident):
            raise ValueError("Use reconciliation or a fill for existing position quantities")
        for field in ("account_alias", "symbol", "currency", "strategy_group_id"):
            text(position[field])
        if position["market"] not in {"KR", "US"}:
            raise ValueError("Only KR/US long stocks supported")
        number(position["current_quantity"])
        number(position["average_cost"])
        fields = {"position_id", "strategy_group_id", "account_alias", "market", "symbol", "currency", "source",
                  "current_quantity", "average_cost", "name", "data_as_of"}
        value = {**{k: deepcopy(v) for k, v in position.items() if k in fields}, "initial_stop_price": None,
                 "current_protection_price": None, "trigger_mode": None,
                 "protection_version": 0,
                 "proposed_medium_trend_state": "UNAVAILABLE", "adopted_medium_trend_state": None}
        value.setdefault("source", "broker" if actor == "broker" else "user_input")
        return self._commit("position", ident, value, {**request, "input": position}, actor=actor, event_type="POSITION_OBSERVED")

    def candidate(self, value: dict, request: dict, *, actor: str) -> dict:
        candidate = validate_candidate(value)
        if not self.get_entity("position", candidate["position_id"]):
            raise ValueError("Unknown position")
        return self._commit("candidate", candidate["candidate_id"], candidate, {**request, "input": value},
                            actor=actor, event_type="CANDIDATE_CREATED")

    def configure_approach(self, position_id: str, value, request: dict, *, actor: str) -> dict:
        """User-selected absolute range in the position's quote currency; None resets."""
        self._user_confirmation(request, actor)
        position = self._required('position', position_id)
        position['approach_range'] = str(number(value, positive=True)) if value is not None else None
        return self._commit('position', position_id, position, {**request, 'approach_range': value},
                            actor=actor, event_type='APPROACH_SETTING_CHANGED')

    def adopt(self, position_id: str, request: dict, *, actor: str) -> dict:
        position = self.get_entity("position", position_id)
        if position is None:
            raise ValueError("Unknown position")
        candidate_id = request.get("adopted_candidate_id")
        if candidate_id:
            candidate = self.get_entity("candidate", candidate_id)
            if candidate is None or candidate["position_id"] != position_id:
                raise ValueError("Candidate does not belong to position")
        adopted = adopt_protection(position, request, actor=actor)
        kind = "INITIAL_STOP_ADOPTED" if position["initial_stop_price"] is None else "PROTECTION_CHANGED"
        if position["current_protection_price"] is not None and number(request["price"]) < number(position["current_protection_price"]):
            kind = "RISK_EXPANSION_OVERRIDE"
        updates = []
        if position.get("current_protection_price") != adopted["current_protection_price"]:
            for plan in self._sell_plans(position_id):
                if plan["status"] == "ACTIVE":
                    final = next((t for t in plan["tranches"] if t["tranche_id"] == plan.get("final_protection_tranche_id")), None)
                    old_protection = position.get('current_protection_price')
                    crossed_tranche = any(
                        t.get('trigger_price') is not None and t.get('status') not in {'EXECUTED', 'CANCELLED', 'SUPERSEDED'}
                        and number(t['trigger_price']) <= number(adopted['current_protection_price'])
                        and (old_protection is None or number(t['trigger_price']) > number(old_protection))
                        for t in plan['tranches'])
                    if crossed_tranche or (final and number(final["trigger_price"]) != number(adopted["current_protection_price"])):
                        plan["status"] = "NEEDS_REAPPROVAL"
                        plan["reapproval_reason"] = "PROTECTION_CHANGED"
                        updates.append(("sell_plan", plan["sell_plan_id"], plan))
            for plan in self.entities("buy_plan"):
                if plan.get("position_id") == position_id and plan["status"] in RESERVING:
                    if not plan.get("approved_reservation"):
                        cash, risk = plan_reservation(plan)
                        plan["approved_reservation"] = {"cash": str(cash), "risk": str(risk)}
                    if number(adopted["current_protection_price"]) < number(plan["adopted_stop_price"]):
                        plan["status"] = "NEEDS_REAPPROVAL"
                        plan["block_reasons"] = ["PROTECTION_MUST_BE_ADOPTED_TOGETHER"]
                    else:
                        plan["adopted_stop_price"] = adopted["current_protection_price"]
                        if any(number(t["risk_entry_price"]) <= number(plan["adopted_stop_price"]) and number(t["remaining_quantity"]) > 0 for t in plan["tranches"]):
                            plan["status"] = "NEEDS_REAPPROVAL"
                            plan["block_reasons"] = ["INVALID_STOP"]
                        else:
                            cash, risk = plan_reservation(plan)
                            plan.update(total_reserved_cash=str(cash), total_nominal_planned_risk=str(risk),
                                        approved_reservation={"cash": str(cash), "risk": str(risk)})
                    updates.append(("buy_plan", plan["buy_plan_id"], plan))
        return self._commit("position", position_id, adopted, request, actor=actor, event_type=kind, updates=updates)

    def observe(self, position_id: str, observation: dict, request: dict) -> dict:
        position = self.get_entity("position", position_id)
        if position is None:
            raise ValueError("Unknown position")
        previous = self.get_entity("monitor", position_id)
        value = monitor(position, observation, previous=previous)
        value["position_id"] = position_id
        position["breach_unresolved"] = value["state"] == "CONFIRMED_BREACH"
        return self._commit("monitor", position_id, value, {**request, "input": observation}, actor="engine",
                            event_type="PROTECTION_OBSERVED", updates=[("position", position_id, position)])

    def _required(self, kind: str, ident: str) -> dict:
        value = self.get_entity(kind, ident)
        if value is None:
            raise ValueError(f"Unknown {kind}")
        return value

    def _sell_plans(self, position_id: str) -> list[dict]:
        return [p for p in self.entities("sell_plan") if p["position_id"] == position_id]

    def confirm_breach(self, position_id: str, request: dict, *, actor: str) -> dict:
        if actor != "user" or request.get("explicit_user_confirmation") is not True:
            raise ValueError("Manual breach requires explicit user confirmation")
        position = self._required("position", position_id)
        if position.get("current_protection_price") is None:
            raise ValueError("No adopted protection")
        position.update(manual_breach_confirmed=True, breach_unresolved=True)
        observed = self.get_entity("monitor", position_id) or {"position_id": position_id}
        observed.update(state="CONFIRMED_BREACH", manual_required=True, notification_required=True,
                        data_as_of=request["data_as_of"])
        return self._commit("position", position_id, position, request, actor=actor,
                            event_type="MANUAL_BREACH_CONFIRMED", updates=[("monitor", position_id, observed)])

    def resolve_breach(self, position_id: str, request: dict, *, actor: str) -> dict:
        if actor != "user" or request.get("explicit_user_confirmation") is not True:
            raise ValueError("Resolution requires explicit user confirmation")
        position = self._required("position", position_id)
        position.update(manual_breach_confirmed=False, breach_unresolved=False)
        observed = self.get_entity("monitor", position_id) or {"position_id": position_id}
        observed.update(state="RESOLVED", manual_required=False, notification_required=False,
                        data_as_of=request["data_as_of"])
        return self._commit("position", position_id, position, request, actor=actor,
                            event_type="BREACH_RESOLVED", updates=[("monitor", position_id, observed)])

    def propose_trend(self, position_id: str, value: dict, request: dict, *, actor: str) -> dict:
        position = self._required("position", position_id)
        evidence = proposed_trend(value)
        position.update(proposed_medium_trend_state=evidence["proposed_state"], medium_trend_evidence=evidence)
        return self._commit("position", position_id, position, {**request, "input": value}, actor=actor,
                            event_type="MEDIUM_TREND_PROPOSED")

    def confirm_trend(self, position_id: str, state: str, request: dict, *, actor: str) -> dict:
        if request.get("explicit_user_confirmation") is not True:
            raise ValueError("Trend adoption requires explicit confirmation")
        position, plans = adopt_trend(self._required("position", position_id), self._sell_plans(position_id),
                                     state, actor=actor, at=request["occurred_at"])
        return self._commit("position", position_id, position, {**request, "input": state}, actor=actor,
                            event_type="MEDIUM_TREND_ADOPTED",
                            updates=[("sell_plan", p["sell_plan_id"], p) for p in plans])

    def save_sell_plan(self, value: dict, request: dict, *, actor: str) -> dict:
        if actor != "user":
            raise ValueError("Only user can create or revise executable plan drafts")
        ident = text(value["sell_plan_id"])
        self._required("position", value["position_id"])
        old = self.get_entity("sell_plan", ident)
        if old and old["position_id"] != value["position_id"]:
            raise ValueError("Plan cannot move to another position")
        if old and old["status"] in {"CANCELLED", "COMPLETED"}:
            raise ValueError("Closed plans cannot be revived; create a new plan")
        allowed = {"sell_plan_id", "position_id", "plan_kind", "quantity_step", "initial_reduction_quantity",
                   "tranches", "final_protection_tranche_id"}
        plan = {k: deepcopy(v) for k, v in value.items() if k in allowed}
        prior_tranches = {t['tranche_id']: t for t in (old or {}).get('tranches', [])}
        for tranche in plan.get('tranches', []):
            prior = prior_tranches.get(tranche['tranche_id'])
            tranche.update(created_at=prior.get('created_at') if prior is not None else request['occurred_at'],
                           updated_at=request['occurred_at'], candidate_id=tranche.get('candidate_id'),
                           status='PLANNED', actual_fills=[], remaining_planned_quantity=tranche.get('planned_quantity'))
        plan.update(created_at=old.get('created_at') if old else request['occurred_at'], updated_at=request['occurred_at'])
        plan.update(status="NEEDS_REAPPROVAL" if old else "DRAFT", approved_by_user=False,
                    history=[*(old or {}).get("history", []),
                             *([{k: v for k, v in old.items() if k != "history"}] if old else [])])
        return self._commit("sell_plan", ident, plan, {**request, "input": value}, actor=actor, event_type="SELL_PLAN_SAVED")

    def approve_sell_plan(self, plan_id: str, request: dict, *, actor: str) -> dict:
        if request.get("explicit_user_confirmation") is not True:
            raise ValueError("Explicit sell-plan approval required")
        plan = self._required("sell_plan", plan_id)
        if plan["status"] not in {"DRAFT", "NEEDS_REAPPROVAL"}:
            raise ValueError("Only a draft or revised plan can be approved")
        if plan.get("approved_by_user") is True:
            raise ValueError("Save a revised remaining-quantity plan before reapproval")
        position = self._required("position", plan["position_id"])
        plan.update(approved_by_user=True, approved_at=request["occurred_at"])
        approved = approve_sell(plan, position, actor=actor)
        return self._commit("sell_plan", plan_id, approved, request, actor=actor, event_type="SELL_PLAN_APPROVED")

    def activate_proactive(self, plan_id: str, request: dict, *, actor: str) -> dict:
        if actor != "user" or request.get("explicit_user_confirmation") is not True:
            raise ValueError("User activation required")
        plan = self._required("sell_plan", plan_id)
        position = self._required("position", plan["position_id"])
        if plan["plan_kind"] != "PROACTIVE_PROFIT_TAKE" or plan["status"] != "READY":
            raise ValueError("A ready proactive partial-sale plan is required")
        if position.get("adopted_medium_trend_state") == "CONFIRMED_BROKEN":
            raise ValueError("Trend-break exit takes priority")
        if number(plan["position_quantity_at_approval"]) != number(position["current_quantity"]):
            plan["status"] = "NEEDS_REAPPROVAL"
        else:
            plan["status"] = "ACTIVE"
            for tranche in plan["tranches"]:
                transition_tranche(tranche, "ARMED", request["occurred_at"], "USER_ACTIVATED_PROACTIVE_PLAN")
        return self._commit("sell_plan", plan_id, plan, request, actor=actor, event_type="PROACTIVE_PLAN_ACTIVATED")

    def observe_sell(self, plan_id: str, observation: dict, request: dict) -> dict:
        plan = self._required("sell_plan", plan_id)
        position = self._required("position", plan["position_id"])
        updated = evaluate_sell(plan, position, {**observation, "observed_at": request["occurred_at"]})
        return self._commit("sell_plan", plan_id, updated, {**request, "input": observation}, actor="engine", event_type="SELL_TRANCHES_OBSERVED")

    def confirm_sell_tranche(self, plan_id: str, tranche_id: str, request: dict, *, actor: str) -> dict:
        if actor != "user" or request.get("explicit_user_confirmation") is not True:
            raise ValueError("Manual tranche confirmation requires user")
        plan = self._required("sell_plan", plan_id)
        if plan["status"] != "ACTIVE":
            raise ValueError("Plan is not active")
        tranche = next((t for t in plan["tranches"] if t["tranche_id"] == tranche_id), None)
        if tranche is None or tranche["status"] in {"CANCELLED", "SUPERSEDED", "EXECUTED"}:
            raise ValueError("No pending tranche")
        if tranche["status"] not in {"PARTIALLY_EXECUTED", "ACTION_REQUIRED"}:
            transition_tranche(tranche, "CONFIRMED_BREACH", request["occurred_at"], "USER_CONFIRMED_TRIGGER")
            transition_tranche(tranche, "ACTION_REQUIRED", request["occurred_at"], "USER_ACTION_REQUIRED")
        return self._commit("sell_plan", plan_id, plan, {**request, "tranche_id": tranche_id}, actor=actor, event_type="SELL_TRANCHE_CONFIRMED")

    def cancel_sell(self, plan_id: str, request: dict, *, actor: str, tranche_id: str | None = None) -> dict:
        if actor != "user" or request.get("explicit_user_confirmation") is not True:
            raise ValueError("Cancellation requires explicit user confirmation")
        plan = self._required("sell_plan", plan_id)
        if tranche_id is None:
            plan["status"] = "CANCELLED"
            for tranche in ([plan["initial_reduction"]] if "initial_reduction" in plan else []) + plan["tranches"]:
                if tranche.get("status") != "EXECUTED":
                    transition_tranche(tranche, "CANCELLED", request["occurred_at"], "USER_CANCELLED_PLAN")
        else:
            tranche = next((t for t in plan["tranches"] if t["tranche_id"] == tranche_id), None)
            if tranche is None or tranche["status"] == "EXECUTED":
                raise ValueError("No remaining tranche to cancel")
            transition_tranche(tranche, "CANCELLED", request["occurred_at"], "USER_CANCELLED_TRANCHE")
            plan["status"] = "NEEDS_REAPPROVAL"
        return self._commit("sell_plan", plan_id, plan, {**request, "tranche_id": tranche_id}, actor=actor, event_type="SELL_PLAN_CANCELLED")

    def record_sell_fill(self, plan_id: str, tranche_id: str, fill: dict, request: dict, *, actor: str) -> dict:
        if actor not in {"user", "broker"}:
            raise ValueError("Actual fill requires user or broker source")
        plan = self._required("sell_plan", plan_id)
        position = self._required("position", plan["position_id"])
        external_ref = encode([position["account_alias"], text(fill["execution_id"])])
        match = self.db.execute("SELECT payload_json FROM risk_events WHERE external_ref=?", (external_ref,)).fetchone()
        if match:
            old = json.loads(match[0])
            if old.get("execution") != {"plan_id": plan_id, "tranche_id": tranche_id, "fill": fill}:
                raise ValueError("Execution ID reused with different fill")
            return plan
        updated, position = sell_fill(plan, position, tranche_id, fill, actor=actor)
        updates = [("position", position["position_id"], position)]
        for other in self._sell_plans(position["position_id"]):
            if other["sell_plan_id"] != plan_id and other["status"] not in {"CANCELLED", "COMPLETED"}:
                other["status"] = "NEEDS_REAPPROVAL"
                other["reapproval_reason"] = "INVENTORY_CHANGED"
                updates.append(("sell_plan", other["sell_plan_id"], other))
        command = {**request, "tranche_id": tranche_id,
                   "execution": {"plan_id": plan_id, "tranche_id": tranche_id, "fill": fill}}
        return self._commit("sell_plan", plan_id, updated, command, actor=actor, event_type="SELL_FILL_RECORDED",
                            external_ref=external_ref, updates=updates)

    def reconcile_position(self, position_id: str, snapshot: dict, request: dict, *, actor: str) -> dict:
        if actor not in {"user", "broker"}:
            raise ValueError("Reconciliation requires observed user/broker holdings")
        position = self._required("position", position_id)
        quantity = number(snapshot["current_quantity"])
        cost = number(snapshot["average_cost"])
        text(snapshot["source_ref"])
        changed = quantity != number(position["current_quantity"])
        position.update(current_quantity=str(quantity), average_cost=str(cost),
                        reconciliation_source_ref=snapshot["source_ref"], data_as_of=request["data_as_of"])
        updates = []
        if changed:
            for plan in self._sell_plans(position_id):
                if plan["status"] not in {"CANCELLED", "COMPLETED"}:
                    plan.update(status="NEEDS_REAPPROVAL", reapproval_reason="INVENTORY_CHANGED")
                    updates.append(("sell_plan", plan["sell_plan_id"], plan))
        return self._commit("position", position_id, position, {**request, "input": snapshot}, actor=actor,
                            event_type="POSITION_RECONCILED", updates=updates)

    @staticmethod
    def _user_confirmation(request: dict, actor: str):
        if actor != "user" or request.get("explicit_user_confirmation") is not True:
            raise ValueError("Explicit user confirmation required")

    def configure_strategy(self, value: dict, request: dict, *, actor: str) -> dict:
        self._user_confirmation(request, actor)
        group = strategy_group(value)
        group.setdefault("created_at", request["occurred_at"])
        state = self.get_entity("strategy_state", group["strategy_group_id"])
        updates = []
        if state:
            state["equity_reconciliation_required"] = True
            updates.append(("strategy_state", group["strategy_group_id"], state))
        return self._commit("strategy", group["strategy_group_id"], group, {**request, "input": value},
                            actor=actor, event_type="STRATEGY_ACCOUNTS_CHANGED", updates=updates)

    def configure_risk(self, value: dict, request: dict, *, actor: str) -> dict:
        self._user_confirmation(request, actor)
        ident = value["strategy_group_id"]
        self._required("strategy", ident)
        config = risk_config(value)
        config["effective_from"] = request["occurred_at"]
        return self._commit("risk_config", ident, config, {**request, "input": value}, actor=actor, event_type="RISK_CONFIG_CHANGED")

    def save_equity(self, group_id: str, value: dict, request: dict, *, actor: str) -> dict:
        if actor not in {"user", "broker", "engine"}:
            raise ValueError("LLM cannot establish account equity or cash")
        group = self._required("strategy", group_id)
        snapshot = equity_snapshot(group, value)
        ident = snapshot["equity_snapshot_id"]
        if self.get_entity("equity_snapshot", ident):
            raise ValueError("Equity snapshots are immutable; use a new ID")
        state = self.get_entity("strategy_state", group_id) or {"strategy_group_id": group_id}
        pending = deepcopy(state.get("unreconciled_buy_executions", {}))
        included = {encode([item["account_alias"], item["execution_id"]]) for item in value.get("included_execution_ids", [])}
        for execution_ref in included:
            pending.pop(execution_ref, None)
        spent: dict[str, str] = {}
        for execution in pending.values():
            currency = execution["currency"]
            spent[currency] = str(number(spent.get(currency, "0")) + number(execution["cash_amount"]))
        legacy_spent = state.get("cash_spent_since_snapshot", {})
        legacy_unknown = bool(legacy_spent) and (
            "unreconciled_buy_executions" not in state or state.get("legacy_cash_unreconciled") is True
        )
        if legacy_unknown:
            # A v3 state written before execution-level cash tracking cannot be
            # reset merely by uploading another balance. Preserve the unknown.
            spent = deepcopy(legacy_spent)
        reconciled = actor == "user" and request.get("explicit_user_confirmation") is True and value.get("cash_reconciliation_confirmed") is True
        if reconciled:
            pending, spent, legacy_unknown = {}, {}, False
        state.update(equity_snapshot_id=ident, cash_spent_since_snapshot=spent,
                     unreconciled_buy_executions=pending, cash_reconciliation_required=bool(pending) or legacy_unknown,
                     legacy_cash_unreconciled=legacy_unknown,
                     equity_reconciliation_required=False)
        return self._commit("equity_snapshot", ident, snapshot, {**request, "input": value}, actor=actor,
                            event_type="EQUITY_SNAPSHOT_CREATED", updates=[("strategy_state", group_id, state)])

    def position_market(self, position_id: str, value: dict, request: dict, *, actor: str) -> dict:
        if actor not in {"user", "broker", "engine"}:
            raise ValueError("Market observations cannot come from LLM interpretations")
        position = self._required("position", position_id)
        number(value["price"], positive=True)
        text(value["session"]); text(value["source"])
        position.update(current_market_price=value["price"], market_data_session=value["session"],
                        market_price_source=value["source"], market_price_as_of=request["data_as_of"])
        return self._commit("position", position_id, position, {**request, "input": value}, actor=actor, event_type="POSITION_MARKET_OBSERVED")

    def _budget_inputs(self, group_id: str):
        group = self._required("strategy", group_id)
        config = self.get_entity("risk_config", group_id) or risk_config({})
        state = self._required("strategy_state", group_id)
        if not state.get("equity_snapshot_id"):
            raise ValueError("Equity snapshot unavailable")
        snapshot = self._required("equity_snapshot", state["equity_snapshot_id"])
        if state.get("equity_reconciliation_required"):
            snapshot.setdefault("errors", []).append("STRATEGY_EQUITY_RECONCILIATION_REQUIRED")
        if state.get("cash_reconciliation_required"):
            snapshot.setdefault("errors", []).append("CASH_RECONCILIATION_REQUIRED")
        snapshot.setdefault("errors", []).extend(state.get("account_reconciliation_errors", []))
        # Legacy reservations have no R4 strategy allocation. Do not reuse their
        # cash/risk capacity until they are actually filled or released.
        for legacy in self.list_plans():
            if legacy["account_alias"] in group["included_account_ids"] and number(legacy["open_purchase_cash"]) > 0:
                snapshot.setdefault("errors", []).append("LEGACY_BUY_RESERVATIONS_UNRECONCILED")
        if snapshot.get("available_cash") is not None:
            for currency, spent in state.get("cash_spent_since_snapshot", {}).items():
                if currency in snapshot["available_cash"]:
                    amount = number(snapshot["available_cash"][currency]) - number(spent)
                    snapshot["available_cash"][currency] = str(max(amount, 0))
                    if amount < 0:
                        snapshot.setdefault("errors", []).append("POST_TRADE_CASH_DEFICIT")
        return group, config, snapshot

    def budget_status(self, group_id: str) -> dict:
        group, config, snapshot = self._budget_inputs(group_id)
        return review_budget(group, config, snapshot, self.entities("position"), self.entities("buy_plan"))

    def save_buy_plan(self, value: dict, request: dict, *, actor: str) -> dict:
        self._user_confirmation(request, actor)
        self._required("strategy", value["strategy_group_id"])
        old = self.get_entity("buy_plan", value["buy_plan_id"])
        if not old and value.get("plan_kind") == "REENTRY" and value.get("position_id") and self.get_entity("position", value["position_id"]):
            raise ValueError("Reentry requires a new position identity; prior positions remain historical")
        if old and old["status"] in {"CANCELLED", "COMPLETED"}:
            raise ValueError("Closed buy plan cannot be revived; create a new plan")
        plan = buy_draft(value, old, at=request['occurred_at'])
        return self._commit("buy_plan", plan["buy_plan_id"], plan, {**request, "input": value}, actor=actor, event_type="BUY_PLAN_SAVED")

    def _check_buy(self, plan: dict, at: str, *, positions: list[dict] | None = None) -> dict:
        try:
            group, config, snapshot = self._budget_inputs(plan["strategy_group_id"])
        except ValueError:
            result = deepcopy(plan)
            result.update(status="MANUAL_REQUIRED", approved_by_user=False, risk_review=None,
                          block_reasons=["CASH_UNAVAILABLE", "STRATEGY_EQUITY_UNAVAILABLE"])
            return result
        checked = approve_buy(plan, group, config, snapshot, positions if positions is not None else self.entities("position"),
                              self.entities("buy_plan"), self.entities("sell_plan"), actor="user", at=at)
        checked["risk_budget_config_id"] = config.get("risk_budget_config_id")
        checked["risk_budget_config_version"] = config.get("version")
        return checked

    def approve_buy_plan(self, plan_id: str, request: dict, *, actor: str) -> dict:
        self._user_confirmation(request, actor)
        plan = self._required("buy_plan", plan_id)
        if plan["status"] in {"CANCELLED", "COMPLETED"}:
            raise ValueError("Closed plan cannot be approved")
        checked = self._check_buy(plan, request["occurred_at"])
        if plan["status"] in RESERVING and checked["block_reasons"]:
            checked["status"] = "NEEDS_REAPPROVAL"
        return self._commit("buy_plan", plan_id, checked, request, actor=actor, event_type="BUY_PLAN_APPROVAL_REVIEWED")

    def trigger_buy_plan(self, plan_id: str, tranche_id: str, market_data: dict, request: dict, *, actor: str) -> dict:
        if actor not in {"user", "engine"}:
            raise ValueError("Only user/deterministic engine can evaluate a buy trigger")
        plan = self._required("buy_plan", plan_id)
        positions = self.entities("position")
        updates = []
        for position in positions:
            if position["position_id"] == plan.get("position_id"):
                position.update(current_market_price=market_data["price"], market_data_session=market_data["session"])
                updates.append(("position", position["position_id"], position))
        checked = self._check_buy(plan, request["occurred_at"], positions=positions)
        try:
            _, _, snapshot = self._budget_inputs(plan["strategy_group_id"])
        except ValueError:
            # Persist the failed revalidation and suspend the tranche, even when
            # the equity/cash snapshot is unavailable. Never infer a fill.
            checked["block_reasons"] = sorted({*checked["block_reasons"], "STRATEGY_EQUITY_UNAVAILABLE"})
        else:
            if market_data["session"] != snapshot["expected_sessions"].get(plan["market"]):
                checked["block_reasons"] = sorted({*checked["block_reasons"], "STALE_DATA"})
        manual = actor == "user" and request.get("explicit_user_confirmation") is True
        updated = trigger_buy(plan, tranche_id, market_data["price"], checked, manual_confirmed=manual, at=request['occurred_at'])
        updated["risk_review"] = checked.get("risk_review")
        return self._commit("buy_plan", plan_id, updated, {**request, "tranche_id": tranche_id, "input": market_data},
                            actor=actor, event_type="BUY_TRIGGER_REVIEWED", updates=updates)

    def cancel_buy(self, plan_id: str, request: dict, *, actor: str, tranche_id: str | None = None) -> dict:
        self._user_confirmation(request, actor)
        plan = self._required("buy_plan", plan_id)
        selected = plan["tranches"] if tranche_id is None else [t for t in plan["tranches"] if t["tranche_id"] == tranche_id]
        if not selected:
            raise ValueError("Unknown buy tranche")
        for t in selected:
            if t["status"] != "FILLED":
                t.update(status="CANCELLED", remaining_quantity="0", updated_at=request['occurred_at'],
                         cancelled_at=request['occurred_at'], cancellation_reason=request['reason'])
        if tranche_id is None or not any(number(t["remaining_quantity"]) for t in plan["tranches"]):
            plan["status"] = "CANCELLED"
        cash, risk = plan_reservation(plan)
        plan.update(total_reserved_cash=str(cash), total_nominal_planned_risk=str(risk),
                    total_remaining_quantity=str(sum((number(t['remaining_quantity']) for t in plan['tranches']), number('0'))),
                    approved_reservation={"cash": str(cash), "risk": str(risk)})
        return self._commit("buy_plan", plan_id, plan, {**request, "tranche_id": tranche_id}, actor=actor, event_type="BUY_RESERVATION_CANCELLED")

    def record_buy_fill(self, plan_id: str, tranche_id: str, fill: dict, request: dict, *, actor: str) -> dict:
        if actor not in {"user", "broker"}:
            raise ValueError("Actual fill requires user or broker source")
        plan = self._required("buy_plan", plan_id)
        external_ref = encode([plan["account_alias"], text(fill["execution_id"])])
        match = self.db.execute("SELECT payload_json FROM risk_events WHERE external_ref=?", (external_ref,)).fetchone()
        if match:
            old = json.loads(match[0])
            if old.get("execution") != {"plan_id": plan_id, "tranche_id": tranche_id, "fill": fill}:
                raise ValueError("Execution ID reused with different fill")
            return plan
        position = self.get_entity("position", plan["position_id"]) if plan.get("position_id") else None
        updated, position = buy_fill(plan, position, tranche_id, fill)
        stamp(fill["actual_at"])
        state = self.get_entity("strategy_state", plan["strategy_group_id"]) or {"strategy_group_id": plan["strategy_group_id"]}
        spent = state.setdefault("cash_spent_since_snapshot", {})
        spent[plan["currency"]] = str(number(spent.get(plan["currency"], "0")) + number(fill["actual_quantity"]) * number(fill["actual_price"]))
        state.setdefault("unreconciled_buy_executions", {})[external_ref] = {
            "account_alias": plan["account_alias"], "execution_id": fill["execution_id"],
            "actual_at": fill["actual_at"], "currency": plan["currency"],
            "cash_amount": str(number(fill["actual_quantity"]) * number(fill["actual_price"]))}
        positions = [p for p in self.entities("position") if p["position_id"] != position["position_id"]] + [position]
        # Actual execution prices remain separate from completed-session market observations.
        projected = deepcopy(position)
        projected.update(current_market_price=fill["actual_price"], market_data_session=fill["actual_at"][:10])
        post_positions = [p for p in positions if p["position_id"] != position["position_id"]] + [projected]
        post_plans = [p for p in self.entities("buy_plan") if p["buy_plan_id"] != plan_id] + [updated]
        try:
            group, config, snapshot = self._budget_inputs(plan["strategy_group_id"])
            checked = review_budget(group, config, snapshot, post_positions, post_plans)
        except (ValueError, KeyError, TypeError) as exc:
            # Actual fills are evidence, not approval requests. A missing or
            # unusable risk snapshot must not discard broker/user execution.
            checked = {"risk_budget_status": "UNAVAILABLE", "manual_required": True,
                       "block_reasons": ["POST_TRADE_DATA_UNAVAILABLE"], "data_error": str(exc)}
        updated["post_trade_review"] = checked
        if any(e in checked["block_reasons"] for e in ("OVER_BUDGET_REVIEW_REQUIRED", "RISK_BUDGET_EXCEEDED")):
            updated["post_trade_status"] = "POST_TRADE_RISK_BREACH"
        else:
            updated["post_trade_status"] = "MANUAL_REQUIRED" if checked["block_reasons"] else "WITHIN_BUDGET"
        command = {**request, "tranche_id": tranche_id,
                   "execution": {"plan_id": plan_id, "tranche_id": tranche_id, "fill": fill}}
        return self._commit("buy_plan", plan_id, updated, command, actor=actor, event_type="BUY_FILL_RECORDED", external_ref=external_ref,
                            updates=[("position", position["position_id"], position), ("strategy_state", plan["strategy_group_id"], state)])
