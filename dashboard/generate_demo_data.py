from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from trademirror.cash_ledger import build_cash_ledger, write_cash_ledger_outputs
from trademirror.equity_realized_pnl import build_equity_realized_pnl, write_equity_realized_pnl_outputs
from trademirror.option_realized_pnl import build_option_realized_pnl, write_option_realized_pnl_outputs
from trademirror.position_ledger import build_position_ledger, write_position_ledger_outputs


AS_OF = date(2021, 12, 31)


def generate_demo_data(destination: str | Path | None = None) -> Path:
    root = Path(destination or Path(__file__).resolve().parents[1] / "demo" / "dashboard_data")
    records = synthetic_records()
    anchors = synthetic_anchors()
    _reset_output_dirs(root)
    cash = build_cash_ledger(
        records,
        as_of=AS_OF,
        opening_cash=Decimal("10000"),
        opening_date=date(2019, 1, 1),
    )
    positions = build_position_ledger(records, as_of=AS_OF, anchors=anchors)
    equity = build_equity_realized_pnl(records, as_of=AS_OF, anchors=anchors)
    options = build_option_realized_pnl(records, as_of=AS_OF, anchors=anchors)
    write_cash_ledger_outputs(cash, root / "cash_ledger")
    write_position_ledger_outputs(positions, root / "position_ledger")
    write_equity_realized_pnl_outputs(equity, root / "realized_pnl")
    write_option_realized_pnl_outputs(options, root / "option_realized_pnl")
    return root


def synthetic_records() -> list[dict[str, str]]:
    return [
        _cash(1, "2019-01-01", "ACH", "deposit", "10000", external=True),
        _equity(2, "2019-01-10", "2019-01-14", "ACME", "111111111", "Buy", "buy", "10", "-1000"),
        _equity(3, "2019-08-15", "2019-08-19", "ACME", "111111111", "Sell", "sell", "4", "600"),
        _option(4, "2019-02-01", "2019-02-05", "ACME", "2019-06-21", "call", "7.50", "BTO", "buy_to_open", "1", "-120", "444444444"),
        _option(5, "2019-05-20", "2019-05-22", "ACME", "2019-06-21", "call", "7.50", "STC", "sell_to_close", "1", "180", "444444444"),
        _cash(6, "2019-09-20", "CDIV", "dividend", "18.75", family="income"),
        _equity(7, "2020-01-12", "2020-01-14", "ZETA", "333333333", "Buy", "buy", "2", "-400"),
        _equity(8, "2020-03-05", "2020-03-09", "ZETA", "333333333", "Sell", "sell", "2", "300"),
        _option(9, "2020-01-20", "2020-01-22", "BETA", "2020-03-20", "put", "20.00", "STO", "sell_to_open", "1", "90", "555555555"),
        _option(10, "2020-03-10", "2020-03-12", "BETA", "2020-03-20", "put", "20.00", "BTC", "buy_to_close", "1", "-140", "555555555"),
        _cash(11, "2020-04-15", "AFEE", "adr_fee", "-5", family="fee"),
        _cash(12, "2020-06-01", "MINT", "margin_interest", "-8.25", family="financing"),
        _cash(13, "2020-11-02", "INT", "internal_transfer", "25", family="internal_transfer", internal=True),
        _equity(14, "2021-01-04", "2021-01-06", "BETA", "222222222", "Buy", "buy", "5", "-500"),
        _equity(15, "2021-02-01", "2021-02-03", "GAMM", "777777777", "Sell", "sell", "1", "80"),
        _option(16, "2021-02-01", "2021-02-03", "FOO", "2021-04-16", "call", "15.00", "BTO", "buy_to_open", "1", "-50", "666666666"),
        _option(17, "2021-03-15", "2021-03-17", "FOO", "2021-04-16", "call", "15.00", "OEXCS", "exercise", "1", "0", "666666666"),
        _option(18, "2021-02-10", "2021-02-12", "OMEG", "2021-03-19", "call", "5.00", "STC", "sell_to_close", "1", "30", "888888888"),
        _option(19, "2021-03-18", "2021-03-19", "ACME", "2021-03-19", "call", "7.50", "STC", "sell_to_close", "1", "5", "444444444"),
        _equity(20, "2021-12-28", "2022-01-03", "DELT", "999999999", "Buy", "buy", "2", "-200"),
        _cash(21, "2021-12-31", "ACH", "withdrawal", "-250", external=True),
        _equity(22, "2021-06-01", "2021-06-03", "EPSI", "121212121", "Buy", "buy", "3", "-300"),
        _equity(23, "2021-07-01", "2021-07-06", "EPSI", "121212121", "Sell", "sell", "3", "360"),
    ]


def synthetic_anchors() -> list[dict[str, str]]:
    return [
        {
            "anchor_date": "2021-01-15",
            "asset_type": "equity",
            "symbol": "GAMM",
            "cusip": "777777777",
            "quantity": "2",
        },
        {
            "anchor_date": "2021-02-05",
            "asset_type": "option",
            "option_underlying": "OMEG",
            "option_expiration": "2021-03-19",
            "option_type": "call",
            "option_strike": "5.00",
            "cusip": "888888888",
            "quantity": "1",
        },
    ]


def _cash(
    row_id: int,
    activity_date: str,
    code: str,
    event_type: str,
    amount: str,
    *,
    family: str = "funding",
    external: bool = False,
    internal: bool = False,
) -> dict[str, str]:
    return {
        "source_row_id": str(row_id),
        "activity_date": activity_date,
        "process_date": activity_date,
        "settle_date": activity_date,
        "instrument": "",
        "cusip": "",
        "transaction_code_raw": code,
        "transaction_family": family,
        "event_type": event_type,
        "asset_type": "cash",
        "quantity_numeric": "",
        "amount": amount,
        "external_cash_flow": str(external).lower(),
        "internal_transfer": str(internal).lower(),
        "review_status": "validated",
        "review_reasons": "",
    }


def _equity(
    row_id: int,
    activity_date: str,
    settle_date: str,
    symbol: str,
    cusip: str,
    code: str,
    event_type: str,
    quantity: str,
    amount: str,
) -> dict[str, str]:
    return {
        "source_row_id": str(row_id),
        "activity_date": activity_date,
        "process_date": activity_date,
        "settle_date": settle_date,
        "instrument": symbol,
        "cusip": cusip,
        "transaction_code_raw": code,
        "transaction_family": "trade",
        "event_type": event_type,
        "asset_type": "equity",
        "quantity_numeric": quantity,
        "amount": amount,
        "external_cash_flow": "false",
        "internal_transfer": "false",
        "review_status": "validated",
        "review_reasons": "",
    }


def _option(
    row_id: int,
    activity_date: str,
    settle_date: str,
    underlying: str,
    expiration: str,
    option_type: str,
    strike: str,
    code: str,
    event_type: str,
    quantity: str,
    amount: str,
    cusip: str,
) -> dict[str, str]:
    return {
        "source_row_id": str(row_id),
        "activity_date": activity_date,
        "process_date": activity_date,
        "settle_date": settle_date,
        "instrument": underlying,
        "cusip": cusip,
        "transaction_code_raw": code,
        "transaction_family": "option_lifecycle" if event_type in {"exercise", "assignment", "expiration"} else "option_trade",
        "event_type": event_type,
        "asset_type": "option",
        "quantity_numeric": quantity,
        "amount": amount,
        "external_cash_flow": "false",
        "internal_transfer": "false",
        "option_underlying": underlying,
        "option_expiration": expiration,
        "option_type": option_type,
        "option_strike": strike,
        "review_status": "validated",
        "review_reasons": "",
    }


def _reset_output_dirs(root: Path) -> None:
    for child in ("cash_ledger", "position_ledger", "realized_pnl", "option_realized_pnl"):
        target = root / child
        target.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print(generate_demo_data())
