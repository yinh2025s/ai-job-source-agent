# Beta Demo Evidence

## Purpose And Boundary

`samples/beta_demo_input.json` is a seven-record public demonstration set. It
shows current product behavior, including conservative refusal, but it is not a
benchmark, holdout, accuracy sample, or claim about generalization.

The inputs contain only public LinkedIn job/company URLs, company names, titles,
locations, and source labels. They contain no expected answers, discovery URLs,
cookies, tokens, authenticated HTML, or historical snapshots.

## Current Focused Live Acceptance

The frozen `.286` product code ran these seven records once on 2026-07-31 with:

- CPython 3.12.6;
- a fresh isolated checkpoint, completion, evidence, snapshot, and output root;
- no resume from Frozen100 or Fresh100 artifacts;
- two company workers and a 120-second per-company bound;
- the accepted `stage_v1` candidate scheduler;
- no code changes during the run.

The run completed 7/7 records in 141.7 seconds:

| Company | Current terminal | Current evidence |
| --- | --- | --- |
| Notion | S7 Exact | Ashby tenant `notion`, opening `297b4ece-765f-4eea-b1b8-46057cb6501f` |
| Sony Interactive Entertainment | S7 Exact | Greenhouse tenant `sonyinteractiveentertainmentglobal`, opening `5840958004`; compatibility `status` is partial because no separate Job List URL was serialized |
| BBVA | S7 Exact | Workday tenant `bbva/BBVA`, opening `JR00107726`, Houston |
| Leadenhall Search & Selection | Verified no-match / partial | Official Loxo board was read; no matching opening was published |
| Aveanna Healthcare | External inventory unavailable / partial | Official Career and Job List were verified; the current terminal is `HTTP_NOT_FOUND`, not a claimed verified absence |
| Panacea Health Corp | Discovery failed | `CAREER_PAGE_NOT_FOUND`; no Job List or opening was published |
| Riverview School | Discovery failed | `WEBSITE_NOT_RESOLVED`; no Job List or opening was published |

The concise totals are 3 S7 Exact, 1 verified no-match, 1 external inventory
failure, and 2 discovery failures. These are demonstration outcomes, not a
success-rate estimate.

### Exact Safety Audit

The measurement-bound identity audit passed 3/3 Exact records with zero issues:

- Notion: company, title, San Francisco location, Ashby provider/tenant, and
  canonical opening passed.
- Sony Interactive Entertainment: company, title, Los Angeles area, Greenhouse
  provider/tenant, and canonical opening passed.
- BBVA: company, title, Houston location, Workday provider/tenant, and canonical
  opening passed.

No other record published an `open_position_url`. The run artifact privacy scan
covered 257 files / 26,230,716 bytes and found zero credential-shaped values.

### Replay Limitation

This focused set is not represented as a clean replay gate. The automatic full
bundle exported 6/7 records and correctly failed record-integrity enforcement;
an Exact-only attempt then rejected an unconsumed Sony transport outcome. The
live identity audit remains valid, but the set must not be described as 7/7
deterministic replay evidence. The stable presentation path is the checked-in
offline demo, not a live rerun.

### Artifact Binding

The raw live directory is local and excluded from the source package. Its
review hashes are:

| Artifact | SHA-256 |
| --- | --- |
| `results.json` | `d61ababefdb0526b717bc122ac9294fad04eaf75e6d378b49a101ab43272b48a` |
| `trace.json` | `03e11acea0e55dec5240cbc60117beeb1e129b73bc2cfeba5a036cd444d41d95` |
| `summary.json` | `0c9d430fe4915db8ec460919b58fc8cd83e52b406aecc8c04ce68c46baba853b` |
| Exact identity audit | `bb04283a93fcabc805c393a84fa8692a0c4e24ac245baf8249e3a6c23eab6341` |

## Historical Selection Provenance

The records were selected from public inputs in the historical Frozen100
release so that several provider families and failure boundaries were
represented. Historical results were used only to choose inputs; they were not
restored as current answers.

- Source release: `frozen100-v188-ed4c934`
- Source commit/tag: `ed4c9343ec382387542d7b917050acbc04096dda` /
  `frozen100-v188`
- Historical adapter: `2026-07-20.188`
- Release SHA-256:
  `df4fdf8585ce70994ae0c7ab61e585913d2e4b90872fa4b98b07d2e43cf063fe`
- Historical release result: 69 Exact, 23 Verified Not Found, 5 External
  Blocked, 3 Input Identity Invalid, and 0 System Gaps; replay 100/100.

That historical result is a developed baseline, not evidence for `.286` or for
unseen-company generalization.

## Demo Guidance

Use `make beta-demo` for the stable 3-5 minute presentation. Use this document
to show that the same backend was also exercised against current public inputs.
Do not rerun the seven live records during the presentation, describe 3/7 as a
general success rate, or claim that the focused set passed strict replay.
