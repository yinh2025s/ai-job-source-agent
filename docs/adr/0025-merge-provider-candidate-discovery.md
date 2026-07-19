# ADR-0025: Merge Provider Candidate Discovery Before Verification

- Status: accepted; enabled by default at product entry points
- Date: 2026-07-15

## Context

The original seven-stage pipeline treated a verified official website and career page as the
main route into S5. LinkedIn External Apply could bypass S4, and S4/S5 contained bounded ATS
search fallbacks, but these routes did not share one explicit untrusted-candidate contract.
Consequently a missing S2/S4 handoff could suppress useful provider evidence, while search
ranking and provider identity were difficult to audit as separate decisions.

## Decision

1. Preserve S1-S7 and add three S5 lead sources: LinkedIn External Apply, explicit ATS URLs
   from website/career evidence, and exhaustive bounded provider-targeted search.
2. Every source emits an immutable `ProviderCandidate`. A candidate is an untrusted public
   HTTPS lead with source provenance; priority and search rank never assert provider, tenant,
   hiring relationship, opening existence, or success.
3. `ProviderCandidatePool` canonicalizes, deduplicates, deterministically ranks, and limits the
   merged set to 12. External Apply ranks above explicit first-party ATS evidence, targeted
   opening/board search, and guessed paths.
4. The provider registry must identify a listing-capable adapter and canonical tenant board
   before a lead enters `JobBoardPortfolio`. Search snippets are never evidence. Search-origin
   provenance remains in trace; the typed board evidence URL stays on the provider origin.
5. S2/S4 remain useful evidence paths but are not mandatory S5 blockers when the feature is
   enabled. If the merged pool yields no listable board, S5 falls back to the legacy path.
6. A missing hiring relationship can be synthesized only from a LinkedIn-declared External
   Apply handoff or an exact normalized provider-tenant/company match. Substring/token overlap
   and title similarity cannot authorize a relationship. Authorized candidates are attempted
   before unverified candidates without removing the latter from diagnostics.
7. S6 publishes typed `OpeningSelectionEvidence` with title, location, inventory scope,
   completeness, and candidate count. Portfolio attempts replace the active provider identity
   with the board that actually produced the opening.
8. S7 verifies hiring entity, provider, tenant, board, opening URL, selected title, and explicit
   location classification. New candidate paths reject an explicit location mismatch; missing
   location remains visible rather than being silently converted into a match.
9. `enable_parallel_candidate_discovery` is part of deterministic run configuration schema 1.3.
   CLI, live evaluator, and extension bridge defaults are true; CLI/live expose a disable switch
   for rollback and controlled comparison. The lower-level library default and legacy 1.0-1.2
   payloads remain false so embedded callers and historical checkpoint fingerprints are not
   silently changed.
10. Schema 1.4 adds benchmark-only `evaluate_all_candidate_routes`. It disables the normal
    direct-route search short circuit so External Apply, provider-targeted search, and
    Website/Career discovery are measured on the same posting. Per-route exact attribution
    still requires a verified provider/tenant board, hiring relationship, and the final typed
    S7 exact identity. Product scheduling and success semantics are unchanged when the flag is
    false.

## Security And Privacy

- Candidate and evidence URLs must be public HTTPS, contain no credentials, fragments, private
  hosts, control characters, or sensitive query keys.
- Candidate pools, traces, and checkpoints contain no HTML, response bodies, cookies, tokens,
  browser state, or arbitrary provider payloads.
- Direct detail URLs are canonicalized to provider tenant boards by the adapter. The detail URL
  can become an exact result only after official inventory/title validation in S6.
- Search-source failures are isolated and typed; they cannot elevate a partial board or fabricate
  an opening.

## Consequences

The website is no longer the only possible entrance to S5, but correctness still depends on
adapter and identity verification. The new search mode spends more bounded requests and marks
its portfolio incomplete when the search plan cannot establish a complete eligible set. Release
requires a frozen live cohort comparison before enabling the flag by default.

Offline acceptance at implementation time: 1463 tests, 25/25 production provider benchmark,
6/6 resolver benchmark, and 26 native adapters with zero architecture issues.

The first serialized observed 30-company flag comparison did not satisfy default-enablement
criteria. Flag off produced 29 websites, 19 career pages, 12 job lists, and 3 exact openings in
198.3 seconds; flag on produced 30/20/13/3 in 215.5 seconds. All three exact canonical URLs were
identical and identity-verified in both runs. The only funnel delta was not attributable to the
candidate path because that company's merged pool was empty. Provider-targeted discovery made
162 source dispatches with 28 errors and emitted zero search candidates; one explicit career ATS
URL entered the pool and preserved an existing exact result, so the flag initially remained off.
Subsequent generic work added verified provider-tenant probes, staged direct-then-search execution,
candidate-scoped hiring evidence, provider-owned canonical board normalization, and strict S7
selection validation. A focused default-command live acceptance recovered Texas Children's through
Oracle HCM and SpaceX through Greenhouse at 2/2 exact with 2/2 replay; the production provider
benchmark remained 25/25 for both output and identity expectations. Product entry points are now
enabled by default, while the lower-level library and old replay payloads remain conservative. This
graduation does not weaken tenant/identity verification or permit company-specific runtime rules.

A later observed 100-posting exhaustive evaluation covered 73 companies and ten job families.
Provider-targeted search attributed 11 exact openings and Website/Career attributed 24; seven
were shared, producing a 28/100 OR-union. No public LinkedIn detail page exposed a usable External
Apply input, so that route's conditional rate is N/A rather than zero. All route traces were
well-formed. Scoped replay reproduced 98/100 outcomes and retained two mismatches as an explicit
residual risk. See `docs/LIVE_100_THREE_ROUTE_REPORT.md`.
