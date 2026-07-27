# v251 Hireology Provider Family - Phase A

## Hypothesis

Three independent employers publish current LinkedIn openings and hand off to
public `careers.hireology.com/<tenant>` inventories. The backend can fetch the
official Hireology surface but has no native provider contract, so S5 does not
verify the board and S6 does not read the specific opening.

This is a provider-family hypothesis, not a company rule.

## Frozen Inputs

Provider-isolation input:
`/private/tmp/hireology-provider-isolation-input.json`

1. San Diego Padres - Executive Assistant to the President of Baseball
   Operations - San Diego.
2. Mills Automotive Group / Classic Toyota of Henderson - General Sales
   Manager - Henderson.
3. Tim Moran Hyundai - Parts Runner - Hemet.

The Padres record is a public LinkedIn search card from v250. Mills and Tim
Moran are current public LinkedIn job-detail records that did not appear in the
first 40 public search cards during focused materialization. Their evidence
source is kept distinct; neither is described as a search-card hit.

RWC Group was removed before Phase B. Its official Career page does hand off to
Hireology, but the frozen Pasco target is absent from the provider's complete
current 68-opening inventory. It is a closed/expired diagnostic control, not an
expected recovery.

## Shared Trigger

Expected shared evidence:

- first-party or source-supported employer identity;
- public Hireology tenant root;
- public numeric Hireology detail route;
- target title and location on the same tenant;
- no existing registered Hireology adapter.

## Proposed Ownership

If and only if frozen `.246` live reproduces all three:

- add `job_source_agent/providers/hireology.py`;
- add `tests/test_provider_hireology.py`;
- add provider fixtures under the existing provider fixture boundary;
- mainline alone updates provider registration, version and shared docs.

The adapter must not add company names, domains or job IDs.

## Safety Contract

A future adapter must:

1. accept only HTTPS `careers.hireology.com/<tenant>` board roots;
2. accept only same-tenant numeric detail routes ending in `/description`;
3. recover inventory from bounded public first-party Hireology evidence;
4. verify employer text, title, location and open/apply state;
5. reject credentials, non-standard ports, cross-tenant redirects, ambiguous
   tenants, login/profile paths and non-specific roots;
6. keep all S7 identity, title and location gates unchanged.

## Phase A Gate

Before implementation, frozen `.246` must show 3/3:

- correct employer Website/Career continuity;
- correct Hireology tenant/detail evidence present in captures;
- no verified Job List or Exact solely because the provider is unsupported;
- same provider-family production path;
- replayable evidence or an explicitly classified replay integrity gap.

If fewer than three reproduce, reject the cluster and retain it as evidence.
