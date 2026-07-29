# Coordinator `.285` iCIMS Aggregate-to-Child Route Phase A

Date: 2026-07-29

Decision: **accepted for one bounded Phase B contract**

## Executable Cause

Some public iCIMS customers expose an official aggregate inventory on one
portal host and publish individual openings on a bounded set of child portal
hosts. The current iCIMS adapter treats the aggregate hostname as the provider
tenant and silently rejects every child-host candidate before title and
location matching.

This is not permission to trust sibling `*.icims.com` hosts. The recoverable
trigger is:

```text
verified first-party Career handoff
-> typed iCIMS aggregate board
-> title-filtered aggregate response
-> exact child opening anchor in one job card
-> matching provider customer identity on source and child detail
-> matching opening ID, title, location and employer evidence
```

The production baseline on the Cretex control demonstrates the rejection:

- `.284` identifies
  `https://cretex-companies.icims.com/jobs/search`;
- the official title-filtered response contains one exact card for opening
  `5219` at `US-MN-Elk River`;
- that card publishes
  `https://careers-cretex.icims.com/jobs/5219/it-cyber-security-risk-analyst/job`;
- `_candidate_from_html_link` rejects the candidate because the child host is
  not the aggregate board host;
- the adapter reports a complete title-filtered inventory with zero
  candidates.

## Independent Development Controls

The controls below are public, anonymously accessible and independent of
sealed or blind cohorts. Live evidence is isolated under
`/private/tmp/phase-a-icims-multiportal-main` and
`/private/tmp/phase-a-cretex-multiportal-main`.

| Company | Official aggregate | Exact child control | Provider identity |
| --- | --- | --- | --- |
| Cretex Companies | `cretex-companies.icims.com/jobs/search` | `careers-cretex.icims.com/jobs/5219/.../job`, IT Cyber Security Risk Analyst, Elk River, MN | customer marker `cretex.icims.com`, hub `15` |
| Emory Healthcare | `ehccareers-emory.icims.com/jobs/search` | `clinical-emory.icims.com/jobs/170893/.../job`, Medical Assistant, Johns Creek, GA | customer marker `emory.icims.com`, hub `14` |
| Ho-Chunk Inc. | `hub-hochunk.icims.com/jobs/search` | `careers-allnativegroup.icims.com/jobs/10483/.../job`, Cable Foreman, Washington, DC | customer marker `ho-chunk.icims.com`, hub `26` |

Each official employer Career page directly declares its aggregate inventory.
Each aggregate title search publishes the selected child URL in the same job
card as the opening title, location and numeric ID. The source search and child
detail also expose the same customer-specific dynamic-portal marker. Live HTTP
responses independently agree on the iCIMS customer, organization, tenant UUID
and customer ID, but response headers are corroboration only because the
current replay contract does not persist them.

Cretex's full aggregate inventory currently publishes four child hosts;
Emory publishes clinical, non-clinical and nursing child hosts; Ho-Chunk
publishes openings for its managed business groups. Child membership must be
derived from the current aggregate response, never from a static allowlist.

The three controls provide three expected provider-level terminal recoveries.
Only Cretex belongs to the Fresh100 development cohort. Emory and Ho-Chunk are
provider controls and must not be used to increase the Fresh100 score.

## Frozen Route Contract

Phase B must add immutable candidate-scoped
`ProviderOpeningRouteEvidence`. It must contain:

- provider;
- source tenant and canonical aggregate board;
- target tenant and canonical child board;
- canonical child opening URL;
- exact source response URL;
- provider customer identity observed on the aggregate response;
- bounded provider route identity (`hub` for iCIMS);
- extraction method and schema version.

The iCIMS adapter may emit this evidence only when:

1. the source is an already typed, safe, replay-safe iCIMS search board;
2. the response remains on that source tenant;
3. a child URL is an HTTPS, credential-free, standard-port
   `/jobs/<numeric-id>/<slug>/job` route;
4. the child URL is the anchor of the same `iCIMS_JobCardItem` as the parsed
   title and location;
5. the child URL contains one positive integer `hub` value;
6. the aggregate page exposes exactly one safe customer-specific
   `<tenant>.icims.com/icims2/servlet/icims2` runtime marker;
7. duplicate evidence for one opening is identical; conflicts fail closed.

The child detail must then be fetched and independently verify:

- the same provider customer identity;
- the same numeric opening ID;
- the same route identity when the detail declares it;
- canonical child host and route;
- title and location continuity;
- non-conflicting provider-published employer evidence.

Central identity validation must consume the typed route. Ordinary openings
continue to require provider, tenant and board equality. A source-to-target
tenant or board transition is legal only when the exact selected opening is
bound to a fully verified route evidence object. The selected opening and
opening identity must retain the target tenant and child board; they must not
be relabeled as the aggregate tenant merely to satisfy S7.

## Safety Rejections

Phase B must reject:

- arbitrary sibling `*.icims.com` hosts;
- a child URL that appears outside a job card;
- a valid child URL not published by the current aggregate response;
- absent, duplicate, malformed or conflicting customer markers;
- absent, duplicate, non-numeric or conflicting `hub` values;
- child detail customer-marker mismatch;
- opening ID, title, location or employer conflict;
- cross-origin source response redirects;
- HTTP, credentials, non-standard ports, fragments or sensitive queries;
- login, profile, onboarding, referral and apply routes;
- evidence reconstructed from search snippets or private trace dictionaries.

Required cross-tenant controls include:

- Emory aggregate paired with Ho-Chunk's All Native Group opening;
- Ho-Chunk aggregate paired with Cretex's QTS opening;
- a source card whose visible title and child anchor belong to different
  records;
- two child tenants claiming the same opening ID;
- a valid route object changed after checkpoint serialization.

Neither a common `icims.com` suffix, a valid `/jobs/<id>/.../job` path, a
matching `hub` number nor title similarity can authorize a route alone.

## Ownership

Main-line shared-contract ownership:

- `job_source_agent/identity_continuity.py`
- `job_source_agent/opening_selection_validation.py`
- `job_source_agent/stages/discovery.py`
- identity schema, checkpoint migration and shared contract tests

iCIMS provider ownership:

- `job_source_agent/providers/base.py`
- `job_source_agent/providers/icims.py`
- `job_source_agent/opening_matcher.py` only for provider detail attestation
- `tests/test_provider_icims.py`

Composition root, provider registry, plugin, coordinator-v2 and the isolated
LLM branch are out of scope.

## Acceptance

1. Cretex, Emory and Ho-Chunk each produce the exact child opening with typed
   source-to-target route evidence.
2. Each child detail revalidates provider customer, opening ID, title and
   location.
3. Cretex reaches S7 Exact through a snapshot-backed full pipeline run.
4. Provider controls and the Cretex pipeline replay from empty checkpoints
   with zero mismatch and zero fixture gap.
5. Same-tenant iCIMS behavior remains unchanged.
6. Cross-company, undeclared-child and marker-mismatch controls publish no
   opening.
7. Wrong URL, wrong location, cross-company and cross-tenant publication are
   all zero.

Focused success does not overwrite the `.283` Fresh100 36/100 measurement.

## Rollback

The route contract must be reverted if it requires a static company/host
allowlist, treats the iCIMS parent domain as tenant proof, relabels child
identity as parent identity, cannot replay deterministically or produces fewer
than three provider-control recoveries. A Cretex-only recovery is insufficient
to retain the shared-contract change.
