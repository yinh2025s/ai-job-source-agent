# v244 Page-Evidenced Provider Career - Phase A

## Trigger

S4 fetched `https://careers.hawaiianelectric.com` from the verified
`hawaiianelectric.com` site family. The SuccessFactors adapter independently
identified a custom-domain board and tenant `hawaiianel`, but the generic S4
Career classifier did not consult the Provider Registry. The valid page was
therefore discarded before S5.

This is a contract-boundary defect, not a company-name heuristic.

## Contract

S4 may recognize a fetched page as a Career surface from provider page evidence
only when all of the following hold:

1. candidate URL and candidate source URL are HTTPS and belong to the same
   registrable site;
2. the Provider Registry identifies exactly one board from the fetched page;
3. the adapter supports listing inventory;
4. the board has a non-empty tenant identifier;
5. the board URL remains in the candidate's registrable site;
6. the existing production-provider policy accepts the board.

Recognition establishes only a Career input for S5. It does not publish a Job
List, establish a hiring relationship, select an opening or bypass S5-S7.

## Acceptance

1. A same-site custom SuccessFactors page with one strict `j2w` tenant identity
   is accepted as a Career surface.
2. Cross-site source/candidate pairs remain rejected.
3. Missing, ambiguous or malformed tenant evidence remains rejected.
4. Non-production provider boards remain rejected by the existing policy.
5. Existing S4, SuccessFactors, S5-S7 and replay-focused tests pass.
6. A focused Hawaiian Electric run must either advance through the verified
   SuccessFactors board or report a downstream typed provider/inventory
   terminal; it must not publish an unverified URL.

## Out Of Scope

This phase does not change search ranking, S4 transport budget, company identity
normalization, External Apply, the extension, coordinator-v2 defaults, LLM
behavior or sealed blind cohorts.
