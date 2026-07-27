# v263 Backend Diagnostic Cohort - Phase C

## Frozen Result

Artifacts:

- `/private/tmp/v263-diagnostic-run1`;
- frozen input SHA-256:
  `17825f2cd49df13cbb240bc679c931059b7e6aad173df4ad336af4d88f56d23f`.

Frozen `.261` completed all 30 serial live records:

| Metric | Result |
| --- | ---: |
| Website | 30/30 |
| Career | 25/30 |
| verified Job List | 18/30 |
| S7 Exact | 10/30 |

The same-version full replay exported and reproduced 30/30 records with zero
mismatch, fixture gap, budget recovery, extra request or unconsumed tape.

## Exact Safety Audit

All ten openings pass company, hiring entity, provider/tenant, title, location,
current captured inventory and canonical opening URL checks:

- American Honda;
- Bose;
- Brex;
- Child Mind Institute;
- Fuse;
- Lyft;
- Meta;
- MrBeast;
- Snap;
- Yamaha Motor Corporation, USA.

Wrong URL, cross-company, cross-tenant, wrong-location and title mismatch
publication are zero. MrBeast again has a safe verified Greenhouse opening but
null top-level Career and Job List fields. Repetition by the same company does
not satisfy the independent-company implementation gate.

## Causal Classification

Terminal stage labels split into distinct causes:

- Ascendion, Madison-Davis, Ring and Valore have different Job Board handoff
  or business-process shapes.
- Bacardi, Hoxton Circle, Peachtree Immediate Care, The IMA Group and
  firstPRO 360 use different inventory protocols.
- CELSIUS exposes a Workable numeric inventory completeness failure by itself.
- LinkedIn and Tremendous share missing provider relationship proof, but only
  two companies.
- Teak Isle is a singleton metro-area versus city location ontology case.
- DataAnnotation, Piping Rock, Garage Beer and Onyx have different candidate,
  rate-limit, access-denial and same-name tenant causes.
- EVONA, Onyx and United Pharma share complete verified inventory with no title
  match, but expected recovery is zero; this is correct fail-closed behavior.
- Future Beauty Brands explicitly reports no public openings.

Peachtree's captured third inventory page contains the exact Warner Robins
opening, but the generic inventory parser emits zero candidates. This matches
the historical ProKids visible-card parser symptom. They are only two
independent companies; no broad class-name heuristic is implemented.

No cluster satisfies all four requirements: three independent companies,
one observable trigger, one production code path and expected recovery of at
least three records. `.263` therefore remains an evidence run with no product
code change.
