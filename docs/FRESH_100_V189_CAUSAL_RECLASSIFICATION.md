# Fresh 100 `.189` Causal Reclassification

## Decision

`.189` did not close an S2 failure cluster. It stabilized typed transport,
retry, worker completion, and snapshot/replay behavior, but recovered only two
additional websites (`3/49 -> 5/49`). The remaining 44 records therefore must
not be grouped by the stage at which execution stopped.

This audit uses the immutable run7 archive, the captured request tape, and the
previously reviewed July 18 website/Career/Job List evidence. The five resolved
records are excluded. Each unresolved record has one primary causal class. A
provider bypass is also recorded as an orthogonal recovery label because it is
not a cause of website-resolution failure.

## Classification Rules

| Code | Primary causal class | Required evidence |
| --- | --- | --- |
| T | Correct candidate existed, transport failed | Correct host was generated and requested; its request ended in timeout, TLS, or HTTP denial. |
| B | Verification budget starvation | Correct host was in the candidate pool but received no verification request because bounded slots were spent elsewhere. |
| S | LinkedIn/search source rejected | The available source resolved to an ambiguous or different entity and the ownership gate rejected it; no stronger source survived. |
| G | Correct candidate was never generated | Reviewed official host was absent from the candidate pool and request tape. |
| I | Candidate identity verification rejected | Correct host was fetched successfully but current identity/relationship rules refused selection. |
| P | Provider route can bypass website | Previously verified provider tenant/opening evidence can enter the three-route portfolio without repairing S2 first. |

`P` is a primary class only where the already verified provider route is the
most direct executable recovery (`record-025`). For other records it is an
overlay in the bypass matrix below. Recurring LinkedIn `451/999` responses are
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
| 005 | Versana, DevOps | B | `versana.io` was ranked but not requested within three verification slots. | Candidate `versana.io`. |
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
| 025 | Holland America Line | P | S2 missed the host, but a verified Oracle tenant and exact opening already exist. | Oracle `HAGroup`, job `13555`. |
| 027 | IMG | G | `IMG + global` brand alias cannot be derived by current transforms. | Missing `imglobal.com`. |
| 028 | Necessary Ventures | G | `.vc` is outside the generated TLD set. | Missing `necessary.vc`. |
| 029 | Versana, UX | B | `versana.io` was present but received no verification slot. | Candidate `versana.io`; Lever exact known. |
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

Counts are `T=10`, `B=2`, `S=1`, `G=26`, `I=4`, and `P=1`, totaling 44.

The second request-tape audit rejected the earlier `B=005/029/033` grouping.
Records 005 and 029 are two postings for the same Versana resolver input and
have an identical candidate pool and request order. Record 033 only shares the
outer `_rank_and_verify_candidates()` call: its candidate came from noisy search
evidence and lacked employer-identity continuity. The `.193` frozen rerun then
failed to generate `slant.app` at all, proving that unstable search production
precedes any identity decision for the current record. The old trace records
generated candidates and actual requests, but not allocator `selected`,
`excluded`, or `reason` decisions. Therefore allocator decision trace must be
added before another causal allocation claim is inferred from a missing request.

## Executable Clusters

| Cluster | Records | Shared trigger and code path | Batch acceptance expectation |
| --- | --- | --- | --- |
| T-403 homepage denial | 010, 015, 016, 020, 039 | Correct candidate reaches `_score_candidate()` and receives HTTP 403. | Alternate authoritative transport/evidence must recover at least 3/5, or this cluster is split by site policy/provider bypass. |
| T-timeout | 000, 012, 034 | Correct candidate reaches `_score_candidate()` and the bounded request expires. | Same frozen request policy recovers at least 2/3 without increasing wrong websites or mean S2 budget. |
| T-certificate chain | 006 | Correct candidate and `www` fallback reach `_score_candidate()` and both fail certificate-chain verification. | 1/1 through an independently validated transport/source; TLS verification itself is not weakened. |
| T-TLS EOF | 018 | Correct candidate reaches `_score_candidate()` and the peer terminates during TLS establishment. | 1/1 through bounded retry or an independently validated source. |
| B-Versana slug-family crowding | 005, 029 | The same resolver input generates `versana.io` as a low-evidence speculative candidate, while higher-scored candidates derived from the misleading `versanatech` LinkedIn slug consume the bounded verification path. | Record-level 2/2 request and select `versana.io` with `verify_limit=3`; each allocation stays at three candidates and unrelated low-evidence/TLD collision controls remain rejected. Because both records share one resolver input, report one unique-host recovery as well as 2/2 records. |
| G-core/legal shortening | 001, 003, 017, 019, 021, 032, 046 | `_guess_domain_candidates()` / `_linkedin_slug_domain_candidates()` retain descriptive/legal tail tokens. | Source-backed bounded shortening recovers at least 5/7 with zero unrelated-brand selection. |
| G-public/institutional namespace | 008, 011, 023, 024, 041, 043, 045 | Current generators do not produce government/education hierarchy or abbreviations. | Authoritative public-domain/search route recovers at least 5/7; no free-form `.gov` guessing. |
| G-alias/acronym host | 013, 027, 037, 038, 044, 047, 048 | Official brand/initialism cannot be derived from current name and slug transforms. | Trusted source or provider relationship recovers at least 4/7; mechanical alias guessing alone is forbidden. |
| G-unsupported TLD | 028, 042 | Correct brand is known but `.vc`/`.co.uk` is outside the candidate TLD contract. | 2/2 source-supported candidates produced and verified. |
| G-singular/plural | 002 | Brand singularization is missing. | 1/1 with collision-negative tests. |
| G-connector normalization | 040 | `+` does not map to brand connector `and`. | 1/1 with punctuation/brand collision negatives. |
| G-unstable search candidate | 033 | An older noisy search emitted `slant.app`, but the frozen `.193` rerun generated only speculative `slantcrm.*` candidates; no stable correct website candidate reaches allocation. | 1/1 only through a stable source with employer continuity or the independently verified Ashby provider route; increasing S2 slots does not count. |
| S-source identity ambiguity | 031 | LinkedIn source is denied and search entities fail ownership continuity in `_search_result_matches_company()`. | Must resolve posting employer identity first; no URL recovery target is claimed meanwhile. |
| I-municipal redirect | 009 | Correct reviewed site is rejected as parent/group after redirect. | 1/1 only with first-party municipal continuity evidence. |
| I-holding-company identity | 022 | Correct site is fetched but holding-company language triggers parent/group rejection. | 1/1 only when downstream provider relationship proves the same hiring group. |
| I-short-brand identity | 030 | Correct page is verified but abbreviated input lacks sufficient positive identity. | 1/1 with title/canonical/structured evidence; unrelated `Frost` negatives remain rejected. |
| I-redirect identity continuity | 035 | Correct input is requested, but its `americanfabrication.com -> amfab.us` redirect loses lexical company continuity in `_score_candidate()`. | 1/1 only with first-party redirect/canonical relationship evidence; unrelated redirect targets remain rejected. |
| P-provider-first | 025 | Verified Oracle tenant/opening exists independently of S2. | 1/1 reaches S5-S7 through provider candidate discovery while S2 remains non-blocking. |

For every cluster, the lower bound in the last column is part of the contract.
If a general repair falls below it, the cluster definition is rejected and the
unrecovered records are re-audited before any further heuristic/provider work.
A stage-level label, lower timeout count, or successful replay is not cluster
closure.

## Provider Bypass Overlay

No public External Apply URL was available in this cohort. Eleven records have
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

These are candidate-discovery inputs, not automatic successes. Every route must
still pass provider, tenant, hiring-relationship, title, location, status, and
S7 identity validation.

## Next Gate

Do not modify resolver/provider behavior from this report alone. First add a
non-behavioral allocation decision trace that records bounded selected and
excluded candidates with reasons. Then freeze separate fixtures and negative
controls for Versana allocation and Slant candidate-generation/provider recovery. A Versana
repair below 2/2 or a Slant repair that merely probes an unverified same-name
site invalidates its cluster contract. Run local tests per cluster, merge once,
and use a single code-frozen focused live/replay gate. The immutable `.188` and
`.189` artifacts must not be overwritten.
