# `.226` Official Candidate Failure Attribution Phase A

## Frozen Evidence

Three independent companies share one resolver attribution defect:

| Company | Verified LinkedIn official candidate | Incorrect published failure |
| --- | --- | --- |
| North Dakota Information Technology | `https://www.ndit.nd.gov` | `kxnet.com` HTTP 403 |
| City of Lubbock | `https://www.mylubbock.us/` | `cityoflubbock.com` timeout |
| State of Montana | `https://www.mt.gov` | `state-of-montana.com` TLS timeout |

For each record, the official candidate completed homepage verification and
contains positive company evidence. The candidate is intentionally withheld by
`parent/group website requires downstream hiring relationship evidence`.
After selection returns no Website, `_strongest_retained_fetch_failure()`
chooses a higher-scored unrelated candidate's transport error and projects it
through S2 as the company's terminal failure.

## Root Cause

The retained-failure selector uses evidence tier and error priority but does
not bind a failure to the strongest verified official identity evidence. A
speculative or alternate host can therefore overwrite the truthful state:

```text
verified official candidate exists
-> exact company identity remains insufficient
-> unrelated host fails transport
-> unrelated failure becomes company terminal
```

This is failure provenance loss. It is not a reason to accept a parent/group
Website or relax company identity.

## Frozen Contract

- Detect a withheld official candidate only when all are present:
  - LinkedIn company page identifies the official Website;
  - the homepage was successfully verified;
  - the candidate is rejected specifically because downstream hiring
    relationship evidence is still required.
- When such evidence exists, a retained Website-resolution transport failure
  must belong to the same registrable site as a withheld official candidate.
- Failures from other hosts remain in `fetch_errors` for diagnosis but cannot
  populate `resolution_failure` or determine the company terminal.
- If no relevant failure remains, S2 returns the existing honest
  `WEBSITE_NOT_RESOLVED` terminal. It must not publish the withheld URL.
- When no withheld verified official candidate exists, current direct-evidence
  retry/403 precedence is unchanged.
- A failure on the same official registrable site remains eligible for normal
  retention.

## Safety Boundaries

- Parent/group candidates remain rejected by `_select_verified_candidate()`.
- This change creates no `company_website_url`, hiring relationship, provider
  relationship, tenant, Job Board or opening evidence.
- LinkedIn/search snippets, domain similarity and successful fetch alone do not
  trigger suppression.
- No company name, domain, job ID or government-specific branch is allowed.
- Existing direct preferred-site timeout/403 tests must continue to retain the
  typed transport failure.

## Acceptance

- NDIT no longer attributes `kxnet.com` 403 to the company.
- City of Lubbock no longer attributes `cityoflubbock.com` timeout to the
  verified `mylubbock.us` evidence.
- State of Montana no longer attributes `state-of-montana.com` timeout to the
  verified `mt.gov` evidence.
- All three remain without a published Website until a later relationship
  contract proves one.
- Same-site official transport failures remain typed and retained.
- Existing parent/group rejection, direct-evidence retry, forbidden evidence,
  resolver benchmark and replay gates remain green.

## Ownership

- Resolver implementation: `job_source_agent/website_resolver.py`.
- Contract tests: `tests/test_website_resolver.py`.
- Main line: version, focused live/replay, URL audit, closure matrix, changelog
  and Phase C.

## Rollback

Revert if a parent/group URL is published, if direct official transport errors
lose their typed terminal, if unrelated failure suppression triggers without
all three official-candidate conditions, or if any wrong-company Website or
opening is accepted.
