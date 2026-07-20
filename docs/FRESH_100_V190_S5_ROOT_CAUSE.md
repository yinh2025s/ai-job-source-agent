# Fresh 100 `.190` S4/S5 Root-Cause Contract

## Scope

This Phase A audit freezes the eight `.188` fresh-cohort records ending in
`JOB_BOARD_NOT_FOUND`. It uses only the immutable trace and snapshots under
`/private/tmp/fresh100-v188-cold-20260720-run1`; it does not change the `.188`
score or reinterpret a focused success as a unified benchmark result.

The eight records represent seven company surfaces because B&D Industries
appears with two target titles.

## Failure Clusters

### 1. S4 accepts non-employment content as a Career page

- Milwaukee Tool selected a press release whose slug ends in `jobs`, even
  though the verified homepage contains a visible external
  `COMPANY + CAREERS` destination.
- B&D Industries selected `/projects/electrical-install-jobs/` from a sitemap;
  the second B&D record then reused that invalid URL as an identity-supplied
  Career root.

The current verifier lets a high URL score or metadata containing the word
`jobs` overcome a content-category conflict. An identity-supplied URL receives
priority but is not stronger evidence that its page is an employment surface.
The B&D duplicate also exposes a persistence defect: S4 writes candidates with
the plural `reasons` field, while the evidence writer reads singular `reason`.
The sitemap origin is consequently lost and the rejected URL is upgraded to
durable first-party navigation before the second record reuses it.

### 2. S5 truncates a registered provider handoff before verification

Northern Clearing's official Career page says that all openings are available
at `northernclearing.applicantpro.com`. The visible anchor text is only `HERE`,
so its score is zero. `_bounded_traversal_candidates()` keeps the first generic
links and does not reserve a slot for a URL recognized by a listing-capable
provider adapter. The existing ApplicantPro adapter therefore never runs.
After reservation, the visible-provider gate must also compare the adapter's
typed tenant identity rather than requiring the observed legacy URL to equal
the adapter's different canonical URL byte-for-byte.

### 3. S5 reaches a real inventory but does not recognize its structure

IGNITE's official Career page links to an HRSmart `View Job Openings` page.
S5 visits that page and captures many `/hr/ats/Posting/view/{id}` records,
including `CYBER SECURITY ANALYST- MID` in Huntsville. Generic inventory
verification recognizes only a narrower `job-detail` route family, so the
already reached inventory is not promoted to a verified Job Board.

### 4. Some records do not prove a recoverable public exact opening

- Splashlight's Career page delegates to LinkedIn and Indeed; its probed
  SmartRecruiters inventory is currently complete and empty. This is not
  evidence for inventing an official opening URL.
- CHAMP exposes a LinkedIn jobs link and a Freshteam widget script, but the
  frozen run did not capture a verifiable widget inventory or opening URL.
- NextPlay Jobs is a recruiting/staffing surface whose public page links back
  to LinkedIn and does not disclose an independently verifiable client opening.

These records remain evidence-classification work. They may become
`VERIFIED_NOT_FOUND`, `EXTERNAL_BLOCKED`, or `INPUT_IDENTITY_INVALID` only when
the corresponding terminal contract is proven; absence of a candidate alone
must remain `SYSTEM_GAP`.

## `.190` Repair Contract

The implementation must satisfy all of the following without company, URL,
tenant, title, or benchmark-specific branches:

1. A sitemap, stored, or identity-supplied candidate in a content category such
   as news, press, products, projects, stories, or articles cannot pass S4 only
   because its URL/title contains `job` or `career`. It needs current page-level
   employment evidence, a verified provider handoff, or structured openings.
2. Homepage navigation evidence may retain a visible, explicit external Career
   destination even when the destination URL itself has no path keyword. The
   checkpoint continues to store only the canonical public URL, never anchor
   text or raw HTML; same-site unrelated links such as `/about` remain excluded.
3. S5's bounded traversal reserves a slot for every observed HTTPS candidate
   that a registered listing-capable adapter can bind, subject to the existing
   public-URL, region, tenant, and relationship gates. Ranking cannot discard
   the only typed provider candidate before verification.
   Adapter-approved legacy-to-canonical normalization is allowed only when the
   provider and tenant extracted from both representations are identical.
4. A first-party action may establish a generic Job Board only after S5 actually
   visits the destination and verifies repeated same-origin opening records.
   Repeated detail routes may include provider-neutral `posting`, `position`,
   `opening`, `requisition`, `vacancy`, or `job` families with stable public
   identifiers; generic article/product IDs and isolated links remain rejected.
5. Search snippets, LinkedIn company-job pages, widget script names, and empty
   speculative provider probes cannot establish an Exact result.
6. S6 and S7 remain responsible for title, location, status, company, provider,
   tenant, and opening identity. S5 traversal success alone cannot publish a
   URL.
7. Persisted Career evidence records the actual selected origin and accepted
   verification method. A sitemap candidate cannot be silently rewritten as
   first-party navigation, and a deterministic semantic rejection invalidates
   only the Career layer while preserving a valid Website layer.

## Negative Matrix

- A press release titled "The Toughest Jobs" is not a Career surface.
- A project page named `electrical-install-jobs` is not a Career surface.
- An identity-supplied non-employment page receives no bypass.
- A same-site `/about` link labelled Careers is not persisted as URL-only S2
  evidence.
- An unregistered cross-site link labelled `HERE` receives no provider reserve.
- A registered provider URL with credentials, non-standard port, region
  conflict, tenant mismatch, or cross-site redirect is rejected.
- One numeric article URL is not inventory; repeated job-family detail routes
  reached from a verified Career action are inventory candidates.
- Similar title on another company or tenant remains rejected by S7.

## Focused Acceptance

Phase B/C requires:

- focused S4/S5 contract tests and all related provider/generic-inventory tests;
- Milwaukee/B&D non-employment pages rejected without selecting a replacement
  by guess;
- Northern's observed ApplicantPro handoff reaches the existing adapter;
- IGNITE's reached HRSmart page is recognized as a generic inventory and the
  target opening is still independently checked by S6/S7;
- no newly published wrong, cross-company, or cross-tenant URL;
- the fixed eight-record current live uses a new checkpoint/snapshot/evidence/
  completion root and code remains frozen during the run;
- same-version scoped replay reproduces every focused record with zero mismatch
  and zero fixture gap;
- full offline, provider, resolver, and architecture gates pass;
- `.188` fresh and frozen-100 artifacts remain immutable.

## Rollback

`.190` must be reverted if it turns content-category pages into Career success,
allows ranking to establish identity, treats search/LinkedIn snippets as
opening evidence, loses provider tenant isolation, or creates any wrong URL.
Recall improvement is not sufficient to retain a correctness regression.
