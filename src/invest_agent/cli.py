"""Small CLI entrypoint for deterministic engine operations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_MCP_URL = "https://korea-stock-analyzer-mcp-production.up.railway.app/mcp"


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="invest-agent")
    subcommands = parser.add_subparsers(dest="command", required=True)

    fetch = subcommands.add_parser("fetch-data", help="Fetch one normalized Korean stock DataBundle.")
    fetch.add_argument("ticker", help="Six-digit KOSPI/KOSDAQ ticker")
    fetch.add_argument("--years", type=int, default=5, choices=range(2, 11))
    fetch.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout")
    fetch.add_argument(
        "--mcp-url",
        default=os.getenv("KOREA_STOCK_MCP_URL", DEFAULT_MCP_URL),
        help="Streamable HTTP endpoint for Korea Stock MCP",
    )

    calculate = subcommands.add_parser(
        "calculate", help="Calculate C02 DCF and C08 warnings from a JSON file."
    )
    calculate.add_argument("input", type=Path, help="Path to the calculation input JSON")
    calculate.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout")

    analyze = subcommands.add_parser(
        "analyze", help="Fetch provider data and calculate C02/C08 from user assumptions."
    )
    analyze.add_argument("ticker", help="Six-digit KOSPI/KOSDAQ ticker")
    analyze.add_argument("assumptions", type=Path, help="Path to assumptions JSON")
    analyze.add_argument("--years", type=int, default=5, choices=range(3, 6))
    analyze.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format (default: json)",
    )
    analyze.add_argument("--output", type=Path, help="Write output to this path instead of stdout")
    analyze.add_argument(
        "--mcp-url",
        default=os.getenv("KOREA_STOCK_MCP_URL", DEFAULT_MCP_URL),
        help="Streamable HTTP endpoint for Korea Stock MCP",
    )
    for name in ("run", "resume", "status"):
        command = subcommands.add_parser(name, help=f"{name.capitalize()} the persistent morning pipeline")
        command.add_argument("--instance", type=Path, required=True)
        if name == "run":
            inputs = command.add_mutually_exclusive_group(required=True)
            inputs.add_argument("--fixture", type=Path, help="Explicit synthetic input")
            inputs.add_argument("--screen-config", type=Path, help="Read-only public market screening configuration")
            inputs.add_argument("--account-snapshot", type=Path, help="Synthetic or exported account JSON; no broker API calls")
            inputs.add_argument("--morning-config", type=Path, help="One report combining market, holdings and sourced valuation inputs")
            command.add_argument("--refresh", action="store_true", help="Create a new morning revision and recollect rather than reuse today's matching run")
            command.add_argument("--mode", choices=("morning",), default="morning")
            command.add_argument("--markets", nargs="+", choices=("KR", "US"), default=["KR", "US"])
            command.add_argument("--report-date", default=datetime.now(timezone(timedelta(hours=9))).date().isoformat())
        else:
            command.add_argument("run_id", nargs="?" if name == "status" else None)
        if name != "status":
            command.add_argument("--stop-after", choices=("snapshot", "summary", "market", "portfolio", "valuation"), help="Stop safely at a checkpoint to exercise resume")
        else:
            command.add_argument("--json", action="store_true", help="Status is always machine-readable JSON")
    plan=subcommands.add_parser("plan",help="Record user plans and local tranche events; never submit orders")
    actions=plan.add_subparsers(dest="plan_action",required=True)
    for name in ("record","event","status","policy-event","policy-status"):
        action=actions.add_parser(name)
        action.add_argument("--instance",type=Path,required=True)
        if name in ("status", "policy-status"):
            action.add_argument("plan_id",nargs="?")
        else:
            action.add_argument("--input",type=Path,required=True)
    backup=subcommands.add_parser('backup',help='Archive and verify the instance DB and referenced artifacts')
    backup.add_argument('--instance',type=Path,required=True)
    restore=subcommands.add_parser('restore',help='Verify an archive and restore to a new directory only')
    restore.add_argument('--archive',type=Path,required=True)
    restore.add_argument('--destination',type=Path,required=True)
    return parser


async def _fetch_data(args: argparse.Namespace) -> str:
    from .korea_stock_provider import KoreaStockMcpProvider
    bundle = await KoreaStockMcpProvider(args.mcp_url).fetch(args.ticker, args.years)
    return json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2)


async def _analyze(args: argparse.Namespace) -> dict:
    from .analysis_pipeline import calculate_from_data_bundles, requested_peer_tickers
    from .korea_stock_provider import KoreaStockMcpProvider
    assumptions = json.loads(args.assumptions.read_text(encoding="utf-8"))
    peer_tickers = requested_peer_tickers(assumptions)
    if args.ticker in peer_tickers:
        raise ValueError("The target ticker cannot also be listed as a peer.")
    provider = KoreaStockMcpProvider(args.mcp_url)
    target = await provider.fetch(args.ticker, args.years)
    # OpenDartReader performs process-global setup; keep DART-backed fetches serial.
    peers = [await provider.fetch(ticker, args.years) for ticker in peer_tickers]
    return calculate_from_data_bundles(target, assumptions, peers)


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_console()
    args = _parser().parse_args(argv)
    if args.command in ('backup','restore'):
        from .trading.backup import create, restore
        try:
            result=create(args.instance) if args.command=='backup' else restore(args.archive,args.destination)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0
        except Exception as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
            print(json.dumps({'error':str(exc),'type':type(exc).__name__},ensure_ascii=False),file=sys.stderr)
            return 1
    if args.command=="plan":
        from .trading.plans import PlanLedger
        from .trading.runner import writable_store
        try:
            if args.plan_action in ("status", "policy-status") and not (args.instance/"state.sqlite3").is_file():
                raise ValueError("No instance database")
            with writable_store(args.instance.resolve()) as store:
                ledger=PlanLedger(store)
                if args.plan_action in ("policy-event", "policy-status"):
                    from .trading.risk_ledger import RiskLedger
                    policy = RiskLedger(store)
                    if args.plan_action == "policy-event":
                        result = policy.execute_command(json.loads(args.input.read_text(encoding="utf-8-sig")))
                    else:
                        result = {"spec_version": "R1-R4/1.0", "authority": "local_user_plan_no_orders",
                                  "positions": policy.entities("position"), "monitors": policy.entities("monitor"),
                                  "sell_plans": policy.entities("sell_plan"), "candidates": policy.entities("candidate"),
                                  "buy_plans": policy.entities("buy_plan"), "strategy_groups": policy.entities("strategy"),
                                  "risk_configs": policy.entities("risk_config"), "equity_snapshots": policy.entities("equity_snapshot"),
                                  "audit": policy.audit()}
                elif args.plan_action=="status":
                    result=ledger.status(args.plan_id) if args.plan_id else {"plans":ledger.list_plans()}
                else:
                    data=json.loads(args.input.read_text(encoding="utf-8-sig"))
                    result=ledger.record(data) if args.plan_action=="record" else ledger.record_event(data)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0
        except (ValueError,KeyError,TypeError,OSError,RuntimeError,sqlite3.Error) as exc:
            print(json.dumps({"error":str(exc),"type":type(exc).__name__},ensure_ascii=False),file=sys.stderr)
            return 1
    if args.command in ("run", "resume", "status"):
        from .trading.runner import PipelineRunner
        try:
            runner = PipelineRunner(args.instance)
            if args.command == "run":
                if args.refresh and not args.morning_config:
                    raise ValueError("--refresh is only supported with --morning-config")
                result = (runner.run_morning(args.morning_config,args.report_date,args.stop_after,args.refresh) if args.morning_config else
                          runner.run_account(args.account_snapshot,args.report_date,args.stop_after) if args.account_snapshot else
                          runner.run_market(args.screen_config, args.report_date, args.stop_after) if args.screen_config
                          else runner.run(args.fixture, args.report_date, args.markets, args.stop_after))
            elif args.command == "resume":
                result = runner.resume(args.run_id, args.stop_after)
            else:
                result = runner.status(args.run_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result.get('backup',{}).get('state')=='unavailable':
                return 2  # Report/run state remains separate from backup failure.
            return 0 if args.command == "status" or result["state"] == "succeeded" else (2 if result["state"] in ("interrupted", "partial") else 1)
        except (ValueError, OSError, RuntimeError, KeyError, sqlite3.Error) as exc:
            print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
            return 1
    if args.command == "fetch-data":
        payload = asyncio.run(_fetch_data(args))
    elif args.command == "calculate":
        from .calculators import calculate_analysis
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        payload = json.dumps(calculate_analysis(raw), ensure_ascii=False, indent=2)
    elif args.command == "analyze":
        from .reporting import render_markdown_report
        result = asyncio.run(_analyze(args))
        payload = (
            render_markdown_report(result)
            if args.format == "markdown"
            else json.dumps(result, ensure_ascii=False, indent=2)
        )
    else:
        raise AssertionError(f"Unsupported command: {args.command}")
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
