# v260 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts:
`/private/tmp/v260-diagnostic-run1`

- input SHA-256:
  `f12220acbc6fa6eaa5f10b02c4f0b352b88b64e9eabd362b6454526b3165f331`;
- product version: `2026-07-27.259`;
- records and independent companies: 30/30;
- known public development IDs excluded: 581;
- Website: 29/30;
- Career: 21/30;
- verified Job List: 15/30;
- raw and audited-safe S7 Exact: 5/30;
- elapsed: 1268.2 seconds.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remained frozen.

## Exact Safety Audit

All five published openings pass company, hiring entity, provider, tenant,
title, location, captured open-state and canonical URL review:

- Flix;
- MrBeast;
- JST;
- Boulder Care;
- West Coast Wound & Skin Care.

Wrong URL, company, tenant, title, location and closed publication are zero.

MrBeast is a safe Exact but exposes a terminal-projection singleton. Its
Greenhouse tenant `mrbeastyoutube`, complete inventory and opening ID
`6118229004` are verified, while top-level `status`, Career and Job List fields
remain partial/null. This does not invalidate the opening but must not be
silently described as a fully coherent product result.

## Replay

All 30 records were exported and replayed:

| Classification | Records |
| --- | ---: |
| reproduced | 30 |
| expected transition | 0 |
| budget recovery | 0 |
| mismatch | 0 |
| fixture gap | 0 |

Record integrity and outcome gates pass. Live/replay opening URLs and identity
chains match.

## Causal Audit

One root cause qualifies for implementation:

| Cluster | Companies | Shared trigger | Expected recovery |
| --- | ---: | --- | ---: |
| Career transport starvation | 4 | blind ATS probes consume 11-14 of 24 dispatches before stronger official candidates | 4 terminal recoveries |

Affected companies:

- The Naked Market;
- Motorola Solutions;
- Daedalus;
- DataAnnotation.

The shared production path is the Career transport dispatch budget and blind
ATS/path scheduler. `.261` may reserve dispatches for high-evidence official
navigation and path candidates, but may not relax verification or claim Exact
without S7.

No other group qualifies:

- CELSIUS and Keurig Dr Pepper have different providers and malformed-data
  paths;
- eight opening-incomplete records use distinct iCIMS, Angular, WordPress,
  AEM, Odoo, Phenom and form protocols;
- Step does not extend HP's portfolio cluster because Step lacks an official
  complete no-match;
- three deterministic official-host denials have no independent ATS
  relationship and correctly remain blocked;
- remaining provider, board, relationship and projection issues are
  singletons.

## Decision

v260 is evidence-only and does not alter Fresh100 projection. Advance only the
four-company Career transport-reservation cluster to `.261`.
