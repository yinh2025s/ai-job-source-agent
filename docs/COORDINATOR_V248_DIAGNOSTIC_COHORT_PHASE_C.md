# v248 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts: `/private/tmp/v248-diagnostic-run1`

- adapter version: `2026-07-27.246`;
- records: 30;
- independent companies: 29;
- Website: 29/30;
- Career: 20/30;
- verified Job List: 11/30;
- S7 Exact: 7/30.

The input spans six role families and has zero LinkedIn job-ID overlap with
Fresh100 and the v245-v247 development cohorts. Public LinkedIn cards do not
establish External Apply state, so `apply_mode=unknown` is not interpreted as
an absent external target.

## Exact Safety Audit

All seven Exact openings pass the captured-evidence audit:

- correct company, title and location: 7/7;
- correct provider and tenant: 7/7;
- specific, open canonical opening URL: 7/7;
- wrong URL, cross-company, cross-tenant or wrong-location publication: 0.

The audited companies are The Clearing House, Meta, Discord, Acorns, Notion
and two Netflix postings. Discord is supported by a verified Greenhouse
`discord` inventory and opening `8546664002`. Its missing published
`job_list_page_url` is a one-company output-consistency debt, not an unsafe
Exact or a reason to weaken the publication gate.

## Causal Findings

No implementation-qualified recovery cluster was found. The apparent stage
groups split into distinct triggers and code paths.

### Upstream

Corsair, Lennar, MedReview, MyEyeDr. and Dior encounter persistent official-host
access blocks. IMDb, Kaizen Food, POP MART, MrBeast and Thom Browne do not
produce a correct Career or provider candidate. The five-company 403 group is
only a transport classification group: the same change cannot recover a
provider relationship or inventory for all five. The S4 budget group is also
not a recovery cluster because the independent S5 search wave produces no
valid downstream candidates.

### S5 and S6

- Tellihealth exposes only a LinkedIn company Jobs surface, not independent
  public inventory.
- ClassWallet exposes a numeric `whr_embed(564001)` Workable configuration that
  the current candidate path does not promote.
- Confidential loops on its root and skips the page's live-search transport.
- Trunk Space URL deduplication loses the stronger `Openings` anchor evidence.
- sweetgreen composes a relative JSON endpoint in a helper expression.
- Funhouse's direct target link remains below the current evidence threshold.
- Tech Mahindra exposes an ASP.NET WebForms inventory.
- Universal Music Group exposes a cross-domain first-party Careers handoff that
  is not currently action-classified.
- Opstergo embeds the target opening in uncommon static HTML structure.
- Sparksoft uses dynamic Nuxt inventory.

These findings require different parsers, relationship contracts or provider
transports. Grouping them as `JOB_BOARD_NOT_FOUND` or
`OPENING_DISCOVERY_INCOMPLETE` would repeat the rejected stage-label clustering.

## Replay Integrity

The same-version bundle exported and replayed all 30 records:

- reproduced: 29;
- mismatch: 1;
- fixture gap: 0.

Equip retains the same company identity and absence of a published opening, but
the live terminal is `FETCH_FAILED` while replay emits
`COMPANY_TIME_BUDGET_EXHAUSTED`. This is one independent company and does not
authorize a global terminal-equivalence change.

## Decision

No Phase B product change is authorized from v248. The frozen `.246` backend
remains the implementation baseline. Continue backend-only evidence collection
until one failure path has:

1. at least three independent companies;
2. the same observable trigger;
3. the same production code path;
4. an expected recovery of at least three companies;
5. focused live, replay and negative-control acceptance inputs.

Fresh100 metrics remain 37 Exact, 12 Verified No Match, 1 External Blocked and
50 unresolved. The extension, coordinator-v2, LLM branch and sealed holdouts
remain frozen.
