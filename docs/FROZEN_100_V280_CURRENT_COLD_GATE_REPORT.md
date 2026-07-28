# Frozen100 `.280` Current-Version Cold Regression Gate

## Decision

The code-frozen `.280` current-version Frozen100 live run completed 100/100,
but the regression gate **failed**:

- live produced 39 S7 Exact openings;
- all 39 Exact outputs passed the current URL and identity safety audit;
- only 38 of the historical 69 Exact records remained Exact;
- strict replay did not reproduce the full cohort;
- no remaining replay or discovery defect currently qualifies for Phase B
  under the three-company and three-expected-recovery rule.

The historical `.188` release remains immutable and authoritative for its
original 69/100 result. This run is a separate current-version measurement and
does not overwrite it.

## Frozen Input And Runtime

- Frozen commit:
  `73ce4ef68f3d336995abbef67f816164a8ef7a22`
- Product adapter: `2026-07-28.280`
- Input records: 100
- Unique LinkedIn URLs: 100
- Unique companies: 73
- Input SHA-256:
  `d88605c5caa720126e1574cb1fa97d72492307e43dc706f5c6fff778996502fa`
- Input source:
  `artifacts/releases/frozen100-v188-ed4c934/frozen100-v188-final-artifacts.tar.zst`
- Historical release checksums: verified before input extraction
- Resume: disabled
- Old completion, checkpoint, evidence, snapshot and Fresh100 state: not used
- Candidate engine: `stage_v1`
- Search backend: `legacy`
- Workers: four bounded company workers in one benchmark process
- Network configuration: the same bounded `.278` measurement configuration
- Sealed v2/v3, authenticated plugin and LLM branch: not accessed

The pre-run manifest, exact command and immutable local evidence are preserved
under:

`/private/tmp/frozen100-current-v280-cold-20260728-run1`

## Cold Live Result

| Metric | Result |
| --- | ---: |
| Records completed | 100/100 |
| Website | 96 |
| Career page | 89 |
| Job List | 76 |
| S7 Exact opening | 39 |
| Pipeline partial | 44 |
| Pipeline failed | 17 |

The run restored zero previous completions. It is therefore a real cold
current-version measurement, not a resumed or cache-reported score.

## Exact Safety Audit

All 39 published openings passed a serialized identity and URL audit:

- 39/39 public HTTPS canonical opening URLs;
- 39/39 verified result identity assertions;
- 39/39 verified hiring/provider relationships;
- 39/39 consistent provider, tenant, board and opening chains;
- 39/39 title and location evidence present;
- zero wrong-location classifications;
- zero cross-company or cross-tenant publications;
- zero output/canonical-opening disagreements.

A credential-shape scan over the live results, trace, summary, checkpoint,
completion, evidence, snapshots, replay and failure paths found zero Google
browser-key, AWS access-key-ID or JWT-shaped values. This statement applies to
this `.280` Frozen100 capsule only.

## Historical 69-Exact Comparison

The current run retained 38 of the historical 69 Exact records and gained one
new Exact record, Caudalie Account Executive:

| Comparison | Records |
| --- | ---: |
| Historical Exact | 69 |
| Current Exact | 39 |
| Historical Exact retained | 38 |
| Historical Exact lost | 31 |
| Current Exact newly gained | 1 |

Of the 38 retained records, 36 preserve the same opening URL. OrganOx and Gucci
only differ by canonical trailing-slash normalization.

Sixteen of the 31 lost records reached a complete or title-filtered official
inventory that no longer contained the historical target: Acorns, Google,
Hadrian, two Instagram Product Manager records, two Meta Product Design
Engineer records, eight Meta Product Manager/Leadership records, Sezzle. This
is compatible with job closure or inventory removal, but is not labelled
`VERIFIED_CLOSED` without explicit status evidence.

The remaining 15 historical Exact records are unresolved current-version
no-regression debt:

- discovery incomplete: Bacardi, Elderwood, Randstad and Saint Laurent;
- no complete current availability diagnostic: Actabl, Garan, two LinkedIn
  records, Michael Kors, NexCare, SKIMS, Solomon Page, SpaceX, Tenet and
  adidas.

Consequently, the current version does not yet prove the required 69-Exact
Frozen100 no-regression condition.

## Strict Replay Result

The full strict replay aborted on a tape divergence instead of producing an
accepted 100-record gate. A diagnostic replay of the other 98 records then
completed:

| Replay classification | Records |
| --- | ---: |
| Reproduced | 95 |
| Outcome mismatch | 3 |
| Hard tape divergence | 2 |
| Fixture gap | 0 |
| Budget recovery | 0 |

This is a failed strict replay gate. The 98-record diagnostic result cannot be
reported as a successful full-cohort replay.

### Hard divergences

1. Sony Interactive Entertainment: live S5 used a same-record
   `stored_verified_provider_board` side effect before S6 read the Greenhouse
   inventory. Scoped replay reconstruction did not reproduce that state, so
   the recorded Greenhouse API request remained unconsumed.
2. Stark Pharma: live website resolution failed, but an independent downstream
   candidate route later recovered an Exact opening. Replay stopped before
   reproducing the downstream route and left four requests unconsumed.

Current executable checks do not reproduce either trigger across three
independent companies.

### Outcome mismatches

1. Two duplicate Redlands Community Hospital records changed from
   `OPENING_NOT_FOUND` to `JOB_BOARD_PORTFOLIO_INCOMPLETE`.
2. BBVA retained the same Exact opening and identity chain, but provider
   `evidence_url` provenance changed from the board URL to the Career page.

The Redlands records represent one company, and the BBVA provenance drift uses
a different path. They do not form a qualified common defect.

## Phase Decision

No implementation follows this measurement:

- Sony and Stark are separate singleton replay roots;
- Redlands affects two records but only one company;
- BBVA is an evidence-provenance singleton;
- the 15 historical Exact losses have not yet been shown to share one trigger,
  one production code path and at least three expected terminal recoveries.

The next legal step is additional non-sealed diagnostic evidence collection or
read-only causal analysis. A Phase B change may start only after a root cause
meets the repository's cross-company threshold. Another full Fresh100 or
Frozen100 run should not be started merely to accumulate attempts.

