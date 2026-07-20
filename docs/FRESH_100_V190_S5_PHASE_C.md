# Fresh 100 `.190` S4/S5 Focused Phase C

## Frozen Run

- Code commit: `a8b00c21429f272817cbd04ab73b6ea0dd861c87`
- Adapter version: `2026-07-20.190`
- Source cohort: July 18 fresh 100, ordinals 13, 26, 41, 54, 73, 76, 78, 90
- Focused input: 8 postings, including both B&D titles
- Cold-start policy: no resume and new checkpoint, completion, evidence, snapshot,
  replay, and output roots
- Parent cohort SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Focused input SHA-256: `16ea42276a43bb79e7e70b33161926586799c45e21f0c6362ebb13e7e92db13d`

The code remained frozen for the full live and automatic full-outcome replay.
This focused result does not replace `.188` fresh 11/100 or frozen-100 69/100.

## Result

The live run completed 8/8 in 77.1 seconds: 4 verified websites, 3 Career
surfaces, 1 verified Job List, and 0 Exact openings. Same-version replay passed
8/8 with 0 mismatch, 0 fixture gap, and full record integrity.

The audited focused ledger is:

| Terminal | Count | Records |
| --- | ---: | --- |
| EXACT | 0 | none |
| VERIFIED_NOT_FOUND | 1 | IGNITE |
| EXTERNAL_BLOCKED | 0 | none |
| INPUT_IDENTITY_INVALID | 1 | NextPlay Jobs |
| SYSTEM_GAP | 6 | Splashlight, CHAMP, Milwaukee Tool, B&D x2, Northern Clearing |

There was no published opening URL, so wrong-opening, cross-company, and
cross-tenant false positives are all zero. IGNITE's internal candidate URL was
not published because S7 rejected its title identity.

## Failure Clusters

### S2 transient transport boundary (4 records)

Splashlight, both B&D records, and Northern Clearing failed website resolution
after primary-site timeout/TLS failures and LinkedIn `451/999` responses. All
four reached S4/S5 in `.188`, so a single retryable run cannot establish an
external terminal. They remain `SYSTEM_GAP` until the general S2 recovery path
preserves usable direct evidence or a later code-frozen run revalidates them.

### Freshteam query normalization (CHAMP)

The first-party Career page declared the expected Freshteam widget and the
captured script declared tenant `ownum`. URL normalization represented the
bare cache-buster query as `?1612292094=`, while the adapter accepted only
`?1612292094`. The script was fetched but the tenant inventory was never
probed. The fix must accept only these equivalent bounded cache-buster forms;
arbitrary query names or values remain rejected.

### External first-party Career handoff (Milwaukee Tool)

The verified homepage visibly links `COMPANY + CAREERS` and `Careers` to
`https://www.milwaukeetool.jobs/`. `.190` neither retained nor traversed that
authoritative handoff, then incorrectly emitted `NO_PUBLIC_OPENINGS`. The
negative press/project gate is correct; the missing piece is carrying an
explicit cross-site Career action from verified homepage evidence into S4 and
preventing a no-public terminal while that action remains unvisited.

### Strict title identity (IGNITE)

The official Career handoff, HRSmart provider, `ignitenow` tenant, complete
68-record inventory, opening 339, and Huntsville location all verify. The only
conflict is target `CYBER SECURITY ANALYST` versus official
`CYBER SECURITY ANALYST- MID`. Without posting-level handoff evidence, `.190`
correctly refuses Exact. The terminal should express complete-inventory strict
title absence rather than a generic relationship identity mismatch; no broad
seniority relaxation is approved.

### Undisclosed recruiting client (NextPlay Jobs)

NextPlay's public surface describes recruiting and staffing services and does
not identify the client employer for the Wichita Project Manager posting.
Company identity is valid, but posting-to-employer identity is not. This should
close as `INPUT_IDENTITY_INVALID` (or the equivalent recruiter-client terminal),
not remain an ordinary `JOB_BOARD_NOT_FOUND` and not trigger guessed tenants.

## Next Version

`.191` will address the two deterministic discovery defects first: Freshteam's
normalized cache-buster query and authoritative external Career handoff
propagation/no-public gating. It will also correct the verified-no-match and
undisclosed-intermediary terminal semantics without weakening S7. S2 transport
recovery remains a separate general cluster and cannot use company overrides.
