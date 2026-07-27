# Coordinator `.235` inventory causal audit

## Conclusion

The current `OPENING_DISCOVERY_INCOMPLETE` records do not form one executable
cluster. The same terminal stage hides different mechanisms:

| Mechanism | Independent companies | Decision |
| --- | --- | --- |
| Exact-title detail exists but detail identity is not parsed | Lorum, StatRad | two companies only |
| Declared GET search runs but completeness is unknown | System One, DSV | two companies only |
| Declared AJAX inventory lacks total/next proof | Conrad | singleton |
| No safe declared recruiting transport | Tyler, Sentar, Necessary, HP, Kelly, WENDEL | different page mechanisms |
| Pagination URL fails the safety gate | Equifax | retain fail-closed behavior |
| S5 selected the wrong page layer | Kelly | upstream selection issue |
| Provider/interaction family not recognized | Cretex, Home Depot | different provider mechanisms |

Lorum contains HTML-escaped JobPosting JSON in a controlled detail-page
container. StatRad exposes location only in prose. These must not be combined
into an unrestricted body-text location extractor.

System One and DSV use declared GET forms. Conrad uses a declared AJAX
inventory whose response does not attest completeness. A shared terminal reason
does not justify treating those transports as one contract.

## Ledger reconciliation

The development matrix count of 14 incomplete records is correct:

```text
.220 incomplete records                         12
- Mayo later replaced by identity rejection     1
- Lorum replaced by the .222 cold retryable     1
+ System One / two WENDEL / Conrad               4
= current incomplete records                    14
```

The separate Lorum injected-evidence opening-path success is excluded from the
projection, while its cold-input retryable result is included.

## Decision

No implementation follows from this audit. Continue searching for a third
company with a controlled, first-party, opening-bound structured detail
container before changing detail metadata parsing. Do not relax location,
pagination, completeness or S7 gates.
