# v262 Development Diagnostic Cohort - Phase C

## Frozen Live Result

Artifact:

- `/private/tmp/v262-diagnostic-run1`

Frozen input:

- `/private/tmp/v262-diagnostic-input.json`
- SHA-256:
  `7bdb90180c7dc95616d7f60fd523fbf03cd67503b5fa6c3de2b0d30ee7c3e45a`

Frozen `.261` produced:

| Metric | Result |
| --- | ---: |
| Website | 27/30 |
| Career | 23/30 |
| verified Job List | 18/30 |
| S7 Exact | 4/30 |

No product code changed during live execution.

## Exact Safety Audit

All four published openings pass company, hiring entity, provider/tenant,
title, location, canonical opening URL and current inventory continuity:

| Company | Provider | Opening |
| --- | --- | --- |
| MrBeast | Greenhouse `mrbeastyoutube` | Senior Recruiter; New York, NY |
| Meta | Meta Careers `meta` | Mechanical Design Engineer, Robotics; Redmond, WA |
| Helion | Ashby `helion` | Mechanical Engineer, Early Career; Everett, WA |
| Yamaha Motor Corporation, USA | verified first-party generic inventory | Mechanical Engineer II; Kennesaw, GA |

Wrong URL, cross-company, cross-tenant and wrong-location publication are zero.
MrBeast again exposes a safe Exact with null top-level Career and Job List. It
remains one-company projection evidence and is not implemented as a benchmark
special case.

## Evidence Terminals

- TAR and Vertisystem have complete verified inventories with no target match.
- Inversion has verified no-public-opening evidence.
- Garage Beer preserves an official-host access denial.

These are evidence outcomes, not recall successes.

## Replay Gate

Automatic replay failed closed before the full 30-record bundle was built:

```text
Vertisystem
outcome tape has 4 unconsumed entries
first remaining request: GET https://vertisystem.com/careers/
```

Live S6 fetched the Career landing, three declared JavaScript assets and then
posted to WP Job Manager. Replay hydrated a runtime-only
`replay_safe=False` typed board and skipped the four discovery requests before
consuming the POST. This is a request-plan determinism defect. The live result
is retained, but replay acceptance is failed until the same-version snapshots
consume with zero divergence.

## Failure Clusters

Stage labels do not form implementation clusters:

- Airbnb official generic inventory is incomplete at its page cap.
- UNIQLO has three complete official Workday inventories, but multi-board set
  completeness proof is too strict.
- HP remains the only confirmed complete official singleton no-match polluted
  by unauthorized search candidates. These three portfolio terminals are not
  one cluster.
- Bullhorn OSCP occurs for StaffBright and Crawford Thomas only.
- Visible-card parser misses occur for ProKids and Peachtree Immediate Care
  only.
- Credo AI and Peachtree share a two-record fetch-failure projection symptom.
- ZSG WP Job Manager, Emonics Simple Job Board, The Hiring Advisors Loxo,
  Quest Financial HTTP downgrade and American Honda Phenom ambiguity are
  separate protocol or identity paths.

No recall cluster reaches three independent companies. The only current hard
gate is runtime-only board replay determinism; historical evidence must reach
the same three-company threshold before implementation.

## Historical Replay Contract Audit

The strict request-plan divergence is confirmed for only two independent
companies:

- Cass County Government;
- Vertisystem.

Both are WP Job Manager pages where live executes the Career landing and
declared asset requests before inventory, while scoped replay hydrates a
runtime-only board and skips those producer requests.

NevadaNano and D&B Engineers and Architects are historical WP Job Manager
controls, not a third failure. Their complete recorded replays classify the
same records as `reproduced` without tape divergence. Three Workable numeric
widget controls also replay successfully, so they cannot count toward an
expected-recovery threshold.

The observed defect therefore remains at two companies. It is recorded as an
open replay risk, but no implementation is permitted under the three-company,
same-trigger, same-code-path and expected-recovery-at-least-three gate.
