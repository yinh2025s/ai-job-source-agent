# ADR-0016: Prioritize Direct Website Evidence Before Speculation

- Status: accepted
- Date: 2026-07-14

## Context

S2 previously mixed a supplied company website, LinkedIn official-website
evidence, search results, slug-derived domains, and speculative guesses into one
bounded concurrent verification batch. Source provenance was preserved in trace,
but it did not control execution. A slow guess could therefore consume the stage
budget after the supplied site had already responded, and a guessed `.com` with
self-canonical evidence could outrank a verified short-brand preferred domain.

The unfamiliar holdout also produced the exact constrained institutional domain
`snhu.edu`, but the host returned HTTP 403. Treating every fetch failure alike
discarded the distinction between a reachable access-controlled host and DNS,
timeout, connection, or not-found failures. Accepting arbitrary blocked domains,
however, would weaken company identity and create false positives.

## Decision

1. Website verification runs direct evidence before speculative evidence.
   `preferred_input`, LinkedIn official website, and its valid cache record form
   the direct wave. They still pass the existing public-URL, fetch, redirect,
   regional, parked-domain, and positive company-identity checks.
2. If exactly one direct candidate is selectable, S2 does not dispatch the
   remaining speculative verification wave. If no direct candidate is selectable
   or multiple direct domains conflict, the existing bounded allocation and
   concurrent fallback continue.
3. Provenance does not make a URL trusted by itself. A stale, redirected,
   parked, identity-negative, malformed, private, or conflicting preferred URL
   remains rejected.
4. For an ambiguous one-token company name, a homepage title may establish
   identity when it starts with the complete token followed by a finite legal
   entity suffix such as `Inc`, `LLC`, `Ltd`, `GmbH`, or `AG`. Arbitrary second
   words do not qualify.
5. An access-controlled institutional acronym is selectable without homepage
   content only when all of these conditions hold:
   - the company has three to eight alphabetic tokens and ends in `college`,
     `institute`, or `university`;
   - the acronym contains at least four characters;
   - the host is exactly `<all-token-initialism>.edu`;
   - the exact host returns HTTP 401 or 403; and
   - no stronger LinkedIn or positive homepage identity is available.
6. DNS failure, timeout, connection failure, HTTP 404, other TLDs, subdomain
   variants, and short acronyms never satisfy the access-controlled fallback.
   Trace records the bounded reason code and status, not response content.
7. This decision does not establish a career page, job board, provider, or
   opening. Every downstream stage keeps its independent evidence gate.

## Consequences

- Direct verified evidence can finish S2 without waiting for unrelated guesses,
  reducing budget loss and same-name domain substitution.
- The resolver performs less speculative network traffic when the input already
  identifies one valid company website.
- A narrow class of access-controlled educational domains can be identified
  without treating generic blocked websites as official.
- A direct-evidence conflict may cost the same bounded fallback work as before;
  correctness takes priority over the fast path.
- Adapter-version invalidation is sufficient because result and checkpoint
  schemas do not change.

## Validation

- Resolver tests cover direct-wave early completion, short-brand preferred-domain
  selection, legal-entity title evidence, and retained concurrent fallback.
- Institutional tests cover exact `.edu` 401/403 acceptance and rejection of
  404, other TLDs, non-institutions, short acronyms, and stronger conflicting
  identity.
- Focused live validation covers Atira, RIVR, Southern New Hampshire University,
  and Bosch Group with isolated checkpoints and snapshots.
- The main release gate runs the full test suite, production provider benchmark,
  resolver benchmark, and architecture validator before publication.
