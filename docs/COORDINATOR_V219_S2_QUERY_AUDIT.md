# `.219` S2 Query And Transport Audit

## Scope

This is a development-cohort diagnostic, not a Fresh100 score. It inspected the
preserved `.188` and `.209` reports, then ran isolated S2 probes for FOTOMILL
STUDIOS LIMITED, Dechert LLP, Investigative Case Management powered by
Tapestrii, IMG (International Medical Group), and iClassPro. No extension, LLM
branch or sealed cohort was used.

Probe snapshots are preserved under:

- `/private/tmp/coordinator-v220-s2-query-probe/snapshots`
- `/private/tmp/coordinator-v220-s2-resolver-probe/snapshots`

## Findings

The old stage label was not one causal cluster. `.188` contained 52 raw S2
failures; `.209` contained 22. Existing captures show both irrelevant SERPs and
correct candidates that later failed homepage transport.

An experimental plan tried at most two exact-brand variants while retaining all
current identity gates. The SERP probe found:

- FOTOMILL: correct domain plus two directory candidates;
- IMG: correct domain;
- iClassPro: correct domain plus two secondary candidates;
- Dechert: directory sites only;
- Tapestrii: no accepted candidate.

Full resolver verification published only FOTOMILL. IMG and iClassPro failed
homepage transport and returned no website. The experiment therefore recovered
1/3 positive resolver records, below the required three-company acceptance
threshold. No wrong company was published.

## Decision

The alternate-query behavior is reverted. `.219` retains only privacy-safe S2
query diagnostics: source, status, raw result count, accepted result count and
typed fetch reason. URLs remain in the existing candidate/fetch evidence fields,
not duplicated into the diagnostic summary.

The next causal split is:

1. correct candidate produced, homepage verification transport failed;
2. correct candidate not produced by any configured source;
3. directory/aggregator candidates produced but rejected by homepage identity.

Replacing the search backend or adding a paid API is a separate product choice;
it cannot be claimed as solved by query formatting alone.

## Offline Gate

The frozen `.219` backend passes 2,625 tests with four intentional skips,
provider benchmark 25/25, resolver benchmark 6/6 and architecture validation
with 46 native adapters and zero issues. The full suite found two stale replay
test builders that left schema `1.6`/`1.7` fields in payloads labelled `1.0`;
the fixtures were corrected without weakening production schema validation.
