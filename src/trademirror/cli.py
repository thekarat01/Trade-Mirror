from __future__ import annotations

import argparse
from pathlib import Path

from .importer import import_robinhood_csv, write_canonical_csv, write_report


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


if __name__ == "__main__":
    main()
