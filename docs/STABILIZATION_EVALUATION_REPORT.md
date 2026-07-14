# Correctness Stabilization Evaluation

## Scope And Provenance

This report closes the correctness-first stabilization round. It evaluates the
single 30-company `frozen_observed` live capture from 2026-07-15; the cohort was
already visible to development and is not a blind or unfamiliar holdout. No
live request was rerun to improve these numbers.

The annotation manifest is
`samples/evaluation/stabilize_v90_frozen_observed_annotations.json`. The merge
tool refuses source drift by checking these frozen SHA-256 digests before it
accepts any annotation:

| Artifact | SHA-256 |
| --- | --- |
| results | `88a6c1f94c75ac4d9e3133cfc7944f6e0b989e00033330536436d16e557abf2a` |
| trace | `ae4b42f2b778dc27e82ae68dfa6b623d645da772648d3cab8d9cd321452a4cfa` |
| summary | `1c0a711d8133cd9ddac96ac802799aaa5df0428783f4b7051ca27c618e64337a` |

The review was performed outside the runtime identity gate against captured
official provider/first-party evidence. It is independent of the runtime
`verified` verdict, but it is still a Codex artifact review rather than a new
human-labelled blind benchmark.

## Trustworthy Metrics

| Metric | Result | Interpretation |
| --- | ---: | --- |
| Annotation coverage | 30/30 (100.0%) | Every frozen record has one disposition and eligibility label |
| Raw exact rate | 19/30 (63.3%) | Product output rate across all records |
| Exact precision | 19/19 (100.0%) | All emitted exact URLs passed the independent artifact review |
| Conditional exact recall | 19/24 (79.2%) | Recall among records confirmed to have an eligible public opening |
| Eligibility unknown | 2 | Deloitte and Akkodis were blocked by retryable transport failures |
| System defect rate | 7/30 (23.3%) | Five identity-evidence false negatives plus two retryable failures |

The 100% precision is evidence for this observed cohort only. It is not a
product-wide precision claim and does not replace a newly frozen blind holdout.

## Record Dispositions

| Disposition | Count | Records |
| --- | ---: | --- |
| `exact_public` | 19 | ModMed, Eightpoint, Stage 2 Capital, VELOX, Seez, Edra, Distyl, Taxbit, Suffolk Construction, Mighty, Nevis, Zello, VOLO Health, Direct Supply, General Motors, Divergent, Kobie, Smart Bricks, PermitFlow |
| `verified_closed` | 3 | Viking, GPTZero, Percepta |
| `recruiter_client_undisclosed` | 1 | Aventis Solutions |
| `system_gap` | 7 | Dematic, Quest Global, ReturnPro, Deloitte, Akkodis, Awesome Motive, Adobe |
| `external_blocked` | 0 | - |
| `no_public_opening` | 0 | - |

## Remaining Failure Clusters

Five eligible openings were correctly rejected by the fail-closed runtime gate
because the official career-to-provider relationship evidence was not promoted
into the typed identity chain: Dematic, Quest Global, ReturnPro, Awesome Motive,
and Adobe. These are false negatives, not permission to add company exceptions.

Deloitte and Akkodis retain typed retryable network failures. Their capture-time
opening eligibility is explicitly unknown; neither is converted to a closed or
not-found result. Viking, GPTZero, and Percepta had complete official inventory
without the target opening, while Aventis did not disclose a verifiable hiring
client.

## Regression Evidence

The final scoped bundle-v7 replay reproduces 30/30 outcomes with record
integrity passed, zero fixture gaps, and zero mismatches. During stabilization,
that gate caught an over-strict canonical-board comparison that rejected valid
Greenhouse, Lever, iCIMS, and Whitecarrot detail mappings. The final rule
requires selected-candidate evidence bound to the typed S5 board handoff when
an opening adapter emits a different locator, while an untraced same-tenant
board substitution remains rejected.

## Next Candidates

1. Freeze a genuinely unfamiliar holdout before further tuning, so precision and recall generalization can be measured without observed-cohort bias.
2. Define a provider-neutral contract that promotes first-party career handoff evidence into the hiring-entity/provider relationship chain, with negative fixtures before implementation.
3. Isolate transport reliability for the two retryable failures, preserving eligibility as unknown until complete official evidence exists.
