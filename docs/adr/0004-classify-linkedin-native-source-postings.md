# ADR-0004: Classify LinkedIn-Native Source Postings Without Inventing Official URLs

- Status: accepted
- Date: 2026-07-13

## Context

Some source postings are visibly active in an authenticated LinkedIn Jobs detail
page but expose only LinkedIn's native Apply control. Public search cards do not
reliably reveal the apply channel, and the company may have no discoverable public
job board. Treating this state as a generic failure loses useful source evidence;
treating the LinkedIn URL as an official company opening or job list would violate
the product's URL provenance rules.

This decision changes S5 and top-level partial-success semantics. It also affects
checkpoint compatibility and evaluation, so the producer, consumer, precedence,
privacy, and version rules must be fixed before parallel adapters use it.

## Decision

### Source Evidence Contract

`CompanyInput.source_trace.linkedin_posting` may contain:

```json
{
  "availability": "active|listed|closed|expired|unavailable|unknown",
  "apply_mode": "linkedin_native|external|unknown",
  "evidence_source": "authenticated_detail_dom|public_search_card",
  "job_url": "https://www.linkedin.com/jobs/view/...",
  "observed_at": "optional timestamp"
}
```

1. Public LinkedIn search cards produce only `listed + unknown` with
   `evidence_source=public_search_card`. A missing External Apply URL never implies
   LinkedIn-native apply.
2. The browser evidence adapter may produce `active + linkedin_native` only from
   a visible, enabled native Apply control in the authenticated detail DOM. A
   visible External Apply control produces `active + external`; an explicit closed
   banner produces `closed + unknown`; missing, hidden, or disabled controls remain
   `unknown + unknown`.
3. S5 trusts the native tuple only when the evidence source is
   `authenticated_detail_dom`, the source URL is canonical HTTPS LinkedIn Jobs,
   and it matches the record's LinkedIn job URL. Malformed, cross-record, unknown,
   or partial evidence is inconclusive.

### Stage And Result Semantics

1. A verified company job board or supported External Apply provider always wins
   over source-channel evidence.
2. Network, provider, parser, budget, or other incomplete discovery evidence keeps
   its original failure. Source evidence cannot turn a retryable or incomplete
   attempt into a business terminal.
3. Only a deterministic `CAREER_PAGE_NOT_FOUND` or complete
   `JOB_BOARD_NOT_FOUND`, combined with trusted active LinkedIn-native evidence,
   makes S5 return `partial` with reason `LINKEDIN_NATIVE_ONLY` and typed
   `source_posting_availability` evidence.
4. This terminal creates no `career_page_url`, `job_list_page_url`, or
   `open_position_url`. The LinkedIn URL remains source evidence, not an official
   company URL. S6 is `not_run`; S7 and both top-level status fields are `partial`.
5. `unsupported` remains reserved for a recognized implementation/provider
   boundary such as an unknown External Apply provider. `LINKEDIN_NATIVE_ONLY`
   means the system has truthfully classified the source channel, not that the
   requested official URL was found.

### Compatibility And Evaluation

1. Result and stage schemas do not change because the contract uses existing
   `source_trace`, reason, evidence, and status fields.
2. `ADAPTER_VERSION` advances to `2026-07-13.44`. The input fingerprint includes
   only `availability`, `apply_mode`, `evidence_source`, and `job_url`; volatile
   `observed_at` and unrelated trace metrics do not invalidate checkpoints.
3. Evaluation records `source_posting_disposition_counts` separately from the S6
   `availability_diagnostic_counts`; source-channel evidence must not redefine
   official provider inventory diagnostics.

### Privacy

Only normalized status, apply mode, provenance label, and public job URL cross the
browser boundary. The extension and checkpoint must not persist cookies, tokens,
headers, browser storage, personal profile data, or authenticated page HTML.

## Consequences

Positive:

- Active LinkedIn-native postings become an explicit, testable partial outcome.
- Public search cannot manufacture native-apply evidence from a missing link.
- Reporting separates channel divergence from ATS/provider failure and from a
  verified official inventory no-match.

Costs and limits:

- The outcome still does not satisfy the official company job-list/opening goal.
- Only the active detail record can carry high-trust apply-mode evidence; other
  visible search cards remain conservative until selected in the authenticated DOM.
- Automated fixtures validate the contract, but one real installed-extension scan
  remains a separate acceptance gate.

## Validation

- Browser DOM tests cover visible native Apply, External Apply, closed banner,
  missing controls, and hidden/disabled controls.
- Public discovery tests cover `listed + unknown`, absent External Apply, and trace
  merge compatibility.
- Source classifier tests cover malformed/mismatched URLs, untrusted provenance,
  unknown values, and closed-state precedence.
- S5 tests cover deterministic career/board miss, verified-board precedence, and
  incomplete/retryable failure precedence.
- Pipeline, checkpoint, and evaluation tests cover dual partial status, reason
  propagation, stable fingerprinting, and separate source/S6 counters.
- The integrated `.44` offline gate passes 640 tests, 21/21 provider cases, 6/6
  resolver cases, and architecture validation with 19 adapters and 0 issues.
