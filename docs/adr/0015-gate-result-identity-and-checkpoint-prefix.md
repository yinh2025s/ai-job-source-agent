# ADR-0015: Gate Result Identity And Checkpoint Prefix

- Status: accepted
- Date: 2026-07-14

## Context

Aggregate website, job-list, and exact-opening rates can stay green while one
company silently receives another company's URL, a provider tenant changes, or
an opening URL drifts. The previous expectation contract primarily checked
stage outcomes and therefore could not make these swaps release-blocking.

Checkpoint reuse also had more than one interpretation. The application runner,
live batch resume, automatic completion retry, and parent-timeout recovery each
implemented part of the prefix policy. A missing or malformed upstream record
could consequently cause one entry point to recompute while another restored a
later record. Replay input fields were also capable of looking like a substitute
for durable stage checkpoints even though they do not prove execution identity.

## Decision

### Company result identity

1. Benchmark expectations may define `expected_identity` for any verified subset
   of the website, career page, job board, and opening. Every declared field is
   strict; an empty identity declaration fails closed. Job-board identity
   contains the provider, a public tenant locator, and its canonical URL.
2. Identity comparison uses strict canonical public HTTP(S) URLs. It rejects
   credentials, malformed or encoded controls, private/local hosts, and
   nonstandard identity transformations. Scheme, host, path, meaningful query,
   requisition identifiers, and semantic fragments remain part of identity.
3. Only declared tracking parameters may be removed. Finite explicit aliases may
   represent a known canonical migration or `www` spelling; regex, wildcard, and
   inferred aliases are forbidden.
4. A public job-board tenant is `url:<canonical board URL>`. Runtime locator IDs,
   credentials, request headers, cookies, tokens, authenticated payloads, and
   provider secrets never enter expectations, summaries, or reports.
5. Duplicate company names in one evaluated cohort fail the identity gate rather
   than being merged. Every expected company gets one deterministic matrix row.
6. Baseline comparison reports added, removed, and field-level changed company
   identity. A legacy baseline without the matrix is explicitly unavailable; it
   is not interpreted as zero drift.
7. Identity failures are expectation failures even when aggregate hit rates are
   unchanged. Human-readable reports expose only company names and stable failure
   codes, with bounded output.

### Authoritative checkpoint prefix

8. One shared inspector defines reusable checkpoint state for all entry points.
   It starts from a fresh context and accepts only the contiguous prefix before a
   requested boundary.
9. A reusable record must match its stage, use a stage-valid `success` or
   `not_applicable` status, apply cleanly in sequence, contain each required stage
   output, and expose only safe normalized public URL updates. Unknown, damaged,
   incompatible, or semantically invalid records are prefix defects.
10. The earliest defect is the only safe recomputation boundary. Records after a
    defect are never restored, even when they are individually well formed.
11. Resume and automatic retry fall back to the earliest defect and invalidate
    the stale suffix before execution. Replay company fields do not bypass a
    missing authoritative prefix.
12. An explicit rerun is stricter: if the prefix before its requested stage is
    defective, it fails before executing a child process or mutating checkpoint
    state. CLI output artifacts are not written on that failure.
13. Parent-timeout recovery inspects through an explicit end-of-pipeline boundary
    and restores only the same contiguous prefix. A complete seven-stage prefix
    may be returned as the durable result; a partial prefix remains evidence for
    the typed outer-budget failure.
14. Trace diagnostics contain only mode, requested/effective boundaries, stable
    defect classes and stage names, plus the invalidated suffix. They do not copy
    checkpoint values, URLs, paths, payloads, or credentials.

## Consequences

- Release gates detect company swaps and provider/tenant/opening drift instead of
  relying on aggregate rates.
- Provider adapters keep ownership of provider-specific locator verification;
  the central evaluator compares only the resulting public identity contract.
- CLI, batch resume, automatic retry, rerun, and timeout recovery cannot disagree
  about which checkpoint records are reusable.
- Resume may recompute more work after a gap, but it cannot claim a result from a
  non-contiguous or replay-injected chain.
- Existing evaluation summaries remain readable. Legacy summaries simply report
  identity comparison as unavailable until a new baseline is recorded.
- Adapter version changes invalidate older stage checkpoints when this contract
  ships; result schema does not change because the identity matrix is evaluation
  output rather than a new discovery-result field.

## Validation

- All fixed provider expectations declare website, career, job-board provider,
  public tenant, board URL, and opening identity.
- Canonicalization tests cover meaningful query/fragment preservation, explicit
  aliases, credentials, encoded controls, private hosts, duplicate companies,
  matrix ordering, and legacy comparison.
- Checkpoint tests cover gaps, corrupt records, unsafe URLs, invalid statuses,
  missing outputs, suffix invalidation, explicit rerun no-mutation behavior, and
  complete seven-stage timeout recovery.
- CLI and live-batch tests prove that both entry points use the shared inspector
  and that failed explicit reruns do not publish normal output artifacts.
- Main runs the complete offline gate and a serialized unfamiliar-company live
  holdout before publishing the iteration baseline.
- Unfamiliar title-only cohorts additionally freeze source opening URLs before
  execution. A same-title URL is not an exact hit when its company or requisition
  identity differs.
