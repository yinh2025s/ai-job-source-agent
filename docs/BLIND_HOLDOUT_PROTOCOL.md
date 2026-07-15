# Blind Holdout Protocol

This protocol defines how the project may claim performance on an unseen cohort. It is a
measurement contract, not a discovery feature. It must not be relaxed to improve a score.

## Lifecycle

```text
S1-only candidate pool
  -> historical unseen audit
  -> frozen cohort + code/config digests
  -> one live execution
  -> cohort permanently observed
  -> independent Codex and human reviews
  -> signed metrics report
```

The candidate pool may contain only source company identity, LinkedIn job identity, title,
location, and bounded source trace. Website, career, board, opening, and external-apply URLs
are discovery answers and are removed or rejected before selection.

The holdout is frozen only from a clean tracked Git tree. The manifest binds the complete
cohort, identity rows, candidate bytes, run-configuration bytes, Git commit/tree, scanned
historical files, and complete Git patch history. A missing history root is an audit failure.

## One-Shot Execution

`scripts/run_blind_holdout_once.py` is the only allowed full runner. It verifies the frozen
digests and code identity, preflights output paths and configuration, then atomically creates
an exclusive ledger before starting `live_batch_eval.py`. The live batch is serial and cannot
resume. Once the ledger is consumed, failure and interruption still make the cohort observed.

Results, trace, summary, and the execution manifest are immutable review inputs. The review
contract recomputes all artifact digests and rejects record-count, identity, URL, provider,
status, stage, or provenance drift.

## Independent Review

Codex records artifact-review suggestions in one manifest. A human independently labels the
separate human manifest and is the only authority for reportable metrics. The human manifest
must be signed with a reviewer-controlled SSH key using namespace
`ai-job-source-human-review`; the merge verifies the detached signature against an explicit
allowed-signers file. A reviewer name or self-attestation is insufficient.

Every `exact_public` label requires manual evidence for:

- the official public opening and its current accessibility;
- the canonical official job board and provider tenant;
- the company, hiring entity, or explicitly verified brand/parent relationship;
- title equivalence and location compatibility.

The evidence URL for the opening and board must match the URLs being scored after canonical
identity comparison. Metrics are generated from the signed human labels only.

## Reporting

Every report publishes raw exact rate, human-verified exact precision, conditional exact
recall, system defect rate, and all six dispositions. Provider fixtures, replay success,
observed cohorts, and prefilled benchmarks remain regression evidence and cannot be presented
as blind product performance.

After the report, the cohort may be reused only as an observed regression cohort. Any product
change requires a new unseen cohort for the next blind claim.
