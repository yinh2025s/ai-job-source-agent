# v247 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts: `/private/tmp/v247-diagnostic-run1`

- records: 30;
- independent companies: 27;
- Website: 27/30;
- Career: 20/30;
- verified Job List: 15/30;
- S7 Exact: 8/30.

The input spans six role families and has zero LinkedIn job-ID overlap with
Fresh100, the v245 diagnostic cohort and the v246 diagnostic cohort. It is a
development-only evidence cohort and does not alter Fresh100 or holdout scores.

## Exact Safety Audit

All eight published opening URLs were inspected against the captured first-party
and provider evidence:

- correct company, title and location: 8/8;
- correct provider and tenant: 8/8;
- wrong URL, cross-company or cross-tenant publication: 0;
- Exact precision: 100%.

The two SpaceXAI records are supported by a continuous LinkedIn company URL to
`x.ai`, first-party `x.ai/careers/open-roles` and Greenhouse tenant `xai`
identity chain. They are not SpaceX openings incorrectly assigned to xAI.

## Causal Findings

No implementation-qualified cluster was found. The apparent stage and budget
groups split into independent code paths:

- Texas A&M: the correct `tamu.edu` candidate had a TLS handshake timeout;
- Veterans Affairs: fetched LinkedIn-official `va.gov` was rejected as a
  parent/group identity;
- Schaeffler, U.S. Pacific Fleet and StevenDouglas: official hosts were
  persistently forbidden, but no common correct downstream candidate exists;
- Swig: a verified first-party Harri handoff reaches an unsupported JS shell;
- Honey Mama's and pmtbox: no correct Career/provider candidate was produced;
- Handraise: the correct specific opening was fetched, but title, location and
  apply evidence begins after the current 200 KB HTML validation slice;
- Alaska Club: ApplicantPro `/jobsearch/` is outside the adapter tenant route
  contract;
- Amcor: its first-party page declares a Firebase jobs collection without a
  supported inventory transport;
- Marriott: a sub-brand board outranks the correct company-wide inventory;
- Microsoft: first-party Eightfold proxy/tenant continuity is not recovered;
- American Battery Technology Company: a numeric Workable embed is not
  promoted;
- Crete United: `recruiting.ultipro.com` is outside the current UltiPro host
  contract;
- Pigment: a real multi-location Lever opening is rejected because only the
  provider's primary location is interpreted;
- thyssenkrupp and NevadaNano: complete official inventories support Verified
  No Match;
- MaineHealth and IDEA Public Schools: the verified official inventory path is
  externally blocked.

The three official-host 403 records form a classification group, not a recall
fix: one common change cannot produce the missing provider relationship for all
three. The four records ending in fetch-budget errors are also a false cluster:
Harri support, rate limiting, candidate absence and HTML truncation require
different code paths.

Handraise's bounded-HTML truncation and Swig's Harri handoff are retained as
high-value singleton evidence. Historical development artifacts are being
audited for two additional independent companies on either exact path before
implementation is authorized.

## Replay Integrity

The same-version offline bundle exported and replayed all 30 records:

- reproduced: 26;
- mismatch: 4;
- fixture gap: 0.

The four mismatches are three separate shapes:

1. Two SpaceXAI postings: live selected the correct opening, while replay found
   two S7-valid same-tenant title candidates and returned
   `OPENING_IDENTITY_AMBIGUOUS`.
2. Crete United: the live generic UltiPro path ended as `FETCH_FAILED`; replay
   preserved the same absence but emitted `COMPANY_TIME_BUDGET_EXHAUSTED`.
3. Pigment: live stopped on incomplete Lever inventory; replay consumed the
   stored inventory, selected the real multi-location opening and correctly
   rejected its primary-location projection at S7.

Each shape currently affects only one independent company. The report therefore
does not weaken exact-conflict handling, tape consumption, location validation
or runtime-only provider policy.

## Decision

No Phase B product change is authorized from this cohort. Continue backend-only
causal evidence collection on the unchanged `.246` code until one path has at
least three independent companies, one common trigger and code path, and an
expected recovery of at least three companies.
