# `.220` LinkedIn Input Canonicalization Phase A

## Frozen Evidence

The code-frozen Fresh100 run at
`/private/tmp/fresh100-v220-cold-20260722-run1` completed 100 live records with
zero restored completions. Three independent records terminated as
`batch_worker_contract_failed`:

| Company | Trigger | Escaped boundary |
| --- | --- | --- |
| Investigative Case Management powered by Tapestrii | LinkedIn job slug contains encoded newline `%0A`; company URL is empty | `website_resolution` |
| University of Oklahoma | optional LinkedIn company URL is empty | `website_resolution` |
| Hays + Sons | LinkedIn job slug contains encoded newline `%0A` | `job_board_discovery` |

All three reach `_coordinator_source_input()`, which passes raw S1 URL strings
to strict `CandidateDiscoveryInput`. That contract correctly rejects encoded
control characters and empty URL strings, but the adapter lets its `ValueError`
escape the stage boundary. Tapestrii and University consequently have no
captured `website_resolution` snapshot boundary, and the 100-record replay plan
fails integrity before executing any replay.

This is one causal cluster: the same trigger, adapter and exception path cause
all three worker failures. It is not a website, company-name or provider defect.

## Contract

The S1-to-coordinator adapter must normalize optional LinkedIn evidence before
constructing the immutable coordinator input:

1. Extract a valid LinkedIn job ID from the input URL using the existing
   bounded ID parser.
2. When an ID exists, rebuild the job evidence URL as the canonical public
   locator `https://www.linkedin.com/jobs/view/{job_id}`. Slug text is not
   identity evidence and must not survive into the coordinator contract.
3. When no valid ID exists, pass neither job URL nor job ID.
4. Convert an empty optional LinkedIn company URL to `None`.
5. A malformed non-empty company URL is omitted rather than converted into a
   candidate; it cannot authorize any relationship or provider route.
6. Keep `CandidateDiscoveryInput` strict. Do not permit encoded controls,
   credentials, non-HTTPS URLs, non-LinkedIn hosts or mismatched job IDs.

This normalization changes only optional route provenance. Company, provider,
tenant, title, location, opening and S7 validation remain unchanged.

## Acceptance

- Unit tests cover `%0A` job slugs, empty company URLs, malformed company URLs,
  job URLs without a valid ID, and mismatched/unsafe URL negatives.
- Tapestrii, University of Oklahoma and Hays complete the coordinator stage
  without an uncaught exception under frozen fixture inputs.
- The three records produce normal typed outcomes; success is not required.
- Every attempted stage publishes its snapshot boundary.
- Scoped replay exports and executes 3/3 with zero fixture gap or tape
  divergence.
- No exact URL, company alias, provider tenant or benchmark ID is hard-coded.

## Rollback

Revert the change if malformed evidence becomes a candidate, if a job ID can be
changed or inferred from non-LinkedIn text, if optional company evidence is
treated as verified, or if any existing strict coordinator contract test is
weakened.
