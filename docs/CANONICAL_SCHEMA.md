# Canonical Financial Event Schema

The importer produces one canonical event for every nonblank source record.
Fields are intentionally explicit so downstream analytics never need to infer
accounting meaning from display text.

| Field | Meaning |
|---|---|
| `source_row_id` | Stable sequential record number within the import |
| `source_line_number` | Physical ending line in the original CSV |
| `activity_date` | Decision or economic-event date |
| `process_date` | Robinhood processing date |
| `settle_date` | Date cash or securities settle |
| `instrument` | Robinhood instrument label, which may reflect a later ticker |
| `description_raw` | Original description; private data only |
| `description_sanitized` | Description with account endings redacted |
| `cusip` | Security identifier extracted from the description |
| `transaction_code_raw` | Original Robinhood activity code |
| `transaction_family` | Trade, funding, fee, income, financing, transfer, etc. |
| `event_type` | Normalized economic event |
| `asset_type` | Equity, option, cash, event contract, or unknown |
| `quantity_raw` | Original quantity text |
| `quantity_numeric` | Parsed numeric quantity |
| `quantity_suffix` | Non-numeric suffix such as `S`, preserved for audit |
| `price` | Parsed unit price |
| `amount` | Signed cash amount; inflows positive, outflows negative |
| `cash_flow_direction` | Inflow, outflow, or none |
| `external_cash_flow` | Deposit or withdrawal crossing the portfolio boundary |
| `internal_transfer` | Movement between Robinhood sub-accounts |
| `option_underlying` | Parsed option underlying symbol |
| `option_expiration` | Parsed ISO expiration date |
| `option_type` | Call or put |
| `option_strike` | Parsed strike price |
| `potential_duplicate_group` | Stable fingerprint for identical-looking rows |
| `duplicate_group_size` | Number of rows sharing that fingerprint |
| `review_status` | `validated` or `review` |
| `review_reasons` | Pipe-separated reasons requiring attention |

## Important semantics

- Activity date drives behavioral analysis; settlement date drives cash
  reconciliation.
- Duplicate fingerprints are warnings, not deletion instructions. Multiple
  identical fills can be legitimate executions.
- Historical ticker identity must eventually be resolved with CUSIP and
  corporate-action mappings. A current ticker printed in a later export is not
  proof that it was the ticker at trade time.
- Source reports can omit cross-account crypto movements. Statement
  reconciliation may therefore require an explicit adjustment event rather
  than an invented trade.

