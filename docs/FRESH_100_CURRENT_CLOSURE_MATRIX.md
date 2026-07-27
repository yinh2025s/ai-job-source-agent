# Fresh100 Current Closure Matrix

## Status

This is a conservative development-cohort projection after `.244`, not an
official code-frozen 100-record rerun.

`.261` changes Career transport scheduling and terminal attribution only. Its
four-record diagnostic input is outside this Fresh100 projection and produced
no new Career, Job List or Exact result, so the table below is unchanged.

| State | `.220` cold baseline | Proven focused delta | Projected current |
| --- | ---: | ---: | ---: |
| S7 Exact | 19 | +18 | **37/100** |
| Evidence-backed Verified No Match | 6 | +6 | **12/100** |
| Evidence-backed External Blocked | 1 | 0 | **1/100** |
| Unresolved / nonterminal | 74 | -24 | **50/100** |
| Published wrong URL/company/tenant | 0 | 0 | **0** |

The seventeen proven Exact recoveries are Hays + Sons, Sunbird Software, IMG, Arkema
`4424789545`, Cintas, STEAMe, BWXT, Salas O'Brien and NYC Department of Social
Services, Target Hospitality, Brown and Caldwell, Slant CRM, B&D Industries
Project Manager, Northern Clearing Project Manager, Milwaukee Tool, Lorum and
Team Royal. Slant's
`.232` provider-published-employer Exact was previously described but omitted
from the arithmetic. Focused successes do not replace the required future
code-frozen cold benchmark.

The ten Verified No Match records are Matlen Silver, City of Pharr, SDS
International, IGNITE, PACS, Ken Garff Automotive Group, QXO, and North Dakota
Information Technology, Steampunk and Dechert. Their durable
`availability_diagnostic` is
`verified_inventory_no_match`; they are acceptable evidence-backed terminals,
not SYSTEM_GAP. Manual evaluation annotations remain absent from the focused
runs, so this is a terminal-semantics projection rather than an official
disposition report.

Altec is the one External Blocked record: its Website, Career and official
`jobs.altec.com` inventory relationship are verified before the inventory
returns persistent HTTP 403. Other 403-like records remain unresolved when the
failure belongs to an unverified candidate or occurs before a provider
relationship is established.

## Latest Durable Terminals

| Terminal reason | Records | Durable outcome |
| --- | ---: | --- |
| Exact opening | 37 | S7 verified |
| `OPENING_NOT_FOUND` with verified inventory | 12 | Verified No Match |
| Official inventory `HTTP_FORBIDDEN` | 1 | External Blocked |
| `JOB_BOARD_NOT_FOUND` | 20 | unresolved; the shared stage label has multiple causes |
| `OPENING_DISCOVERY_INCOMPLETE` | 14 | unresolved; multiple inventory/provider shapes |
| Retryable failures | 11 | 6 fetch-budget, 2 company budget, 1 fetch failure, 2 provider fetch failures |
| `RESULT_IDENTITY_MISMATCH` | 3 | unresolved and fail closed |
| `COMPANY_IDENTITY_AMBIGUOUS` | 4 | unresolved and fail closed |
| `JOB_BOARD_PORTFOLIO_INCOMPLETE` | 2 | Crosby and STRIKE |
| `INVALID_STRUCTURED_DATA` | 1 | ARUP UltiPro response/budget boundary |

Total unresolved: 50. The separate 12 Verified No Match and one External
Blocked record are acceptable evidence-backed terminals and are excluded from
the causal backlog.

## Projection Method

Records are keyed by canonical LinkedIn job URL. Later code-frozen focused
artifacts replace the `.220` record only for the same job ID, in this order:

1. `.220` 100-record cold baseline
2. `.221` LinkedIn input focused run
3. `.222` detail-location cold run2
4. `.224` SuccessFactors cold run2
5. `.225` portfolio focused run
6. `.225` known-host transport run
7. `.226` official-candidate failure-attribution run
8. `.227` provisional official-site five-record run
9. `.228` Lubbock checkpoint integration run
10. `.229` S5-S6 checkpoint/replay integration runs
11. `.230` UltiPro display-address focused run
12. `.231/.232` ambiguous Website and tenant-probe safety runs
13. `.233` captured producer-state replay repair
14. `.233` ten-record transport diagnostic
15. `.234` LinkedIn-official alias focused runs
16. `.235` multi-hop provider replay repair
17. `.235` known-domain transport diagnostic
18. `.235` generic S5-to-S6 refetch diagnostic
19. `.235` seven-record causal-cluster reproduction
20. `.235` five-record historical-timeout reproduction
21. `.244` Hawaiian Electric page-evidenced provider Career run

This produces exactly 100 unique records. Opening-path-only injected evidence
is excluded unless its terminal is explicitly listed in
`samples/evaluation/fresh100_development_projection_acceptance.json`.

`.231/.232` do not alter aggregate terminal counts. They remove unsafe identity
promotion: Focus no longer publishes an unrelated Website, and Focus, OneApp's
Ashby tenant and STRIKE remain unauthorized without an observed handoff.
Slant CRM remains Exact through provider-published employer evidence. The final
four-record `.232` capture replays 4/4 with zero mismatch or fixture gap.

The `.233` ledger audit corrects the earlier arithmetic: Slant was still carried
inside the old provider-fetch unresolved bucket even though the same-version
`.232` capture had already proven its Exact terminal. No live result changed;
the projection is therefore 31 Exact and 60 unresolved before `.234`.

`.233` changes replay determinism only. A legitimate Versana stored-board
capture reproduces its Exact result, while the historical polluted Focus state
is rejected before tape execution. It does not replace any live terminal in
this projection.

The later `.233` ten-record cold diagnostic rejects the historical transport
label as a common implementation cluster. Brown and Caldwell now has a current
audited Wailuku Exact and replaces its older verified no-match projection.
BWXT and Salas O'Brien reproduce already-counted Exact results. The run replays
10/10 with zero mismatch or fixture gap.

`.234` closes a three-company resolver defect without changing aggregate
terminal counts. RLB, Jushi and Heritage all recover their LinkedIn-official
Websites under strict first-party alias evidence. RLB reaches its Career page,
Jushi reaches its Lever board, and Heritage reaches its Paylocity board before
an opening-phase company budget terminal. `.235` then restores Jushi's captured
same-host multi-hop provider producer state; the original capture replays 3/3,
and the clean Heritage retry replays 1/1. No opening URL is published by these
focused runs.

The later `.235` five-record known-domain diagnostic rejects another historical
transport grouping. All five records recover Website, Career and Job List
without a code change. B&D Project Manager and Northern Clearing become
S7-audited Exact; Steampunk becomes a verified complete-inventory no-match.
Vertiv remains identity ambiguous and B&D HR remains retryable. The first four
records replay 4/4; B&D HR has a separate retry-reason mismatch, so the full
five-record replay debt remains explicit.

The `.235` three-record generic-refetch diagnostic also rejects its historical
transport grouping. Milwaukee becomes an audited Exact, Dechert becomes a
verified complete-inventory no-match, and Crawford advances to a verified Job
List but remains inventory-incomplete. Replay is 3/3. Because no record
reproduces the proposed S5-to-S6 same-URL failure, a process-local page lease is
not implemented.

`.238` adds a versioned SearchBackend boundary after the current causal ledger
showed 12 `search_results_filtered_to_zero` records across 11 companies.
Default Bing/DuckDuckGo behavior is unchanged; optional SearXNG search remains
candidate-only and cannot bypass provider, tenant, hiring relationship,
inventory, title/location or S7 gates. No real SearXNG endpoint has been
configured, so this is not counted as a recovery and the durable development
projection remains 36 Exact, 10 Verified No Match, 1 External Blocked and
53 unresolved.

`.239` then runs the frozen 12-record search slice against an isolated local
SearXNG service. The valid same-version A/B is 1 raw Job List / 1 Exact for
legacy versus 7 raw Job List fields / 0 Exact for SearXNG, with 12/12 replay on
both sides. The six additional fields are not six verified recoveries: all lack
a complete identity chain, and CHAMP retains an unrelated Greenhouse partial
candidate. The final gate prevents an incorrect Exact, but the search cluster
does not close. `.240` restores the regressed iClassPro audited Paylocity Exact
through a strict LinkedIn display-descriptor identity rule and preserves DSV's
first-party site; focused replay is 1/1 for each. This regression guard does not
change aggregate durable counts.

`.241` closes the shared unsafe-publication contract, not the search-recall
cluster. A provider candidate with an explicitly unverified hiring relationship
remains available internally but no longer appears as a product
`job_list_page_url`; CHAMP's cross-company candidate is the negative control.
The five-record focused live preserves iClassPro Exact and publishes no board
for the other four records, with replay 5/5. Search volatility did not reproduce
all historical ambiguous candidates, so no new terminal is promoted and
aggregate durable counts remain unchanged.

`.242` closes the batch S5-to-S6 orchestration deadlock for a durable typed
identity-pending native-provider portfolio. Caesars demonstrably restores
S1-S5, reads complete Oracle title-filtered inventory in S6 and rejects the
Las Vegas opening for a Reno input; CHAMP publishes no cross-company URL.
Fabric and Prophetic did not reproduce their historical search candidates in
that run, so no aggregate terminal changes.

`.243` carries provider-owned employer evidence through the common S6 contract.
Real Fabric Greenhouse inventory proves tenant `fabric83`, employer `Fabric`
and a complete two-opening inventory with no Product Designer; focused S6/S7
therefore verifies the Job List and `OPENING_NOT_FOUND`. The end-to-end search
route was not reproduced in the code-frozen run, so this focused provider
success does not alter the 100-record projection. Caesars lacks Oracle employer
evidence. Prophetic has an exact Ashby opening but is discontinuous from the
unrelated Canadian Website selected by S2. The apparent three-provider recall
cluster is rejected as three causal roots.

`.244` replaces the two Hawaiian Electric `JOB_BOARD_NOT_FOUND` projections.
The same-site Career page contains strict SuccessFactors page evidence for
tenant `custom:hawaiianel`; both records continue through verified S5 and
complete native S6 inventory. The adapter inspected 84 and 25 candidates and
returned evidence-backed `verified_inventory_no_match`. Replay is 2/2 with no
wrong URL or identity publication. Because both records are one independent
employer, this is a focused contract-boundary closure, not a three-company
recall cluster or an official Fresh100 rerun.

The `.244` ledger rebuild also corrects one earlier acceptance-manifest
omission. iClassPro job `4441446072` was already a fully audited Paylocity
Exact in the code-frozen `.243` run and its 8/8 replay, but the projection
manifest still left it unresolved. Counting that existing reviewed terminal
raises Exact from 36 to 37 and reduces unresolved from 51 to 50; it is a
governance correction, not another `.244` runtime recovery.

The separate v245-v250 zero-overlap diagnostic cohorts do not replace any
Fresh100 record and therefore do not change this projection. v250 produced
eight audited Exact records with zero identity or URL safety errors, but its
non-Exact records did not establish a three-company common implementation
cluster. The two SKIMS records also exposed an explicit shared-tape replay
divergence. Those findings remain diagnostic evidence, not projected Fresh100
recoveries. The later Hireology provider-family gate is likewise reported
separately until its code-frozen focused live and replay complete.

The later `.235` causal reproductions reject two more stage-label groupings.
Seven records prove that the apparent generic inventory cluster combines
Consider, Eightfold and Bullhorn OSCP rather than one code path. A separate
five-record historical-timeout run reproduces zero timeouts and replays 5/5;
Lorum and Team Royal recover S7-audited Exact openings. Their company, title,
location, provider, tenant and opening URLs are explicitly accepted by the
development projection manifest. The aggregate becomes 36 Exact and
53 unresolved without claiming a product-code improvement.

`.226` does not change aggregate durable terminal counts because the independent
S5 coordinator still ends the three records as `JOB_BOARD_NOT_FOUND`. It does
remove false S2 attribution to `kxnet.com`, `cityoflubbock.com` and
`state-of-montana.com`; Montana retains only its current same-site `mt.gov`
timeout.

The provisional-site work narrowed its original five-record hypothesis to
NDIT, Montana and Lubbock. All three now reach verified Job Boards without
publishing an unsupported opening. NDIT advances to verified inventory
no-match. NYC independently reaches an S7-audited Exact after ordinary S2
resolution; Heritage did not qualify for that provisional path and was later
recovered by the strict `.234` official-alias path. Lubbock's `.228` full replay
exposes a separate S6 deadline-tape
determinism defect, so the projected aggregate does not treat its partial as a
terminal closure.

`.229` closes that S5-S6 checkpoint and typed provider-error replay defect;
the second Lubbock capture reproduces 1/1 and the three-tenant capture
reproduces 3/3, but no new terminal Exact or verified no-match is claimed.

`.230` reinterprets only the UltiPro provider's literal public
`DisplayAddress=true` contract. Target Hospitality retains the same verified
company, Career handoff, provider, tenant and opening ID while its provider
location changes from the organizational label `Corporate` to
`The Woodlands, Texas`; S7 then verifies the Exact opening. The all-outcome
capture replays 1/1 with no identity or URL change.

## Validation Debt

- `.225` focused live: 5/5 Job Lists, 1/5 Exact, zero unsafe publication.
- Scoped replay: 5/5 exported and replayed; 4 reproduced, 1 ARUP terminal-reason
  mismatch, zero fixture gap.
- Known-host transport rerun: 9/10 Websites recovered with no code change,
  2 Exact, and replay 10/10 with zero mismatch or fixture gap.
- `.227/.228` provisional-site focused live: three shared-path verified Job
  Boards, one ordinary-path Exact, one verified no-match, zero unsafe
  publication; the later `.229` capture closes Lubbock replay 1/1.
- `.230` Target Hospitality focused live: one audited UltiPro Exact and
  all-outcome replay 1/1; zero wrong URL, location, company or tenant.
- `.234/.235` official-alias focused live: 3/3 Websites, 3/3 Careers, 2/3 Job
  Lists across the code-frozen run plus one clean transport retry; original
  capture replay 3/3 and Heritage replay 1/1.
- `.235` causal reproductions: 7/7 plus 5/5 replay; unrelated provider families
  are no longer grouped, historical timeout reproduction is 0/5, and two
  audited Exact terminals are added through the explicit acceptance manifest.
- `.238` SearchBackend foundation: offline contract, privacy, configuration,
  composition and replay wiring are implemented.
- `.239/.240` local SearXNG gate: frozen A/B and both focused regression
  captures replay completely, but zero new S7 Exact recoveries means the
  11-company search cluster remains open.
- `.241` publication gate: focused replay 5/5 and verified iClassPro Exact
  preserved; unverified partial boards are no longer product Job Lists.
- `.242/.243` provider identity gate: focused live/replay proves S6 continuation
  and zero unsafe publication; real Fabric provider S6/S7 verifies a no-match.
  Relevant tests pass 506, provider benchmark 25/25, resolver 6/6 and
  architecture validation 46/0.
- The later `.243` eight-record causal run replays 8/8 and preserves one
  iClassPro Exact. Three companies share pre-search S4 transport exhaustion,
  but the independent search wave also produced zero valid candidates for all
  three. The budget label is therefore not treated as an actionable recall
  cluster and no aggregate terminal count changes.
- `.244` Hawaiian Electric focused live: 2/2 verified SuccessFactors Job Lists,
  two Verified No Match terminals, 2/2 replay and zero unsafe publication.
  Relevant integrated tests pass 458, provider benchmark 25/25, resolver 6/6
  and architecture validation 46/0.
- The post-`.244` three-workstream causal audit finds zero implementation-
  qualified clusters among the remaining 50 records. Pitch, Systematic and
  Wichita share S4 fetch-budget exhaustion, but independent S5 search produces
  zero valid candidates for all three. DSV, StatRad, Equifax and Aramark each
  have one deterministic recovery path, but the paths are distinct singletons.
  No additional product delta is counted.
- `.245` fixes the mixed-case Ashby runtime/checkpoint contradiction found in a
  separate non-sealed diagnostic cohort. Oso and Blossom are 2/2 S7 Exact and
  replay 2/2 with zero mismatch or fixture gap. Blossom's result restores an
  already accepted Frozen100 Exact; neither record belongs to this Fresh100
  ledger, so the aggregate remains 37 Exact, 12 Verified No Match,
  1 External Blocked and 50 unresolved.
- `.246` uses another zero-overlap development-only cohort to identify and fix
  a three-company LinkedIn-official homepage alias rejection contract.
  Yum! Brands, County of Maui and Duke University Health System recover 3/3
  correct Websites; Duke reaches an audited S7 Exact and focused replay has
  zero mismatch or fixture gap. None of these records belongs to Fresh100, so
  its aggregate remains unchanged.
- The v247 zero-overlap development cohort runs on unchanged `.246` code and
  reaches 27/30 Websites, 20/30 Careers, 15/30 verified Job Lists and 8/30
  Exact. All eight Exact URLs pass company, title, location, provider and
  tenant review. Causal audit finds no three-independent-company recovery path;
  the four replay mismatches split across one SpaceXAI same-company conflict,
  one Crete budget terminal and one Pigment inventory/location path. This
  evidence-only run does not change the Fresh100 aggregate.
- The v248 zero-overlap development cohort also runs on unchanged `.246` code:
  29/30 Websites, 20/30 Careers, 11/30 verified Job Lists and 7/30 Exact.
  Exact precision is 7/7 with zero URL, company, tenant or location error.
  Same-version replay is 29 reproduced, one Equip terminal-reason mismatch and
  zero fixture gaps. Its apparent groups split into official-host blocks,
  absent candidates and distinct S5/S6 parser or transport paths, so no
  implementation-qualified cluster exists.
- Numeric Workable embed evidence remains below the implementation threshold.
  American Battery and ClassWallet are two recovery cases; ESR, Symmetrio and
  iClassPro are positive controls already recovered by stronger direct
  evidence. No adapter or projection change is counted.
- The v249 zero-overlap development cohort runs on unchanged `.246` and reaches
  30/30 Websites, 22/30 Careers, 18/30 verified Job Lists and 6/30 Exact.
  Exact safety is 6/6 with zero URL, company, tenant, location or closed-opening
  error. Full replay exports 30/30 with 22 complete reproductions, five budget
  normalizations, three mismatches and zero fixture gap, tape divergence or
  missing snapshot boundary.
- v249 does not add a third recovery company to the Workable numeric-embed,
  strict-card-parser or same-origin-dynamic-GET hypotheses. Its other non-Exact
  records split across correct inventory no-matches, distinct provider/custom
  transports, relationship gaps and heterogeneous budget exits. No product or
  aggregate terminal change is counted.
- `.251` closes a separate three-company Hireology provider-family cluster:
  San Diego Padres, Mills Automotive Group and Tim Moran Hyundai reach 3/3
  verified Job Lists, 3/3 S7 Exact openings with exact locations and 3/3
  same-version replay. URL, company and tenant errors are zero. The work also
  fixes structural JSON snapshot redaction that previously corrupted replay.
  None of these development records belongs to Fresh100, so its projected
  aggregate remains unchanged.
- `.252` runs a new 30-company backend development cohort on unchanged `.251`:
  29 Websites, 17 Careers, 11 verified Job Lists and 6 audited S7 Exact
  openings with zero URL, company, tenant, location or captured-status error.
  The other 24 records contain no three-company implementation path. Full
  replay exports 30/30 with 20 reproduced, eight budget recoveries, two
  reason-code mismatches and zero fixture or snapshot-integrity gap. The cohort
  does not belong to Fresh100 and does not change its projection.
- `.253` closes the separate three-company Workable numeric-embed cluster.
  American Battery Technology Company, ClassWallet and Mention Me reach 3/3
  verified Job Lists, 3/3 S7 Exact and 3/3 replay through strict runtime widget
  identities. ESR, Symmetrio and iClassPro remain 3/3 Exact and 3/3 replay as
  stronger-route controls. All URL, company, tenant, title and location safety
  errors are zero. These records are outside Fresh100, so its projection is
  unchanged.
- v254 runs another zero-overlap 30-company backend development cohort on
  frozen `.253`: 29 Websites, 21 Careers, 17 verified Job Lists and 9 audited
  S7 Exact, with zero URL, company, tenant, title or location error. Replay is
  20 reproduced, seven budget recoveries, three mismatches and zero fixture
  gap. The cohort is outside Fresh100 and does not change its projection.
- `.255` closes the four-company generic opening failure-taxonomy mismatch
  shared by Barstool, Ichor, i-Pharm and Plaid. Focused live/replay reproduces
  4/4 terminals with zero mismatch and publishes no opening. This changes
  replay correctness only, not Fresh100 aggregate counts.
- v256 runs a ninth zero-overlap 30-company backend development cohort on
  frozen `.255`: 29 Websites, 22 Careers, 13 verified Job Lists and 5 audited
  S7 Exact openings with zero URL, company, tenant, title or location error.
  Replay is 25 reproduced, four budget normalizations, one status mismatch and
  zero fixture gap. A same-version 90-second diagnostic recovers 0/4 Exact
  from the apparent generic opening-budget cluster, so no implementation-
  qualified cluster or Fresh100 projection change is claimed.
- v257 runs a tenth zero-overlap 30-company backend development cohort on
  frozen `.255`: 27 Websites, 20 Careers, 15 verified Job Lists and 10 raw
  Exact openings. Audit accepts 9/10; CRG is an unsafe intermediary
  publication because an undisclosed client is the employer. Replay covers
  30/30 with zero comparator mismatch or fixture gap, while two location
  fields expose redaction mutation. The cohort does not change Fresh100
  projection. Gordian is the third public Eightfold PCS X family member and
  advances separately to `.258`.
- `.258` implements the shared Eightfold PCS X public-search contract without
  company branches. Gordian reaches one audited S7 Exact; Mayo reaches a
  complete title-filtered no-match. HP's native adapter also verifies its
  complete three-record no-match, but the aggregate record can still become
  `JOB_BOARD_PORTFOLIO_INCOMPLETE` when unrelated untrusted provider-search
  candidates enter the wider S5 portfolio. The provider family is supported;
  that scheduler completeness defect remains separately open. These
  development records do not alter Fresh100 projection.
- `.259` closes the seven-company replay location-redaction defect across
  BambooHR, Paylocity and Hireology. Newly captured live/replay pairs are 7/7
  Exact and match opening URL, title, location and location classification
  exactly. Query/OAuth `state` remains sensitive. This replay-correctness
  change does not alter Fresh100 projection.
- v260 runs an eleventh zero-overlap 30-company backend cohort on frozen
  `.259`: 29 Websites, 21 Careers, 15 verified Job Lists and 5 audited S7
  Exact openings with zero publication safety error. Replay is 30/30
  reproduced with zero mismatch or fixture gap. Four companies establish one
  Career transport-reservation cluster for `.261`; the cohort itself does not
  alter Fresh100 projection.
- Full Fresh100 cold start, original Frozen100 regression and sealed holdouts
  remain pending until multiple major clusters are completed.
