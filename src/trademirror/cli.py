from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path

from .behavioral_insights import (
    build_behavioral_insights,
    write_behavioral_insights_outputs,
)
from .cash_ledger import build_cash_ledger, write_cash_ledger_outputs
from .equity_realized_pnl import (
    build_equity_realized_pnl,
    write_equity_realized_pnl_outputs,
)
from .importer import import_robinhood_csv, write_canonical_csv, write_report
from .option_realized_pnl import (
    build_option_realized_pnl,
    write_option_realized_pnl_outputs,
)
from .position_ledger import (
    build_position_ledger,
    load_position_anchors,
    write_position_ledger_outputs,
)
from .trusted_trades import build_trusted_trade_dataset, write_trusted_trade_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trademirror")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Normalize a Robinhood activity CSV")
    importer.add_argument("input", type=Path)
    importer.add_argument("--output", type=Path, required=True)
    importer.add_argument("--report", type=Path, required=True)
    importer.add_argument(
        "--include-raw",
        action="store_true",
        help="Include raw descriptions and raw-row JSON in the canonical CSV",
    )
    cash_ledger = commands.add_parser("cash-ledger", help="Generate a settled cash ledger")
    cash_ledger.add_argument("input", type=Path)
    cash_ledger.add_argument("--output-dir", type=Path, required=True)
    cash_ledger.add_argument("--as-of", type=date.fromisoformat)
    cash_ledger.add_argument("--opening-cash", type=Decimal)
    cash_ledger.add_argument("--opening-date", type=date.fromisoformat)
    position_ledger = commands.add_parser(
        "position-ledger",
        help="Generate trade-date and settled position ledgers",
    )
    position_ledger.add_argument("input", type=Path)
    position_ledger.add_argument("--output-dir", type=Path, required=True)
    position_ledger.add_argument("--as-of", type=date.fromisoformat)
    position_ledger.add_argument("--anchors", type=Path)
    realized_pnl = commands.add_parser(
        "realized-pnl",
        help="Generate analytical FIFO equity realized P&L outputs",
    )
    realized_pnl.add_argument("input", type=Path)
    realized_pnl.add_argument("--output-dir", type=Path, required=True)
    realized_pnl.add_argument("--as-of", type=date.fromisoformat)
    realized_pnl.add_argument("--anchors", type=Path)
    option_realized_pnl = commands.add_parser(
        "option-realized-pnl",
        help="Generate analytical FIFO option realized P&L outputs",
    )
    option_realized_pnl.add_argument("input", type=Path)
    option_realized_pnl.add_argument("--output-dir", type=Path, required=True)
    option_realized_pnl.add_argument("--as-of", type=date.fromisoformat)
    option_realized_pnl.add_argument("--anchors", type=Path)
    trusted_trades = commands.add_parser(
        "trusted-trades",
        help="Classify completed realized-P&L lots by confidence",
    )
    trusted_trades.add_argument("--equity-dir", type=Path, required=True)
    trusted_trades.add_argument("--option-dir", type=Path, required=True)
    trusted_trades.add_argument("--output-dir", type=Path, required=True)
    behavioral_insights = commands.add_parser(
        "behavioral-insights",
        help="Generate deterministic behavioral insight metrics from trusted trades",
    )
    behavioral_insights.add_argument("--trusted-dir", type=Path, required=True)
    behavioral_insights.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "import":
        records, report = import_robinhood_csv(args.input)
        if args.include_raw:
            print(
                "PRIVACY WARNING: --include-raw writes raw descriptions and raw-row JSON. "
                "Keep this output out of Git and shared folders."
            )
        write_canonical_csv(records, args.output, include_raw=args.include_raw)
        write_report(report, args.report)
        print(f"Imported {report['canonical_records']} records")
        print(f"Review queue: {report['review_rows']} records")
        print(f"Quality report: {args.report}")
    elif args.command == "cash-ledger":
        records, _ = import_robinhood_csv(args.input)
        result = build_cash_ledger(
            records,
            as_of=args.as_of,
            opening_cash=args.opening_cash,
            opening_date=args.opening_date,
        )
        write_cash_ledger_outputs(result, args.output_dir)
        print(f"Cash ledger events: {result['summary']['event_count']}")
        print(f"Pending settlement: {result['summary']['pending_settlement_count']}")
        print(f"Review issues: {result['summary']['review_count']}")
        print(f"Output directory: {args.output_dir}")
    elif args.command == "position-ledger":
        records, _ = import_robinhood_csv(args.input)
        anchors = load_position_anchors(args.anchors) if args.anchors else []
        result = build_position_ledger(records, as_of=args.as_of, anchors=anchors)
        write_position_ledger_outputs(result, args.output_dir)
        print(f"Position events: {result['summary']['event_count']}")
        print(f"Positions as of: {result['summary']['position_count']}")
        print(f"Pending settlement: {result['summary']['pending_settlement_count']}")
        print(f"Review issues: {result['summary']['review_count']}")
        print(f"Output directory: {args.output_dir}")
    elif args.command == "realized-pnl":
        records, _ = import_robinhood_csv(args.input)
        anchors = load_position_anchors(args.anchors) if args.anchors else []
        result = build_equity_realized_pnl(records, as_of=args.as_of, anchors=anchors)
        write_equity_realized_pnl_outputs(result, args.output_dir)
        print(f"Lot matches: {result['summary']['match_count']}")
        print(f"Open lots: {result['summary']['open_lot_count']}")
        print(f"Net realized P&L: {result['summary']['net_realized_pnl']}")
        print(f"Review issues: {result['summary']['review_count']}")
        print(f"Output directory: {args.output_dir}")
    elif args.command == "option-realized-pnl":
        records, _ = import_robinhood_csv(args.input)
        anchors = load_position_anchors(args.anchors) if args.anchors else []
        result = build_option_realized_pnl(records, as_of=args.as_of, anchors=anchors)
        write_option_realized_pnl_outputs(result, args.output_dir)
        print(f"Option lot matches: {result['summary']['match_count']}")
        print(f"Option open lots: {result['summary']['open_lot_count']}")
        print(f"Option basis transfers: {result['summary']['basis_transfer_count']}")
        print(f"Net option realized P&L: {result['summary']['net_realized_pnl']}")
        print(f"Review issues: {result['summary']['review_count']}")
        print(f"Output directory: {args.output_dir}")
    elif args.command == "trusted-trades":
        result = build_trusted_trade_dataset(
            equity_dir=args.equity_dir,
            option_dir=args.option_dir,
        )
        write_trusted_trade_outputs(result, args.output_dir)
        coverage = result["coverage_summary"]["confidence"]
        print(f"Completed matches evaluated: {result['coverage_summary']['total_completed_matches_evaluated']}")
        print(f"High confidence: {coverage['high_confidence']['count']}")
        print(f"Limited confidence: {coverage['limited_confidence']['count']}")
        print(f"Excluded: {coverage['excluded']['count']}")
        print(f"Output directory: {args.output_dir}")
    elif args.command == "behavioral-insights":
        result = build_behavioral_insights(trusted_dir=args.trusted_dir)
        write_behavioral_insights_outputs(result, args.output_dir)
        summary = result["behavioral_summary"]
        print(f"High-confidence trades analyzed: {summary['high_confidence_trade_count']}")
        print(f"Primary insight candidates: {len(result['insight_candidates'])}")
        print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
