from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from trademirror.importer import import_robinhood_csv, write_canonical_csv, write_report
from trademirror.reconciliation import CashAnchor, ReconciliationAdjustment, reconcile_cash


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("anchors", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include raw descriptions and raw-row JSON in private canonical output",
    )
    args = parser.parse_args()

    records, quality = import_robinhood_csv(args.csv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.include_raw:
        print(
            "PRIVACY WARNING: --include-raw writes raw descriptions and raw-row JSON. "
            "Keep this output out of Git and shared folders."
        )
    write_canonical_csv(
        records,
        args.output_dir / "canonical_transactions.csv",
        include_raw=args.include_raw,
    )
    write_report(quality, args.output_dir / "data_quality_report.json")

    anchor_data = json.loads(args.anchors.read_text(encoding="utf-8"))
    results = []
    for item in anchor_data["anchors"]:
        anchor = CashAnchor(
            label=item["label"],
            start_date=date.fromisoformat(item["start_date"]),
            end_date=date.fromisoformat(item["end_date"]),
            opening_cash=Decimal(item["opening_cash"]),
            closing_cash=Decimal(item["closing_cash"]),
        )
        adjustments = [
            ReconciliationAdjustment(
                label=adjustment["label"],
                amount=Decimal(adjustment["amount"]),
                reason=adjustment["reason"],
            )
            for adjustment in item.get("adjustments", [])
        ]
        results.append(reconcile_cash(records, anchor, adjustments))

    private_results = {"quality": quality, "reconciliations": results}
    (args.output_dir / "private_validation.json").write_text(
        json.dumps(private_results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    safe_summary = {
        "canonical_records": quality["canonical_records"],
        "date_range": quality["date_range"],
        "option_parse_rate": quality["option_parse_rate"],
        "review_rows": quality["review_rows"],
        "reconciliations": [
            {
                "label": result["label"],
                "passed": result["passed"],
                "absolute_difference": str(abs(Decimal(str(result["difference"])))),
                "documented_adjustment_count": result["adjustment_count"],
                "invalid_row_count": result["invalid_row_count"],
                "review_reasons": result["review_reasons"],
            }
            for result in results
        ],
    }
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    (reports / "sprint1_validation_summary.json").write_text(
        json.dumps(safe_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
