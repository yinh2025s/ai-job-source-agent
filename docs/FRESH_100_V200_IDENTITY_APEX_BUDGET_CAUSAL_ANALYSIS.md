# Fresh100 `.200` Identity-Apex Budget Causal Analysis

Analysis date: 2026-07-21

## Scope

This is a read-only Phase A analysis of the immutable `.200` cold run and the
`.201` focused run. It does not reinterpret a stage label as a causal cluster.
Blind holdouts v2/v3 were not opened or executed.

## Reclassification

The `.200` non-Exact records contain 25 S2 failures. Their retained terminal
labels split into LinkedIn HTTP 429, LinkedIn/search/homepage timeout, and one
outer worker timeout. Those labels describe the strongest unavailable evidence
source; they do not prove that retrying that source will discover the website.

Two candidate bypass hypotheses were audited:

1. All 100 frozen inputs have a null `external_apply_url`. External Apply is
   therefore unavailable to this cold cohort even though the runtime contract
   supports it.
2. Thirty-seven non-Exact records ran five Bing RSS ATS queries, received 38-50
   raw results, retained zero policy-valid candidates, and did not use another
   source because `query_diversity_first` spends the bounded budget across RSS
   queries. A live diagnostic on eight independent companies tested the same
   broad query against DuckDuckGo and Bing HTML. DuckDuckGo returned a challenge
   for 8/8 and Bing HTML produced zero parsed results for 8/8. Adding source
   fallback would therefore add requests without demonstrated recovery and is
   rejected as the next implementation cluster.

OneApp is the positive scheduler control: its S2 request failed, but an Ashby
tenant probe independently verified a board, so S5/S6 continued and honestly
returned `OPENING_NOT_FOUND`. S2 is already non-blocking when a current provider
lead actually exists.

## Selected Causal Cluster

At least 18 non-Exact records requested an identity-shaped company apex and
received timeout/connection/access failure. The common implementation defect is
narrower than the transport label:

- the candidate is already generated before search;
- its apex host label equals the compact or dashed core company name after legal
  suffix removal;
- it uses `.com`;
- it has no direct evidence source yet, so `_candidate_fetch_policy()` treats it
  like an ordinary speculative guess;
- the speculative retry scope receives at most two seconds, observed as roughly
  2.7-2.9 seconds after wrapper overhead in `.200`.

Matlen Silver (`matlensilver.com`), American Fabrication
(`americanfabrication.com`), and Arkema (`arkema.com`) independently crossed S2
on the same official apex in `.199` or `.188`, but timed out under the short
speculative slice in `.200`. These three companies form the minimum Phase C
acceptance cohort. HP and Sentar are additional controls; their official-host
identity remains subject to ordinary page validation.

## Generic Repair Contract

Reserve one bounded `identity_apex_candidate` fetch policy for an exact core-name
`.com` apex. It may receive a larger single-attempt window inside the unchanged
25-second S2 budget. It must not receive authority, score, identity evidence, a
retry exemption, or automatic selection.

The following remain unchanged:

- homepage content/canonical/organization identity validation;
- ambiguous-company and contradictory-body rejection;
- LinkedIn, search, public-registry and stored-evidence authority;
- regional and parent/brand continuity;
- S3-S7 provider, tenant, title, location and opening validation.

`getbrand.com`, arbitrary prefixes/suffixes, unrelated `.com`, non-`.com`
candidates and all later guesses retain the ordinary short speculative policy.

## Acceptance And Rollback

Phase B:

- focused unit tests prove policy classification and negative controls;
- all resolver/provider/architecture and full offline gates pass;
- no URL is accepted merely because the longer request returns a page.

Phase C:

- cold, isolated focused live for Matlen Silver, American Fabrication and both
  Arkema postings, with HP and Sentar as controls;
- at least three independent companies must move past S2 on verified website
  identity under the unchanged 25-second website budget;
- every produced opening must pass the complete S7 identity/location gate;
- full scoped replay must have zero mismatch, fixture gap and boundary gap.

If fewer than three independent companies recover, the cluster definition is
rejected. The implementation must not be described as transport-cluster closure;
the remaining failures must be reclassified before another change.
