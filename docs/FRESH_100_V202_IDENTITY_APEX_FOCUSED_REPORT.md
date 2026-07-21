# Fresh100 `.202` Identity-Apex Focused Gate

Run date: 2026-07-21

## Contract

The Phase A contract allowed one larger bounded request window for the first
exact core-name `.com` apex already selected by the S2 allocator. It changed no
candidate score, authority, retry count or homepage acceptance gate. Phase C
required recovery of at least three independent companies; otherwise the
cluster definition and behavior change had to be rejected.

## Isolation

- Runtime commit: `ace84bd5c36f46ac80240685fb7e43eb5c1a05d5`
- Adapter version: `2026-07-21.202`
- Run root: `/private/tmp/fresh100-v202-identity-apex-20260721-run1`
- Input: six frozen Fresh100 records from Matlen Silver, Sentar, HP, American
  Fabrication and Arkema.
- Cold start: six pending, zero restored.
- Blind holdouts v2 and v3 were neither opened nor executed.

## Results

| Outcome | Records | Independent companies |
| --- | ---: | ---: |
| Verified website/Career/Job List | 2 | 1 |
| Exact opening | 0 | 0 |
| S2 network timeout | 4 | 4 |
| S7 identity rejection | 2 | 1 |

Matlen Silver, Sentar, HP and American Fabrication remained
`NETWORK_TIMEOUT`. Both Arkema postings reached the official website, Career
surface and Job List, but S7 rejected one for `OPENING_TITLE_MISMATCH` and the
other for `OPENING_LOCATION_UNVERIFIED`. No wrong opening URL was published.

Full scoped replay passed 6/6 with six reproduced outcomes, zero mismatch and
zero fixture gap. This proves deterministic reproduction of the result; it does
not turn one recovered company into a batch-level success.

## Decision

Only one independent company passed S2 in this run, below the required minimum
of three. No same-network parent-version A/B was run, so this report does not
attribute even that one transition exclusively to the experiment.
The proposed cluster was therefore too broad and the `.202` request-allocation
behavior is rejected. `.203` removes the behavior and its policy-specific unit
tests while preserving this report, the Phase A analysis, the `.202` commit and
all live/replay artifacts as negative evidence.

## Artifact

- Read-only focused archive:
  `artifacts/releases/fresh100-v202-identity-apex-20260721-run1.tar.zst`
- Archive SHA-256:
  `31ef2b6bec4c92ac365dfaee57e27e28e1961cc3d90308844365e47fa48ff662`
