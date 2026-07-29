# Coordinator `.284` iCIMS Custom-Shell Phase C

Date: 2026-07-29

Decision: **accepted for same-portal iCIMS shells**

## Contract

The accepted provider path is:

```text
safe public *.icims.com root or /jobs/intro
-> strong iCIMS iframe runtime evidence
-> one same-origin in_iframe=1 target
-> same-origin public searchForm
-> canonical query-free /jobs/search board
-> same-tenant title-filtered inventory
-> card-local title, location and opening
```

Root and `/jobs/intro` URLs are not accepted by hostname alone. HTTP,
credentials, non-standard ports, suffix-confusion hosts, multiple distinct
iframes, cross-origin frames/search actions, login/profile/onboarding pages and
HRSmart-only handoffs fail closed.

iCIMS `US-STATE-CITY` card locations are normalized only after an exact
card-local location label. Page filters, descriptions and adjacent cards are
not location evidence.

## Development Cohort

New public development controls were collected independently of sealed or
blind cohorts:

| Company | Target | Result |
| --- | --- | --- |
| Bluehawk | Data Scientist - Mid-Level; Hawaii | exact opening `2779` |
| Hyland | Treasury Analyst; Remote U.S. | exact opening `14262` |
| Wheels Up | Flight Controller (Expression of Interest); Chamblee, GA | exact opening `3624` |
| Room & Board | Safety and Compliance Manager; Golden Valley, MN | exact opening `5009` |

The first provider probe found all four boards and openings but exposed
`location=None`; it is retained as a failed diagnostic. After restoring the
previously rejected card-local parser under the new four-company evidence
threshold, the code-frozen run2 produced 4/4 complete title-filtered
inventories with exact title, location and same-tenant opening URLs.

Provider snapshots:

`/private/tmp/icims-shell-v284-focused-live-run2`

The 12 responses materialized as 13 fixture views. Offline replay reproduced
all four provider results with zero failure or corrupt-tail record.

## End-To-End Gate

Bluehawk is an unsealed development record. The first full run found the
correct opening but S7 rejected raw `US-HI-` as
`OPENING_LOCATION_MISMATCH`. The provider now normalizes that empty-city code
to `HI, United States`; the strict S7 rule is unchanged.

The accepted snapshot-backed run is:

`/private/tmp/icims-shell-v284-focused-e2e-bluehawk-run4`

Live and empty-checkpoint offline replay both returned:

`https://careers-bluehawk.icims.com/jobs/2779/data-scientist---mid-level/job`

The replay capsule contains seven page records, six fixture views, two
duplicate records, zero failures, zero corrupt tails and the same S1-S7
terminal. An earlier snapshot attempt with 43 `DNS_FAILED` records is excluded
as an environment-contaminated diagnostic.

## Safety And Residual

- wrong URL: 0;
- wrong location: 0;
- unsafe URL: 0;
- cross-company publication: 0;
- cross-tenant publication: 0.

Cretex remains open. Its aggregate shell declares one search board but
publishes openings on multiple child iCIMS hosts. `.284` discovers the shell
but preserves same-tenant rejection; it does not use the iCIMS parent domain
as permission to cross portal tenants. Highgate/HRSmart remains a negative
control.

## Offline Gate

- related tests: 181/181;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 adapters, 0 issues;
- full suite: 2,870 tests, 4 skipped, with only the managed sandbox refusing a
  loopback bind;
- affected loopback HTTP module: 5/5 with local-socket permission;
- `git diff --check`: passed.

## Scope

`.284` does not overwrite the `.283` Fresh100 cold measurement, rerun
Fresh100/Frozen100, access a sealed cohort, enable coordinator-v2, change the
plugin or merge the isolated LLM branch. The authoritative Fresh100 raw Exact
count remains 36/100 until a later code-frozen full measurement gate.
