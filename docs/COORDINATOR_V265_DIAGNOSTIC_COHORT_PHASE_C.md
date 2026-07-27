# v265 Backend Diagnostic Cohort - Phase C

## Decision

Accept the frozen `.261` run as deterministic development evidence and advance
one provider-family cluster to `.266`.

## Results

Artifacts:

`/private/tmp/v265-diagnostic-run1`

- Website: 26/30;
- Career: 24/30;
- verified Job List: 20/30;
- S7 Exact: 5/30;
- full same-version replay: 30/30 reproduced;
- mismatch: 0;
- fixture gap: 0;
- extra or unconsumed request divergence: 0.

## Exact Safety Audit

All five Exact openings preserve company, provider, tenant, title and location:

| Company | Provider | Target location | Result |
| --- | --- | --- | --- |
| Sol | Ashby `sol` | San Mateo, CA | verified Full Stack Engineer |
| krea.ai | Ashby `krea` | San Francisco, CA | verified Backend Engineer |
| Essentia Health | Workday `essentiahealth/Essentia_Health` | Duluth, MN | verified Clinical Research Assistant |
| Infleqtion | Workable `coldquanta` | Louisville, CO | verified Cybersecurity Analyst |
| Moog Inc. | Workday `moog/MOOG_External_Career_Site` | Blacksburg, VA | verified Quality Engineer |

Wrong URL, wrong location, cross-company and cross-tenant publication are zero.

## Causal Decision

Top Prospect Group and Kavaliro both reached customer-owned dynamic job boards
that load `/js/combobo.js`, expose `JBSearchList_form` and rely on
`/json/index.smpl`; generic S6 read the HTML shell but not the inventory.

Historical Madison-Davis supplies the third independent company with the same
Haley Marketing HMG contract. Focused anonymous provider probes then established
one still-open Exact and two complete target-title zero inventories. This is a
shared provider protocol with three expected terminal recoveries, so it
qualifies for `.266`.

The other `.265` failures remain separate one- or two-company causes, including
custom Vennture inventory, KFC JavaScript state, WordPress pagination, iCIMS
inventory, incorrect blog selection and duplicate Workday title/location
candidates. They are not bundled under `OPENING_DISCOVERY_INCOMPLETE`.

## Next Step

Implement and verify only the Haley Marketing adapter cluster under:

- `docs/COORDINATOR_V266_HALEY_MARKETING_PHASE_A.md`
- `docs/COORDINATOR_V266_HALEY_MARKETING_PHASE_C.md`

Plugin work, authenticated External Apply, coordinator-v2, LLM and sealed
holdouts remain frozen.
