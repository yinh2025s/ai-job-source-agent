# v244 Page-Evidenced Provider Career - Phase C

## Result

The S4 contract-boundary fix is accepted. A provider page on the verified
first-party site family can now establish a Career input when the Provider
Registry derives one strict listing-capable board and tenant from the fetched
page. It still cannot publish a Job List or opening by itself.

The focused run used the two Fresh100 Hawaiian Electric records and entirely
new checkpoint, completion, evidence and snapshot roots:

`/private/tmp/fresh2-v244-hawaiian-run1`

| LinkedIn job | Career | Verified Job List | Provider / tenant | Terminal |
| --- | --- | --- | --- | --- |
| Cyber Operations Analyst - Oahu | `https://careers.hawaiianelectric.com` | `https://careers.hawaiianelectric.com/search/` | SuccessFactors / `custom:hawaiianel` | Verified No Match |
| Information Assurance Analyst - Oahu | `https://careers.hawaiianelectric.com` | `https://careers.hawaiianelectric.com/search/` | SuccessFactors / `custom:hawaiianel` | Verified No Match |

For both records, the hiring entity is verified as Hawaiian Electric and the
provider relationship is verified from the first-party provider page. The
native SuccessFactors adapter completed its declared pagination. It inspected
84 candidates for Cyber Operations Analyst and 25 candidates for Information
Assurance Analyst. The strongest observed title scores were 65 and 150,
respectively; the second record also failed the required location match.

No opening URL was published. This is an evidence-backed
`verified_inventory_no_match`, not a transport fallback or an inferred closed
posting.

## Replay And Safety

- Same-version scoped replay: 2/2 reproduced.
- Fixture gaps: 0.
- Tape divergence: 0.
- Wrong opening URL: 0.
- Cross-company or cross-tenant publication: 0.
- Adapter version: `2026-07-27.244`.
- Relevant integrated tests: 458 passed.
- Provider benchmark: 25/25 passed.
- Resolver benchmark: 6/6 passed.
- Architecture validation: 46 adapters / 0 issues.
- `git diff --check`: clean.

The canonical replay summary is:

`/private/tmp/fresh2-v244-hawaiian-run1/replay/replay-summary.json`

## Projection

These are the same two LinkedIn job IDs present in the Fresh100 development
cohort. They replace two unresolved `JOB_BOARD_NOT_FOUND` projections:

- The `.244` runtime delta leaves Exact unchanged.
- Verified No Match increases from 10 to 12.
- External Blocked remains 1.
- The `.244` runtime delta reduces unresolved from 53 to 51.

Rebuilding the ordered ledger also exposed an older acceptance-manifest
omission: iClassPro job `4441446072` was already audited as an S7 Exact in the
code-frozen `.243` eight-record run and reproduced in its 8/8 replay, but was
still projected as unresolved. Adding that existing reviewed terminal makes
the reconciled current projection 37 Exact, 12 Verified No Match,
1 External Blocked and 50 unresolved. This is a governance correction, not an
additional `.244` product-code recovery.

This is a reviewed development projection from a focused code-frozen run. It is
not an official 100-record cold rerun and does not establish a multi-company
recall cluster. Hawaiian Electric contributes two records but only one
independent employer.

## Causal Decision

The fix closes one generic contract-boundary defect: strict page-derived
provider evidence was available but S4 discarded it before S5. It does not
authorize broad provider-host guessing, cross-site Career recognition, relaxed
tenant identity or a larger fetch budget.

A separate audit of records that appeared to need “one more search action”
found four recoverable examples, but they split across four code paths:
declared GET result projection, pagination URL safety, static detail-card
identity and S7 checkpoint continuity. They are not one implementation cluster.
No generic interactive-form relaxation is authorized.

Plugin, coordinator-v2, LLM and sealed blind cohort work remain unchanged and
out of scope.
