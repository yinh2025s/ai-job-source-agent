# Coordinator `.283` GovernmentJobs XHR Phase C

Date: 2026-07-29

Decision: **accepted; 3/3 evidence-backed terminal recoveries**

## Contract

The qualified cluster is one provider family and one production path:

1. verify `/careers/{tenant}` and its unique agency identity;
2. call `/careers/home/index?agency={tenant}&keyword={title}` as the declared
   same-host XHR;
3. require complete count and same-tenant detail identities;
4. bind table-local location to each job ID;
5. compare provider-published employer with the resolved hiring entity.

No company, domain or job-ID branch was added. Exact, no-match and identity
rejection are all valid recoveries when supported by current official evidence.

## Focused Runs

Run1 is retained under
`/private/tmp/fresh100-v283-governmentjobs-xhr-focused-20260729-run1`.
It proved the XHR and employer contract but exposed missing table-location
binding: College Station's correct candidate was rejected because location was
null. It is a failed diagnostic and is not the accepted result.

Frozen-code run2 is under
`/private/tmp/fresh100-v283-governmentjobs-xhr-focused-20260729-run2`.

| Record | Terminal | Evidence |
| --- | --- | --- |
| City of College Station | Exact | `cstx` opening `5372109`, exact title and `College Station, TX` |
| City of Lubbock | Verified no-match | complete title-filtered `lubbock` inventory, 0 target candidates |
| WICHITA COMPANY LIMITED | Identity rejected | tenant shell publishes `City of Wichita Human Resources` |

The Exact URL is:

`https://www.governmentjobs.com/careers/cstx/jobs/5372109/hr-operations-and-services-manager`

It passed S7 with company, title, location, provider, tenant, board and opening
continuity. No URL was published for the other two records.

## Replay And Safety

Strict same-version replay passed:

- 3 reproduced;
- 0 mismatch;
- 0 fixture gap;
- 0 budget recovery.

URL review found zero unsafe, wrong-location, cross-company or cross-tenant
publication.

## Offline Gate

The full discovery executed 2,862 tests with four skips. The only initial error
was the managed sandbox refusing a test server's `127.0.0.1` bind; that affected
HTTP module passed 5/5 with loopback permission. The remaining gates passed:

- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 adapters, 0 issues;
- `git diff --check`: passed.

## Scope

This accepts the `.283` GovernmentJobs behavior only. It does not overwrite
the `.281` Fresh100 measurement, rerun Frozen100, open sealed cohorts, enable
coordinator-v2, touch the plugin, or merge the isolated LLM branch.
