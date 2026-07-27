# v239 Local SearXNG Runtime - Phase A

## Scope

Version `.238` introduced a tested SearchBackend boundary but did not configure
a real backend. This phase supplies an isolated local SearXNG runtime and uses
it for one frozen focused A/B over the 12 search-filtered development records
across 11 companies.

It does not change query planning, candidate filtering, provider adapters,
hiring relationships, tenant validation, S7, coordinator-v2, the browser
extension, or the LLM branch.

## Runtime Contract

The service must:

- bind only to `127.0.0.1`;
- enable SearXNG JSON output explicitly;
- receive its secret through a runtime environment variable;
- reject startup when the secret is absent;
- use a pinned official container image digest;
- bind the image/settings server-profile SHA-256 into the application search
  profile and checkpoint fingerprint;
- store runtime cache outside tracked source files;
- disable container log collection so engine errors cannot retain full search
  query URLs;
- expose a bounded health check;
- remain optional and disabled unless `--search-backend searxng` and
  `--search-backend-url http://127.0.0.1:8888` are supplied.

The application continues to send one request per query through its existing
Fetcher, retry, budget, snapshot, and replay boundaries.

The pinned image is official SearXNG version `2026.7.26-b060c780d`, revision
`b060c780d0751a55e75ad22f0d930c8965789db8`, digest
`sha256:d0aaeb14880e6e92bde1518fcc7261e995783367d63d95203383607bef9c6516`.

## Focused Acceptance

1. Container health check passes on loopback.
2. A JSON query returns a valid `results` list.
3. The SearXNG adapter fixture and privacy tests remain green.
4. Freeze the exact 12-record input before running either route.
5. Run legacy and SearXNG with the same `.239` code, fresh isolated
   checkpoint/snapshot/completion/output directories, one worker, and no
   resume.
6. Replay both captures with zero mismatch, fixture gap, tape divergence, or
   missing snapshot boundary.
7. Compare:
   - raw candidate production;
   - verified Job Lists;
   - S7 Exact openings;
   - wrong URL/location/company/tenant;
   - request count and elapsed time.
8. Do not promote focused terminals into the durable projection without the
   existing acceptance manifest and manual identity review.

The cluster qualifies only if the alternative backend recovers a nonzero batch
of independently verified candidates without a safety regression. Recovering
one or two records is evidence that the original cluster was too broad, not
permission to declare it closed.

## Rollback

Stop the local Compose service and select `legacy`. No checkpoint, result, or
candidate contract migration is needed.
