# Fresh 100 `.189` Causal Reclassification

## Decision

`.189` did not close an S2 failure cluster. It stabilized typed transport,
retry, worker completion, and snapshot/replay behavior, but recovered only two
additional websites (`3/49 -> 5/49`). The remaining 44 records therefore must
not be grouped by the stage at which execution stopped.

This audit uses the immutable run7 archive, the captured request tape, and the
previously reviewed July 18 website/Career/Job List evidence. The five resolved
records are excluded. Each unresolved record has exactly one primary causal
class. Provider and External Apply bypasses are recorded only as orthogonal
recovery labels because they are not causes of website-resolution failure.

## Classification Rules

| Code | Primary causal class | Required evidence |
| --- | --- | --- |
| T | Correct candidate existed, transport failed | Correct host was generated and requested; its request ended in timeout, TLS, or HTTP denial. |
| B | Verification budget starvation | A source-backed correct host was in the candidate pool but received no verification request because bounded slots were spent elsewhere. A mechanically guessed URL without independent source evidence does not qualify. |
| S | LinkedIn/search source rejected | The available source resolved to an ambiguous or different entity and the ownership gate rejected it; no stronger source survived. |
| G | Correct candidate was never generated | Reviewed official host was absent from the candidate pool and request tape. |
| I | Candidate identity verification rejected | Correct host was fetched successfully but current identity/relationship rules refused selection. |
The provider/External Apply bypass label is never a primary causal class. It is
an execution overlay in the bypass matrix below. Recurring LinkedIn `451/999` responses are
not a cluster by themselves: `_strongest_retained_fetch_failure()` often emits
them after a more specific candidate-generation, allocation, identity, or
homepage-transport failure.

## Record Matrix

| Record | Company | Class | Executable cause | Correct candidate or bypass evidence |
| ---: | --- | :---: | --- | --- |
| 000 | Loveland Innovations | T | Correct apex request timed out. | `lovelandinnovations.com`; Paylocity exact known. |
| 001 | iClassPro | G | Display/slug generation retained descriptive or legal tokens. | Missing `iclasspro.com`; Paylocity exact known. |
| 002 | Indica Labs | G | Generator emitted plural `indicalabs`, not singular `indicalab`. | Missing `indicalab.com`; BambooHR exact known. |
| 003 | Caesars Entertainment | G | Generator retained `entertainment`; core-brand host absent. | Missing `caesars.com`. |
| 005 | Versana, DevOps | G | No independent source produced `versana.io`; it existed only as a mechanical TLD guess, while `versanatech.*` slug candidates consumed the bounded slots. | Missing source-backed `versana.io`. |
| 006 | Altec | T | Correct apex and `www` fallback failed TLS certificate verification. | `altec.com`. |
| 008 | NYC DSS | G | Municipal agency path/domain was not produced from the display name. | Missing `nyc.gov/site/dss`; CityJobs exact known. |
| 009 | City of Lubbock | I | Reviewed `mylubbock.us` redirect target was fetched and verified, then rejected as parent/group evidence. | `mylubbock.us`; GovernmentJobs board known. |
| 010 | Sunwest Bank | T | Correct homepage returned HTTP 403. | `sunwestbank.com`. |
| 011 | City of Pharr | G | `.gov` municipal host was outside generated candidates. | Missing `pharr-tx.gov`. |
| 012 | Stuller | T | Correct apex request timed out. | `stuller.com`. |
| 013 | SDS International | G | Current `sdslink.com` alias was absent; slug yielded legacy `atdlink.com`. | Missing `sdslink.com`. |
| 015 | Hawaiian Electric, Cyber Ops | T | Correct homepage returned HTTP 403. | `hawaiianelectric.com`. |
| 016 | Mayo Clinic | T | Search produced the correct host, whose verification returned HTTP 403. | `mayoclinic.org`. |
| 017 | Benefis Health System | G | Generator retained `health-system`; shortened brand host absent. | Missing `benefis.org`. |
| 018 | Diamondback Energy | T | Correct homepage failed with TLS EOF. | `diamondbackenergy.com`. |
| 019 | Cretex Companies | G | Legal/display suffixes remained; shortened host was not produced. | Probable `cretex.com`; ground truth needs confirmation. |
| 020 | Hawaiian Electric, Information Assurance | T | Correct homepage returned HTTP 403. | `hawaiianelectric.com`. |
| 021 | Fotomill Studios | G | Generator retained `limited`; official host drops it. | Missing `fotomillstudios.com`. |
| 022 | PACS | I | `pacs.com` was fetched, but holding-company/parent identity rules rejected it. | `pacs.com`; Workday exact known. |
| 023 | North Dakota IT | G | Government subdomain `ndit.nd.gov` was not generated. | Missing `ndit.nd.gov`. |
| 024 | University of Oklahoma | G | Institutional acronym/domain was not generated; no LinkedIn company source existed. | Probable `ou.edu`; ground truth needs confirmation. |
| 025 | Holland America Line | G | Generator retained `line`; the reviewed `hollandamerica.com` host was absent. | Missing `hollandamerica.com`; Oracle `HAGroup` bypass known. |
| 027 | IMG | G | `IMG + global` brand alias cannot be derived by current transforms. | Missing `imglobal.com`. |
| 028 | Necessary Ventures | G | `.vc` is outside the generated TLD set. | Missing `necessary.vc`. |
| 029 | Versana, UX | G | Same resolver input as 005: `versana.io` had no independent source and existed only as a mechanical TLD guess. | Missing source-backed `versana.io`; Lever exact known. |
| 030 | Frost | I | Correct `frostbank.com` was verified, but input `Frost` failed ambiguous-name identity strength. | `frostbank.com`. |
| 031 | Fabric | S | LinkedIn evidence was denied and search resolved unrelated Fabric entities; ownership could not be established. | Posting employer remains identity-ambiguous. |
| 032 | Dechert | G | `LLP` was retained in generated domains. | Missing `dechert.com`. |
| 033 | Slant CRM | G | The `.189` search happened to emit `slant.app` beside unrelated same-name sites, but the frozen `.193` rerun emitted no `slant.app` candidate at all; the search source is not a stable producer. | Missing current `slant.app`; Ashby exact known and is the safer provider-first route. |
| 034 | Brown and Caldwell | T | Correct homepage TLS handshake timed out. | `brownandcaldwell.com`; UKG exact known. |
| 035 | American Fabrication | I | Correct input host was requested and redirected to `amfab.us`, but redirect identity continuity scored below the selection gate. | `americanfabrication.com -> amfab.us`. |
| 037 | Team Royal | G | Generator could not drop `team` or produce the `.us` brand host. | Missing `royal.us`. |
| 038 | Rider Levett Bucknall | G | Standalone acronym host was not produced. | Missing `rlb.com`. |
| 039 | Salas O'Brien | T | Correct homepage returned HTTP 403. | `salasobrien.com`. |
| 040 | Hays + Sons | G | `+` normalization did not recover the brand's `and` connector. | Missing `haysandsons.com`. |
| 041 | City of Sioux Falls | G | Municipal `.gov` host and dropped `cityof` form were not produced. | Missing `siouxfalls.gov`. |
| 042 | Wichita Company | G | `.co.uk` plus removal of legal qualifiers is unsupported. | Missing `wichita.co.uk`. |
| 043 | City of College Station | G | Municipal acronym and `.gov` host were not produced. | Missing `cstx.gov`; GovernmentJobs board known. |
| 044 | Jushi Holdings | G | Branded `co` suffix host was absent from candidates. | Missing `jushico.com`. |
| 045 | State of Montana | G | State abbreviation `.gov` host was absent; search produced the wrong `state.gov`. | Missing `mt.gov`. |
| 046 | Ken Garff Automotive Group | G | Descriptive `automotive group` suffix was not removed. | Missing `kengarff.com`. |
| 047 | Systematic Business Consulting | G | Initialism-bearing brand host was absent; search selected another Systematic entity. | Missing `systematicbc.com`. |
| 048 | Heritage Companies | G | Official shorthand host has no derivable display-name/slug relationship. | Missing `hhandr.com`; Paylocity board known. |

Counts are `T=10`, `B=0`, `S=1`, `G=29`, and `I=4`, totaling 44. The zero
count for `B` is intentional: after `.193` added allocator decisions, no
remaining record had a source-backed correct candidate excluded solely by the
verification budget.

The second request-tape audit rejected the earlier `B=005/029/033` grouping.
Records 005 and 029 are two postings for the same Versana resolver input and
have an identical candidate pool and request order. `.193` proved that
`versana.io` was excluded, but it also proved that the URL had only
`speculative_guess` provenance. Slot crowding is therefore a secondary symptom,
not a sufficient causal class. Record 033 only shares the outer
`_rank_and_verify_candidates()` call: the frozen `.193` rerun failed to generate
`slant.app` at all, so unstable source production precedes allocation or identity
validation. Allocator traces must continue to distinguish source-backed leads
from guesses before any future `B` assignment.

## Executable Clusters

| Cluster | Records | Shared trigger and code path | Batch acceptance expectation |
| --- | --- | --- | --- |
| T-403 homepage denial | 010, 015, 016, 020, 039 | Correct candidate reaches `_score_candidate()` and receives HTTP 403. | Alternate authoritative transport/evidence must recover at least 3/5, or this cluster is split by site policy/provider bypass. |
| T-timeout | 000, 012, 034 | Correct candidate reaches `_score_candidate()` and the bounded request expires. | Same frozen request policy recovers at least 2/3 without increasing wrong websites or mean S2 budget. |
| T-certificate chain | 006 | Correct candidate and `www` fallback reach `_score_candidate()` and both fail certificate-chain verification. | 1/1 through an independently validated transport/source; TLS verification itself is not weakened. |
| T-TLS EOF | 018 | Correct candidate reaches `_score_candidate()` and the peer terminates during TLS establishment. | 1/1 through bounded retry or an independently validated source. |
| G-alias source absent with slot crowding | 005, 029 | The same resolver input reaches `_linkedin_slug_domain_candidates()`, `_guess_domain_candidates()`, and `_allocate_verification_slots()`: `versanatech.*` has slug provenance, while `versana.io` has only `speculative_guess` provenance and no source reservation. | A bounded company-name plus LinkedIn-slug source query must independently support the correct host before it may receive one reservation. Then record-level 2/2 and unique-host 1/1 must pass with `verify_limit=3`; no TLD preference, extra slot, or unrelated alias selection is allowed. If the source query supplies no such evidence, the cluster remains open rather than forcing the guess. |
| G-legal-form normalization | 021, 032 | `Limited/LLP` are absent from display, exact-identity, and LinkedIn-slug legal-form sets, so the source slug cannot produce the brand host. | 2/2 source-backed candidates generated under `verify_limit=3`; endpoint results are reported separately if a later transport failure appears. |
| G-structured display/slug cleanup | 001 | The display tagline survives tokenization and terminal delimiter noise prevents the existing `Inc` slug suffix rule. | 1/1 only after both structured display boundary and sourced slug cleanup pass collision negatives. |
| G-source-backed descriptive brand tail | 003, 017, 025, 046 | The reviewed host drops one or two descriptive/brand-tail tokens; those words are not legal forms and cannot be removed mechanically. | Independently sourced bounded prefix shortening recovers at least 3/4 with zero unrelated-brand selection. |
| G-unconfirmed shortening target | 019 | Frozen review did not confirm the claimed `cretex.com` ground truth, so no recovery URL is admissible yet. | Excluded from recovery scoring until ground truth is independently confirmed; then receives a separate 1/1 gate. |
| G-authoritative public-domain registry | 011, 041, 043, 045 | Independent city/state entities have authoritative `.gov` roots, but neither name transforms nor ordinary search produce a source-backed domain. | An authoritative public-domain registry route produces 4/4 websites; wrong-state, same-name, non-government, and `state.gov` collisions remain zero. Free-form `.gov` guessing is forbidden. |
| G-nested public agency namespace | 008, 023 | The hiring entity is an agency below a verified parent-government namespace, so the correct identity is a path or subdomain rather than an independently derivable apex. | Parent namespace plus first-party agency directory/link evidence produces 2/2 agency-specific identities; a parent homepage alone never counts. |
| G-education institution identity unconfirmed | 024 | No LinkedIn company source survived and frozen review did not confirm the probable `.edu` ground truth. | Excluded from recovery scoring until an authoritative institution directory confirms ground truth; then receives a separate 1/1 gate. |
| G-alias/acronym host | 013, 027, 037, 038, 044, 047, 048 | Name/slug transforms in `_guess_domain_candidates()` and `_linkedin_slug_domain_candidates()` cannot derive the reviewed brand alias, so no correct host reaches allocation. | Trusted source or provider relationship recovers at least 4/7; mechanical alias guessing alone is forbidden. |
| G-unsupported TLD | 028, 042 | `_guess_domain_candidates()` has no source-backed `.vc`/`.co.uk` route, so the correct brand host is absent before allocation. | 2/2 source-supported candidates produced and verified. |
| G-singular/plural | 002 | Exact-token transforms in `_guess_domain_candidates()` emit plural `indicalabs`, never singular `indicalab`. | 1/1 with collision-negative tests. |
| G-connector normalization | 040 | Company tokenization feeding `_guess_domain_candidates()` drops `+` without producing the brand connector `and`. | 1/1 with punctuation/brand collision negatives. |
| G-unstable search candidate | 033 | An older noisy search emitted `slant.app`, but the frozen `.193` rerun generated only speculative `slantcrm.*` candidates; no stable correct website candidate reaches allocation. | 1/1 only through a stable source with employer continuity or the independently verified Ashby provider route; increasing S2 slots does not count. |
| S-source identity ambiguity | 031 | LinkedIn source is denied and every search result is rejected by `_search_result_matches_company()` as a different or ambiguous Fabric entity. | Must resolve posting employer identity first; no URL recovery target is claimed meanwhile. |
| I-municipal redirect | 009 | `_score_candidate()` fetches the reviewed redirect target, then parent/group identity checks prevent `_select_verified_candidate()` from selecting it. | 1/1 only with first-party municipal continuity evidence. |
| I-holding-company identity | 022 | `_score_candidate()` fetches the correct site, but holding-company language reaches the same parent/group rejection path before `_select_verified_candidate()`. | 1/1 only when downstream provider relationship proves the same hiring group. |
| I-short-brand identity | 030 | `_score_candidate()` verifies the correct page, but ambiguous-name identity strength prevents `_select_verified_candidate()` from selecting it. | 1/1 with title/canonical/structured evidence; unrelated `Frost` negatives remain rejected. |
| I-redirect identity continuity | 035 | `_score_candidate()` follows `americanfabrication.com -> amfab.us`, then lexical continuity checks reject the final host before `_select_verified_candidate()`. | 1/1 only with first-party redirect/canonical relationship evidence; unrelated redirect targets remain rejected. |

For every cluster, the lower bound in the last column is part of the contract.
If a general repair falls below it, the cluster definition is rejected and the
unrecovered records are re-audited before any further heuristic/provider work.
A stage-level label, lower timeout count, or successful replay is not cluster
closure.

## Provider And External Apply Bypass Overlay

No public External Apply URL was available in this cohort, so the External Apply
overlay count is zero. Eleven records have
previously reviewed provider evidence that can bypass S2 candidate generation:

| Records | Provider evidence |
| --- | --- |
| 000, 001, 048 | Paylocity board/opening or board evidence |
| 002 | BambooHR tenant/opening |
| 009, 043 | GovernmentJobs tenant/board |
| 022 | Workday tenant/opening |
| 025 | Oracle tenant/opening |
| 029 | Lever tenant/opening |
| 033 | Ashby tenant/opening; preferred over treating ambiguous search identity as a website fact |
| 034 | UKG/UltiPro tenant/opening |

These are reviewed recovery targets, not automatic runtime successes and not
primary cause assignments. Every route must
still pass provider, tenant, hiring-relationship, title, location, status, and
S7 identity validation. In particular, Slant cannot establish
`Slant CRM -> Ashby/slant` from tenant-prefix similarity or a search snippet.
The provider must publish machine-readable employer identity that strictly
matches the input entity, and the same canonical tenant must publish the
matching title, location, status, and opening URL. If that evidence is absent,
record 033 remains `G`.

## Next Gate

`.193` completed the non-behavioral allocation audit; it did not close a product
cluster. Before behavior changes, freeze separate contracts and collision
fixtures for (1) source-backed Versana alias discovery and (2) provider-published
employer binding for Slant. A Versana change that merely reserves `versana.io`,
or a Slant change that merely probes an unverified same-name tenant, is rejected.
The live source preflight found no valid Versana alias result, so that path stays
open and fail-closed. `.194` therefore selects only the Slant-shaped generic
provider-published-employer contract for Phase B/C.
Run local tests per cluster, merge once, and use a single code-frozen focused
live/replay gate. If a proposed shared fix misses its table acceptance floor,
split and re-audit the unrecovered records before another change. The immutable
`.188` and `.189` artifacts must not be overwritten.

## Closure Delta

The 44-row matrix above remains the immutable pre-fix causal ledger. It is not
edited in place when a later focused gate succeeds.

`.195` closes only record 033 and its one-record `G-unstable search candidate`
acceptance contract. The code-frozen focused live did not produce or validate a
website; instead, the bounded provider route found Ashby tenant `slant`, and the
provider's public job-board API bound employer `Slant`, descriptor `CRM`, exact
title, strict city/state location, open status, tenant, board, and canonical
opening. Hiring and provider identities both cite that provider-owned API. The
same-version replay reproduced 1/1 with zero mismatch and zero fixture gap.

This is not S2 closure and does not generalize to records that lack equivalent
provider-published employer evidence. The post-`.195` open ledger therefore has
43 records: `T=10`, `B=0`, `S=1`, `G=28`, and `I=4`. Versana records 005/029
remain open because their source preflight still produces no independent
evidence for `versana.io`. See
`docs/FRESH_100_V195_PROVIDER_EMPLOYER_PHASE_C.md` for the frozen run audit.

`.196` then tested only the newly split legal-form records 021/032. Both correct
hosts were generated from their LinkedIn slugs and entered bounded verification,
so the shared candidate-generation defect is removed 2/2. End-to-end recovery
was 1/2: Dechert reached its verified Career/current inventory and an
authoritative `OPENING_NOT_FOUND`; FotoMill's correct host failed TLS in both
Python and curl. FotoMill is therefore reclassified from G to T-TLS instead of
being left under a repaired generation label. The post-`.196` open ledger has
42 records: `T=11`, `B=0`, `S=1`, `G=26`, and `I=4`. This is partial cluster
closure, not a lowered 2/2 endpoint expectation. See
`docs/FRESH_100_V196_LEGAL_FORM_PHASE_C.md`.

`.197` adds one bounded case-preserved Lever probe for a single-token company
before its lowercase equivalent. The Versana UX focused trace proves that the
correct `Versana` tenant candidate was generated and requested. Run 1 failed
after two TLS EOF responses; a same-version, fresh-root run 2 returned the
canonical Lever board and exact opening and replayed 1/1 with zero mismatch or
fixture gap. This moves the observed UX execution from candidate absence to a
transport-sensitive provider bypass, but it is only one company and therefore
does not satisfy the project's three-company generalization threshold or close
the two-record Versana ledger entry.

The subsequent public/institutional audit rejects the old seven-record `5/7`
contract. Records 011/041/043/045 share an authoritative public-domain registry
need; 008/023 share a nested agency relationship path; 024 remains unconfirmed
education identity. Provider overlays for 023 and 043 remain separate S5
contracts, and no External Apply URL exists for these records.
