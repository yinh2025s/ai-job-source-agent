# Fresh 100 `.195` Provider Employer Phase C

## Scope

This gate tests one executable causal cluster from the immutable 44-record
ledger: record 033, Slant CRM / Product Designer / Lehi, UT. It does not rerun or
rescore the `.188` fresh 100, and it does not claim closure of S2, all candidate
generation failures, or records without equivalent provider-owned evidence.

The live source was frozen at base commit
`a17cdcc91c828eed43f7d78ddfedd6fe3aedcf1a` plus dirty source diff SHA-256
`d209b01f79361b234a92043f32ee1f1dcade4071e737b73aa11c0a9a422c667e`.
No implementation file changed during live or replay.

## Contract Under Test

The website-unresolved provider route may establish a hiring relationship only
when one provider-owned public opening/API record binds:

- explicit employer name and descriptor terms;
- canonical provider and tenant;
- canonical board and opening URL;
- exact normalized target title;
- strict city/state location;
- current open status.

Search snippets, tenant-prefix similarity, a full inventory by itself, wrong
city, cross-opening evidence, and missing employer evidence remain insufficient.
S6 and S7 still validate title, location, status, company, provider, tenant, and
opening after candidate discovery.

## Offline Gates

| Gate | Result |
| --- | --- |
| Relevant tests | 88 passed |
| Full test suite | 2505 passed, 4 skipped |
| Provider benchmark | 25/25 |
| Resolver benchmark | 6/6 |
| Architecture gate | 46 adapters, 0 issues |
| Compile / diff check | Passed |

## Isolated Live Run

Root: `/private/tmp/fresh100-v195-provider-employer-20260720-run1`

The root began with independent checkpoint, completion, company-evidence,
snapshot, replay, and output stores. Snapshot store ID was
`171a4f74c3204d6f8b74605ad42750dc`; producer attempt ID was
`6522dddb28b146fcad9175a15f612be5`. Trace checkpoint restores are same-run
handoff from the S1-S3 producer process to the S4-S7 consumer process. They are
not cross-run resume and do not read `.188`, `.189`, or `.194` state.

| Metric | Result |
| --- | --- |
| Records | 1 |
| Pipeline success | 1 |
| Website | 0 |
| Career page | 0 |
| Verified Job Board | 1 |
| Exact Opening | 1 |
| Elapsed | 22.1 seconds |
| Wrong / cross-company / cross-tenant URL | 0 / 0 / 0 |

The S2 `FETCH_FAILED` stage diagnostic remains in trace because website
resolution failed. It is not the terminal outcome: the non-blocking S5 provider
route completed the verified identity chain and the pipeline terminal outcome is
`exact_opening`.

## Exact Audit

| Field | Verified value |
| --- | --- |
| Source company | Slant CRM |
| Hiring entity | Slant CRM |
| Relationship method | `provider_published_employer` |
| Evidence URL | `https://api.ashbyhq.com/posting-api/job-board/slant` |
| Provider / tenant | `ashby` / `slant` |
| Board | `https://jobs.ashbyhq.com/slant` |
| Target / selected title | Product Designer / Product Designer |
| Target / selected location | Lehi, UT / Lehi, Utah |
| Location classification | `exact` |
| Opening | `https://jobs.ashbyhq.com/slant/1d5a754e-593d-445e-a605-89e674eb077f` |
| Inventory | Full, complete, 6 records |
| S7 verdict | `verified`, no failure codes |

The candidate was produced only after the ordinary `slant-crm` provider probes
failed. The bounded acronym-suffix route probed `slant`; Ashby then published
the matching opening and an opening-scoped `About Slant` section whose explicit
CRM descriptor completed the employer binding. Both HiringIdentity and
ProviderIdentity cite the Ashby API, not the denied LinkedIn company URL.

## Replay

The same-version bundle replayed the complete selected record:

- 1/1 reproduced;
- 0 expected transition;
- 0 budget recovery;
- 0 fixture gap;
- 0 mismatch;
- record integrity passed with one source result, one selected/exported record,
  one trace, and one comparison.

Live and replay normalized identity chains are equal for hiring, provider,
tenant, board, opening, and both evidence URLs.

## Decision

Record 033 and the one-record `G-unstable search candidate` contract are closed.
The original 44-row ledger remains unchanged as the pre-fix audit; its closure
delta is now 43 open records: `T=10`, `B=0`, `S=1`, `G=28`, and `I=4`.
Versana 005/029 and all other records remain open until their own causal
contracts meet the documented batch acceptance floors.
