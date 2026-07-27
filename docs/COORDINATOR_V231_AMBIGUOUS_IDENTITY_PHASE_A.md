# Coordinator `.231` Ambiguous Website Identity Phase A

## Run Disposition

The `.230` 100-record cold run at
`/private/tmp/fresh100-v230-cold-20260723-run2` is diagnostic only and must not
replace the current closure projection.

- Live completed with 29 Exact, 40 partial and 31 failed records.
- The run lasted from 12:02 to 00:18 and contains repeated snapshot gaps of
  roughly one hour. Per-record wall-clock values reached 12,416 seconds while
  the configured company budget remained bounded.
- Twenty-two terminal `NETWORK_TIMEOUT` results and adjacent DNS/fetch failures
  overlap the suspended intervals. They cannot be used as product-recall
  evidence.
- Full scoped replay stopped on Focus with one unconsumed S6 request:
  `GET https://api.ashbyhq.com/posting-api/job-board/focus`.

The artifacts remain frozen for diagnosis. They are not overwritten, promoted
to the official baseline or mixed into the `.230` focused projection.

## Causal Cluster

Focus exposes an upstream identity false positive, not merely a replay
bookkeeping error.

The target input is `Focus - HR Manager - Anniston, AL`. S2 received no
LinkedIn official-website evidence. It selected `https://focus.org/` from
search/speculative candidates because:

- the short company token matched the domain;
- the page canonical URL matched that same domain;
- JSON-LD organization data repeated the short token; and
- the LinkedIn slug was also the generic word `focus`.

None of those observations distinguishes the target employer from another
organization with the same short name. The selected site belongs to a
different organization and its Career path does not establish the target
LinkedIn identity.

S5 later probed `https://jobs.ashbyhq.com/focus`. That inventory describes
Focus Digital, yet the mutable evidence store retained it beneath the
`focus.org` identity. S6 found no HR Manager, so no wrong opening URL was
published, but the intermediate website/provider chain was unsafe.

Replay then correctly refused to reconstruct that same-record mutable provider
write as authoritative pre-existing evidence. It reran S5 without the polluted
stored candidate, did not reach the Ashby S6 request, and exposed the live tape
entry as unconsumed. Making replay consume the request would reproduce an
unsafe identity chain rather than fix the producer.

## Shared Trigger

The executable trigger is:

1. the normalized company name is a short ambiguous single token;
2. no LinkedIn official-website evidence is present;
3. the candidate comes from search, slug expansion or speculative guessing;
4. homepage identity consists only of self-referential domain/canonical and
   organization-name evidence; and
5. no bounded title or body evidence distinguishes the employer.

This is a resolver contract, independent of Focus, Ashby or a benchmark job
ID. It applies to short-name collisions across all downstream providers.

The same gate must continue to accept stronger independent evidence:

- LinkedIn explicitly identifies the official website;
- bounded homepage title or body text establishes the short company identity;
- a trusted direct input carries verified provenance; or
- an existing specialized public/institutional identity contract applies.

## Change Boundary

Phase B changes only:

- `job_source_agent/website_resolver.py`
- `tests/test_website_resolver.py`
- adapter version metadata
- Phase C and governance summaries

It does not change provider adapters, title/location matching, S7 thresholds,
External Apply, the extension, coordinator-v2, LLM code or sealed cohorts.

The resolver selection gate will reject an ambiguous short-name candidate when
LinkedIn did not identify it and its only positive page evidence is canonical
or organization metadata repeating the same token. Bounded title/body identity
or an existing authoritative source remains sufficient.

## Acceptance

1. Three synthetic short-name collisions across `.com`, `.org` and provider-like
   domains fail closed when only canonical/organization self-evidence exists.
2. LinkedIn official-site evidence still selects the same candidate.
3. A bounded title/body identity signal still selects a legitimate short-name
   company without LinkedIn availability.
4. Existing ambiguous-name, product-extension, corporate-group and search
   collision tests remain green.
5. A Focus-shaped focused run does not publish `focus.org`, the Ashby Focus
   Digital board or any opening as the target employer.
6. The new focused capture replays with zero mismatch, fixture gap or
   unconsumed tape entry.
7. Wrong opening URL, company, tenant and location remain zero.

## Rollback

Remove the additional ambiguous-candidate selection predicate and restore
`.230`. The change writes no persistent schema and requires no cache migration;
the adapter version bump invalidates prior checkpoints conservatively.
