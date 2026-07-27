# v272 Jobvite Qualification - Phase C

## Decision

Do not implement a Jobvite adapter yet.

Historical development evidence suggested three companies, but a fresh focused
baseline did not confirm three current, identity-safe Jobvite relationships.

## Frozen Input

`/private/tmp/v272-jobvite-focused-input.json`

SHA-256:

`18b45602dd14c4a88a351ceceb5920013a63b0c2ae3897df90f0138083d83119`

The records are the original public LinkedIn inputs for LHH, The IMA Group and
Samtec. No provider URL was injected into the inputs.

## Baseline

Artifacts:

`/private/tmp/v272-jobvite-baseline-run1`

- The IMA Group: verified first-party Jobvite board, inventory incomplete;
- Samtec: official Website resolved, but the Jobvite Career handoff did not
  reproduce in this network run;
- LHH: an unrelated SmartRecruiters `Axiado` tenant was discovered through
  search and correctly rejected by S7 as
  `PROVIDER_RELATIONSHIP_UNVERIFIED`.

Replay reproduced 3/3 with zero mismatch or fixture gap.

## Qualification Result

The IMA Group and the earlier Samtec evidence are two credible Jobvite
examples. LHH does not currently prove the same provider relationship, and the
historical search candidate is insufficient on its own.

The required three-company threshold is not met. Implementing from these
records would either use stale evidence or weaken provider relationship
validation, so the provider remains a read-only candidate family.

The next step is a new public S1-only development pool and another mechanical
diagnostic cohort. Plugin work, authenticated External Apply, coordinator-v2,
LLM and sealed holdouts remain frozen.
