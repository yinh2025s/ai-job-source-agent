# Coordinator `.234` LinkedIn Official Alias Phase A

## Scope

This phase addresses one causal cluster in the Fresh100 development cohort.
It does not change provider, tenant, hiring-relationship, opening-title,
location or S7 validation.

Three companies share the same trigger and resolver path:

| Company | LinkedIn-bound official site | Current rejection |
| --- | --- | --- |
| Rider Levett Bucknall RLB | `https://www.rlb.com` | the homepage identity omits the redundant `RLB` acronym |
| Jushi Holdings Inc. | `https://www.jushico.com` | the primary brand is `Jushi`, while the body and legal footer name `Jushi Holdings Inc.` |
| Heritage Companies | `https://www.hhandr.com` | the LinkedIn company slug and homepage identify `Heritage Hotels & Resorts` |

In all three records, the LinkedIn company page supplies the official Website,
the Website fetch succeeds, and first-party page identity is present.
`_homepage_has_parent_group_identity()` nevertheless treats the display-name
difference as a parent/group handoff. `_select_verified_candidate()` then
rejects the candidate before S3/S4.

## Causal Contract

A LinkedIn-bound official Website may suppress the parent/group rejection only
when the Website independently proves the identity carried by that same
LinkedIn company URL:

1. the candidate must carry observed LinkedIn official-Website evidence;
2. a multi-token LinkedIn company slug must match a first-party structured
   organization identity or homepage title after bounded normalization; or
3. a single-token LinkedIn slug must be reinforced by an exact legal/display
   identity statement for the input company in first-party page content.

Domain similarity, search snippets, canonical URLs, one shared generic token or
tenant-name equality are insufficient.

## Negative Gates

The following must remain rejected:

- `Tata Technologies` bound to a page that identifies only `Tata`;
- `Google DeepMind` bound to a page that identifies only `Google`;
- an ambiguous short company such as `Focus` whose page offers only
  self-referential one-token identity;
- an unrelated same-name or extension-domain page;
- any candidate that was not observed as the LinkedIn company's official site.

## Ownership

- `job_source_agent/website_resolver.py`
- `tests/test_website_resolver.py`

Shared provider, stage, checkpoint and S7 contracts are out of scope.

## Acceptance

- all three positive fixtures no longer receive the parent/group rejection and
  are selectable as Websites;
- the parent-company and ambiguous-name negative fixtures remain rejected;
- resolver/upstream scoped tests pass;
- resolver benchmark remains 6/6;
- architecture gate remains clean;
- `git diff --check` passes.

After offline acceptance, run a clean three-record focused live/replay. A
Website recovery is the expected batch result; Exact is not required unless
the unchanged downstream evidence chain independently proves it.

## Rollback

Revert the alias-continuity helper and its call site. The previous fail-closed
parent/group behavior then returns without affecting any provider or S7 code.
