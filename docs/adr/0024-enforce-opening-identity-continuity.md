# ADR-0024: Enforce Opening Identity Continuity

- Status: accepted
- Date: 2026-07-14

## Context

The pipeline can currently discover an official company career page and then
follow a portfolio-company link to an unrelated ATS tenant. A matching title on
that tenant can be published as an exact opening because S3-S6 exchange URLs and
provider names, while S7 only checks duplicate stage results. Fresh Ventures to
Notion is the frozen regression case that exposed this contract defect.

An official career page is evidence about the company, not blanket authorization
for every job-board link rendered on that page. Exact output therefore needs a
continuous, typed identity assertion from the source company through the opening.

## Decision

### Typed Evidence

The stage contract carries three immutable values:

1. `HiringIdentityEvidence` binds the source company to the resolved hiring
   entity and records the relationship, verification method, evidence URL, and
   whether that relationship was verified.
2. `ProviderIdentity` binds the hiring entity to a provider tenant and canonical
   board URL. Its verification method must explain why that tenant is authorized;
   discovering a link on an official page is not sufficient by itself.
3. `OpeningIdentity` binds the selected opening URL to the same provider, tenant,
   and canonical board.

These values are public, versioned stage data. Later stages must not reconstruct
them from another stage's private trace.

### Authorization And Continuity

1. A same-entity relationship is verified when S3 retains the source company.
   Parent, acquired-brand, or alternate-employer relationships require explicit
   structured evidence from the identity resolver.
2. A provider board is authorized only by a generic verification rule: a verified
   hiring relationship plus a direct identity career root, a matching provider
   tenant, or a first-party same-site board. No company-name exception is allowed.
3. Provider tenant identity is derived from the native adapter's board locator.
   Generic first-party boards use their canonical board URL as the tenant locator.
4. S6 must re-identify a native opening with the provider adapter and prove that
   it maps to S5's provider tenant and canonical board. Generic openings must stay
   on the verified first-party site. A title match never supplies identity proof.
5. S7 independently validates every exact output. Missing, unverified, malformed,
   or conflicting identity evidence produces `RESULT_IDENTITY_MISMATCH` and the
   pipeline cannot publish exact success.
6. The LinkedIn external-apply path must preserve its typed `DiscoveredJobBoard`;
   it cannot bypass provider identity validation.

### Persistence And Compatibility

The identity objects have strict checkpoint payloads and their own schema
version. The pipeline contract and adapter versions are bumped when this decision
lands, invalidating incompatible checkpoints. Checkpoint reset clears all three
identity values and the job-board portfolio.

Older public results remain readable, but they cannot be counted as verified
exact output without the new identity assertion. Evaluation and replay report
identity comparison as unavailable instead of inferring it from URLs.

### Availability And Errors

Identity failure is a system defect, not `OPENING_NOT_FOUND`. Typed transport and
provider failures retain their reason, retryability, status, and request identity.
Only complete non-empty inventory can yield `OPENING_NOT_FOUND`; verified empty
company-wide inventory yields `NO_PUBLIC_OPENINGS`; unresolved inventory or
identity evidence yields `OPENING_DISCOVERY_INCOMPLETE`.

## Consequences

Positive:

- Cross-company and cross-tenant title matches fail closed.
- Acquired-brand and parent-company hiring can still pass with explicit evidence.
- Replay and evaluation can compare a stable identity chain rather than private
  trace shape.

Costs and limits:

- Exact rate may decrease while previously implicit tenant relationships are
  classified as incomplete.
- Native adapters whose detail URLs cannot be re-identified cannot produce exact
  success until their existing board locator contract is made complete.
- This ADR does not add providers or company-specific discovery behavior.

## Validation

- Contract tests cover Fresh-to-Notion rejection, valid same-tenant continuity,
  explicit parent/acquired-brand relationships, same-title cross-tenant rejection,
  missing native identity, checkpoint round trips, and reset behavior.
- Evaluation tests cover precision, conditional recall, raw exact rate, system
  defect rate, six exclusive dispositions, and unknown eligibility.
- Replay tests require identity-related failures to reproduce the same normalized
  chain and failure code.
- After all offline gates pass, the main workstream runs one frozen cohort once.

