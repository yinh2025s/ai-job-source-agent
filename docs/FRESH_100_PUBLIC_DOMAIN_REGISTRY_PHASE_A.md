# Fresh 100 Public-Domain Registry Phase A

## Decision

Records 011, 041, 043, and 045 form an executable four-company candidate-
generation cluster. Each input names an independent U.S. city or state
government; the correct `.gov` root is absent from mechanical and search
candidates; and the CISA `.gov` registry publishes organization, domain type,
city, and state evidence for the entity.

This contract does not cover nested agencies 008/023 or education record 024.
It also does not establish Career, provider, tenant, inventory, or opening
identity.

## Authoritative Source

CISA/get.gov states that it is the authoritative registry for `.gov` domains
and publishes a daily complete CSV:

- https://get.gov/about/data/
- https://raw.githubusercontent.com/cisagov/dotgov-data/main/current-full.csv

The current CSV schema contains `Domain name`, `Domain type`, `Organization
name`, `Suborganization name`, `City`, and `State`. The four development inputs
have exact organization records under the expected government type and state.
State of Montana intentionally has more than one valid domain, so registry
matching produces a bounded candidate set rather than declaring one URL as the
website.

## Shared Trigger And Code Path

Trigger:

1. Input identity has an explicit `City of ...` or `State of ...` government
   form and a U.S. city/state location.
2. Direct LinkedIn/External Apply evidence has not already established a
   stronger route.
3. Ordinary source-backed website discovery has not produced a verified
   identity.

Shared path:

1. Query a versioned `PublicDomainRegistry` interface backed by the CISA CSV.
2. Require normalized organization equality, compatible domain type, exact
   state, and exact city for city entities.
3. Emit at most a small bounded set of source-backed HTTPS candidates with CSV
   URL, dataset digest, row identity, and retrieval timestamp provenance.
4. Pass every candidate through ordinary fetch, redirect, canonical, company
   identity, Career, provider, tenant, title, location, and S7 gates.

No `.gov` URL is synthesized from a city/state name. Registry membership ranks
a candidate but cannot by itself make website resolution successful.

## Negative Controls

- Same city name in another state.
- County, school district, special district, or federal row for a city input.
- `state.gov` and other token-overlap collisions.
- Partial organization-name matches such as a county containing a state name.
- Multiple registry domains: all remain candidates until first-party identity
  verification; list order cannot authorize a winner.
- Missing, stale, malformed, unexpectedly large, or schema-changed CSV fails
  closed and leaves other discovery routes available.
- Security contact data is neither persisted nor emitted; only non-personal
  organization/domain fields are retained.

## Acceptance And Rollback

Development acceptance is 4/4 source-backed correct website candidates for
records 011/041/043/045 with zero wrong-state, wrong-government-type, or
`state.gov` candidates selected as a website. GovernmentJobs record 043 is a
separate downstream provider overlay and cannot be used to inflate this S2
contract.

Focused live must use fresh checkpoint, completion, evidence, snapshot, replay,
and output roots. It must audit every emitted Website/Career/Board/Opening URL
and replay 4/4 with zero mismatch or fixture gap. If fewer than 4/4 candidates
are produced, or one identity collision survives, the cluster is rejected and
re-split before another repair.

The feature remains disabled until two zero-overlap blind holdouts are sealed.
Rollback is removal of the registry discovery source; existing website and
provider routes remain unchanged.
