# ADR-0003: Cache Verified LinkedIn Website Evidence

- Status: accepted
- Date: 2026-07-13

## Context

Public LinkedIn company pages sometimes expose the company's official website in
an `Organization` JSON-LD record, but repeated requests can return a throttling
shell or an incomplete page. Losing previously verified public evidence can make
S2 select a stale historical domain even though the same run previously observed
the current official website.

This cache affects identity resolution, persists across runs, and may be written
concurrently. Its compatibility, privacy, and recovery behavior therefore must be
fixed before it is used by parallel workstreams.

## Decision

### Identity And Value

1. The cache key is the SHA-256 digest of a canonical JSON pair containing the
   case-folded, whitespace-normalized company name and normalized LinkedIn company
   URL. Both fields are required; company name alone or LinkedIn slug alone is not
   sufficient identity.
2. URL normalization canonicalizes company keys to HTTPS `www.linkedin.com`,
   removes fragments, known tracking and secret-bearing query parameters, and a
   trailing LinkedIn path slash. The cache stores only normalized official website
   URLs, the normalized identity pair, and the observation timestamp.
3. Evidence is accepted only from a public LinkedIn company page's
   `Organization` JSON-LD after strict company-name matching. Search guesses,
   arbitrary outbound links, non-HTTP(S) URLs, URLs containing credentials or
   non-standard ports, user cookies, and browser session data are never written to
   this store.

### Lifetime And Compatibility

1. The default TTL is 30 days. A record at the exact TTL boundary is valid;
   expired, future-dated, malformed, `NaN`, or infinite timestamps are cache
   misses.
2. The file has an independent integer schema version. A missing or mismatched
   version invalidates the whole file. Resolver behavior changes still bump the
   pipeline adapter version so stage checkpoints cannot silently retain old S2
   decisions.
3. Live evidence is attempted first. Cache evidence is used only when the current
   public company page provides no matching official website.

### Corruption And Concurrency

1. Missing files, invalid JSON, invalid roots, incompatible schemas, malformed
   record collections, malformed records, and invalid URL collections are safe
   cache misses.
2. The next successful save after file-level corruption starts from a clean
   current-schema payload; incompatible records are not carried forward.
3. Reads and read-modify-write saves use a process lock. Writes use a same-directory
   temporary file, file `fsync`, atomic replace, and directory `fsync`. A failed
   replace preserves the previous complete file and removes the temporary file.

### Trust And Privacy

1. Cached data is evidence, not a company override and not a success result. It is
   tagged as cached in trace and still enters the existing redirect, parking,
   region, and brand-identity checks before selection.
2. The file contains public company-level URLs only. It must not contain job-seeker
   identity, personal profile content, job-page HTML, cookies, tokens, request
   headers, browser storage, or authenticated LinkedIn payloads.
3. No company-specific cache records are committed to the repository. A benchmark
   may seed an isolated temporary cache only from a recorded authoritative trace,
   and its report must identify the run as seeded replay evidence.

### Paths And Parallel Ownership

1. The CLI accepts an explicit cache path. When omitted, a checkpointed run uses
   `<checkpoint_dir>/linkedin-website-evidence.json`. Extension runs use one stable
   file under their output directory so successive runs can reuse evidence.
2. Every parallel worktree owns a separate checkpoint root and temporary directory.
   Cache files are never shared by concurrent benchmarks.
3. Resolver/store implementation is owned by the main S2 workstream; composition,
   CLI, and extension wiring form a separate write set; cache contract tests form
   a separate write set; failure analysis is read-only. Only the main workstream
   runs the complete offline gates and live benchmark after integration.
4. The failure-analysis workstream classifies Eightpoint, Aventis, and M|R Walls
   from evidence. It must not add company-specific rules: a company can legitimately
   have no public openings or can represent an undisclosed client.

## Consequences

Positive:

- A transient LinkedIn throttle no longer erases recently verified official-domain
  evidence.
- Cache behavior is deterministic, bounded, privacy-minimal, and independently
  testable.
- Parallel work can proceed without sharing mutable evidence or benchmark state.

Costs and limits:

- Recently migrated domains can remain candidates for up to 30 days, although the
  normal resolver verification still applies.
- Cache recovery improves evidence availability but does not create a career page,
  public job list, or exact opening.
- Automated extension smoke cannot validate LinkedIn's authenticated DOM or local
  extension installation.

## Validation

- Contract tests cover normalized round trips, exact TTL boundaries, future and
  non-finite timestamps, schema invalidation, corrupt-file recovery, malformed
  records, concurrent writers, atomic-replace failure, and temporary-file cleanup.
- Resolver tests cover live evidence persistence, throttled-page cache fallback,
  and trace provenance.
- Composition tests cover explicit, checkpoint-derived, and extension output paths.
- After merge, the main workstream runs all offline gates and the same frozen
  30-company cohort. A separate manual acceptance installs the extension in a real
  logged-in Chrome session and executes a LinkedIn scan.
- Release decisions prioritize exact/job-list success on unfamiliar frozen samples,
  not cache hit count or number of parallel tasks.
