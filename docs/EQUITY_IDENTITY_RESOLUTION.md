# Equity Identity Resolution

TradeMirror prefers CUSIP for equity identity because ticker symbols can be reused,
renamed, or affected by corporate actions. Real statements may contain only a
symbol, so Sprint R1 adds a bounded resolver for opening equity anchors.

## Mapping Window

A symbol-only equity anchor may map to a canonical CUSIP only when sanitized
canonical equity records show exactly one nonblank CUSIP for the same normalized
symbol from the anchor date through the next 90 days. The window is intentionally
short and date-bounded so a later corporate action, rename, or unrelated future
record cannot rewrite historical identity.

A direct or resolved CUSIP is trusted only after validation. TradeMirror trims
surrounding whitespace, uppercases letters, and then requires the normalized
value to be exactly nine characters, contain only supported CUSIP characters,
and have a valid ninth-character check digit calculated from the first eight
characters with the standard CUSIP checksum algorithm.

Invalid, placeholder, truncated, malformed, or checksum-invalid CUSIPs fail
closed. TradeMirror never repairs, guesses, pads, truncates, or fabricates a
CUSIP, and rejected raw CUSIP values are not included in rejection metadata.

If a CUSIP is valid, TradeMirror uses it directly with high confidence. If no
valid CUSIP candidate exists, or more than one valid candidate exists in the
window, the resolver fails closed and keeps the anchor unresolved instead of
guessing.

## Confidence Labels

- `high_cusip_direct`: the anchor supplied a CUSIP.
- `resolved_symbol_to_cusip`: one CUSIP matched the anchor symbol inside the
  mapping window.
- `lower_symbol_only`: no CUSIP was available and symbol-only identity remains.
- `review`: identity or date data was malformed enough to require review.

The resolver records the original symbol/CUSIP, resolved key, confidence and
resolution reason as sanitized audit metadata. It does not include raw brokerage
descriptions, raw-row JSON, account identifiers or statement text.

## Unknown Basis

Resolved opening quantity can seed the position ledger and equity realized-P&L
engine, but it does not establish cost basis. When a later sale consumes anchored
quantity without trusted basis, TradeMirror reduces inventory and records the
closure as unknown basis. That quantity is excluded from included realized P&L;
TradeMirror never fabricates basis from statement value, closing price or market
value.

## Statement Comparison

Statement-position comparison should use the same resolved identity namespace as
the position ledger. A resolved statement symbol can match a calculated CUSIP key
only when the bounded resolver supports that mapping. Ambiguous identities remain
separate and visible for review.

## Limitations

This resolver handles date-bounded symbol-to-CUSIP equity anchors only. It does
not resolve option lifecycle issues, cash anchoring, tax-lot elections, wash
sales, or corporate-action transformations. Historical identity resolution fails
closed because a wrong merge can corrupt both position quantities and realized
P&L.
