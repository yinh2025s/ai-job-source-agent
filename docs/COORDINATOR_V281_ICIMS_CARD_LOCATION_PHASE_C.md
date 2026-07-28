# `.281` iCIMS Job-Card Location Phase C

## Outcome

The proposed `.281` behavior change is **not released**.

The hosted-iCIMS parser change correctly bound each opening link to the
location displayed in the same `iCIMS_JobCardItem`. Provider tests passed, and
the three captured development snapshots produced the expected title,
location and canonical opening URL. The code-frozen focused live gate,
however, recovered only two terminal Exact outcomes:

| Company | Live result | Explanation |
| --- | --- | --- |
| Elderwood | Exact | Opening `36227`; `US-NY-Ticonderoga` |
| Great Day Improvements | Exact | Opening `24730`; `US-GA-Savannah` |
| Steampunk | Partial | Correct opening `6317` was parsed, but the existing ambiguity gate also saw three same-location `UX/UI Designer` openings |

The run completed 3/3 records with 3 Websites, 3 Career pages, 3 verified Job
Lists and 2 S7 Exact openings. The isolated automatic strict replay reproduced
all three outcomes:

- reproduced: 3;
- mismatch: 0;
- fixture gap: 0;
- budget recovery: 0;
- replayability drop: 0.

Artifacts remain isolated at:

`/private/tmp/v281-icims-card-location-focused-live-20260728-run1`

## Causal Reclassification

Steampunk is not another failure of card-local location extraction. The live
trace contains the correct candidate:

```text
https://careers-steampunk.icims.com/jobs/6317/ui-ux-designer/job
title: UI/UX Designer
location: US-VA-McLean
```

It is rejected later by
`opening_identity_ambiguity=multiple_same_location_title_candidates`, because
the same official inventory also contains three `UX/UI Designer` openings in
McLean. Changing that safety gate is a separate matcher/identity contract and
cannot be bundled into the provider parser fix.

A read-only scan of the existing unsealed development captures found no third
independent company with the same parser trigger and a demonstrated terminal
recovery. Therefore the implementation produced only two independent Exact
recoveries and fails the repository requirement of at least three.

## Decision

- Revert the uncommitted provider and test implementation.
- Restore the adapter version to `2026-07-28.280`.
- Do not weaken the ambiguity, title, location, company or tenant gates.
- Preserve this report and the Phase A document as evidence that the original
  cluster definition overestimated terminal recovery.
- Do not rerun Fresh100, Frozen100 or a sealed cohort for a rejected change.

The current product behavior and all published `.278` and `.280` measurements
remain unchanged.
