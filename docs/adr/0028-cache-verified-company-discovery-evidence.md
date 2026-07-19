# ADR-0028: Cache Verified Company Discovery Evidence

- Status: accepted for implementation behind an explicit store
- Date: 2026-07-19

## Context

The frozen `.125` cohort ended with 42 LinkedIn company-page failures across 34
companies: 37 HTTP 451 responses and five HTTP 999 responses. Seventeen records
had already reached a verified website in `.120`, including ten verified Job
Lists and three Exact openings, but a new adapter version and isolated checkpoint
root correctly prevented old execution checkpoints from being restored.

ADR-0003 cannot solve this loss. Its store accepts only the official website
field extracted from a matching public LinkedIn `Organization` record. It must
not be widened to accept a resolver result, Career page, provider board or search
lead. A separate evidence boundary is required for public facts that the pipeline
itself previously verified and can verify again with current code.

## Decision

### Identity And Stored Value

1. The store key is the SHA-256 digest of the canonical JSON pair containing the
   case-folded, whitespace-normalized source company name and normalized LinkedIn
   company URL. Both values are mandatory. Company name, domain, provider tenant
   or LinkedIn slug alone is insufficient identity.
2. One record may contain these independently optional public evidence layers:
   - a verified company website root;
   - a verified Career page and its website-root relationship;
   - one or more typed provider boards with provider, tenant/identifier,
     canonical board URL, relationship-evidence URL and verification method.
3. Every layer stores its own `observed_at` and bounded provenance. Provenance is
   an allowlisted enum plus public evidence URLs, never arbitrary trace text.
4. Exact opening URLs, title/location matches, inventory responses and posting
   status are never stored. S6 and S7 always read current public inventory and
   revalidate the individual posting.

### Trust And Revalidation

1. Store values are discovery candidates, not authoritative output, checkpoints
   or success results. A read can bypass a blocked LinkedIn evidence fetch, but it
   cannot bypass current verification.
2. A website candidate must pass the current resolver's fetch, redirect,
   ownership, parking, region and company-identity gates before publication.
3. A Career candidate must be fetched and remain an identity-continuous Career or
   listing page related to the currently verified website or hiring entity.
4. A provider board must be recognized and canonicalized by the current provider
   registry. Its provider, tenant, canonical board and hiring relationship must
   all agree with the stored typed identity before it can enter S5. Search snippets
   and tenant-name similarity cannot refresh relationship evidence.
5. Current verification can refresh a layer. A redirect to another company,
   parked/migrated website, provider/tenant mismatch, malformed value or explicit
   not-found invalidates only the affected layer and its downstream descendants.
   Retryable network failures retain the unexpired candidate but publish a typed
   retryable terminal, never the stale URL.

### Lifetime And Compatibility

1. The default TTL is 30 days per layer. The exact TTL boundary is valid;
   expired, future-dated, malformed, non-finite or missing timestamps are misses.
2. The file uses an independent integer schema version. Missing or mismatched
   schema invalidates the whole file safely.
3. Adapter-version changes do not erase public facts from this store. Instead,
   current-version revalidation and canonicalization are mandatory. Execution
   checkpoints remain adapter-version-bound and are not replaced by this store.
4. Negative results are not durable. HTTP 451/999, timeout, rate limit, empty
   search and missing Career discovery are never cached as company facts.

### Persistence, Privacy And Isolation

1. Reads and read-modify-write updates use a process lock. Writes use a
   same-directory temporary file, file `fsync`, atomic replace and directory
   `fsync`. Missing/corrupt/incompatible files and malformed records are safe
   misses; a later valid write recovers from a clean current-schema payload.
2. Stored URLs must be public HTTP(S), use standard ports, contain no credentials,
   fragments, private hosts or sensitive query parameters, and be canonicalized
   before persistence.
3. The store contains no HTML, response bodies, cookies, headers, tokens, browser
   state, personal/job-seeker data, job descriptions or authenticated LinkedIn
   payloads.
4. Product and extension runs may use one stable explicitly configured store.
   Parallel worktrees and simultaneous benchmarks use isolated store paths. A
   frozen benchmark that seeds evidence from a prior verified run must record the
   source run, evidence count and seeded status in its report.

### Batch Coalescing

1. Within one batch, postings with the same normalized company + LinkedIn identity
   may share a single in-flight S2-S5 producer and immutable verified company
   evidence. Posting-level source data, S6 matching and S7 output remain separate.
2. Producer success may fan out to consumers. A retryable producer failure may be
   coalesced only for the current bounded run and is never persisted as negative
   evidence.
3. Coalescing must not cross hiring entities, LinkedIn company identities,
   provider tenants, checkpoint roots or store namespaces.

## Consequences

This store can recover the 17 `.125` regressions that had verified `.120`
company evidence without weakening ADR-0003 or restoring old execution state.
It cannot manufacture evidence for the 25 records that failed S2 in both runs;
those require authenticated extension input, a verifiable External Apply/provider
lead or another current first-party source.

The design spends bounded revalidation requests, but avoids repeating LinkedIn
public-page requests and Career/provider discovery for duplicate postings. Wrong
or stale evidence fails closed. Success is measured by current verified output,
not store hit count.

## Validation

- Contract tests cover key isolation, layer TTLs, schema invalidation, corrupt
  recovery, atomic writes, concurrent writers, URL privacy and malformed records.
- Resolver tests cover 451/999 plus stored website revalidation, parking,
  cross-brand redirect, region conflict and retryable revalidation failure.
- S4/S5 tests cover current Career fetch, native adapter canonicalization, wrong
  tenant/company rejection and typed relationship continuity.
- Batch tests cover one producer for duplicate postings, independent S6/S7 output,
  no durable negative cache and isolated store roots.
- Integration acceptance uses a disclosed `.120` verified-evidence seed for the
  17 regression records, then runs fresh live revalidation and same-version replay.
  A later clean unfamiliar cohort remains the product generalization gate.
