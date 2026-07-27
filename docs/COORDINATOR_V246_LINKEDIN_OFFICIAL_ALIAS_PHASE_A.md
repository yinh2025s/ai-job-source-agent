# v246 LinkedIn-Official Homepage Alias - Phase A

## Causal Cluster

Three independent companies share one trigger and one resolver path:

| Company | LinkedIn-declared site | Homepage identity |
| --- | --- | --- |
| Yum! Brands | `www.yum.com` | `Yum.Com` |
| County of Maui | `mauicounty.gov` | `Maui County` |
| Duke University Health System | `dukehealth.org` | `Duke Health` |

For all three, the official candidate was fetched successfully and the page
contains first-party organization or employment evidence. The resolver still
adds `parent/group website requires downstream hiring relationship evidence`
because the homepage uses a shorter brand, drops institutional descriptors or
reorders `County of Maui` to `Maui County`.

Sofidel is excluded from this implementation cluster. Its `S.p.A.` tokenization
and candidate scheduling are a different code path.

## Contract

The parent/group rejection may be removed only when:

1. the candidate source is LinkedIn's declared official website;
2. the homepage was fetched successfully;
3. the LinkedIn slug and requested company name reduce to the same alias token
   set;
4. one structured organization identity or the leading homepage title reduces
   to that same token set; and
5. reduction removes only a fixed set of connectors, organization descriptors
   and web-title suffixes.

The comparison is set-based only to support official word-order variants. It
does not use fuzzy edit distance, substring matching or a company-specific
table.

## Negative Controls

- `Tata Technologies` must not accept a `Tata Group` homepage.
- `Bosch - Home` must not accept a `Bosch` homepage.
- `Google DeepMind` must not accept a `Google` homepage.
- a non-LinkedIn or unfetched candidate must not use the alias rule.

## Acceptance

- all three captured official homepages pass S2 in focused fixture replay;
- all three retain their exact official domain;
- no parent company negative control passes;
- downstream Career/Job Board/Opening gates remain unchanged;
- relevant resolver and pipeline tests pass;
- focused live/replay reports no cross-company, cross-tenant or wrong URL.

## Rollback

Remove the bounded alias-token comparison. Do not weaken the ordinary
parent/group rejection or add company/domain exceptions.
