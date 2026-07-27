# Coordinator `.227` Provisional Official Website Phase A

## Scope

The current Fresh100 projection contains a recurring causal cluster that is
not represented by the shared `JOB_BOARD_NOT_FOUND` stage label:

- Heritage Companies
- NYC Department of Social Services
- North Dakota Information Technology
- City of Lubbock
- State of Montana

For each record, LinkedIn identifies an official website and the fetched
homepage verifies that site, but the resolver correctly refuses to publish it
as the final company website because the page represents a parent, group, or
government umbrella. The rejected candidate is then discarded. S4 cannot
inspect the official site for a first-party Career handoff and S5 reports
`verified_website_career_evidence_absent`.

## Causal Contract

```text
LinkedIn official website
-> current homepage verification
-> parent/group relationship still unresolved
-> candidate withheld from published Website output
-> no typed evidence reaches S4/S5
-> first-party handoff cannot prove the missing relationship
```

This is a circular evidence dependency. It is not solved by accepting the
parent/group site, trusting a search result, matching a tenant name, or
relaxing S7.

## Change Boundary

Introduce immutable `ProvisionalWebsiteEvidence` with these semantics:

- it is emitted only for a currently fetched LinkedIn-official website whose
  homepage passed identity verification;
- it never populates `company_website_url` and cannot itself make S2, S3, S5,
  or S7 successful;
- S4 may use it as a bounded exploration root;
- a same-host Career page (`www` is equivalent) must pass the existing current-page Career and
  company checks, or a cross-site provider handoff must be observed from the
  official site;
- only that verified first-party chain may create hiring relationship
  evidence and authorize a provider board;
- targeted search, guessed paths, title similarity, and tenant similarity
  remain untrusted leads.

The context and checkpoint schemas are upgraded because the evidence crosses
stage and replay boundaries. The evidence contains only canonical public URLs
and stable reason identifiers; it cannot contain HTML, cookies, tokens, or
browser state.

## Acceptance

1. Contract tests reject non-HTTPS, mismatched company, mutable, or malformed
   provisional evidence.
2. S2 still fails closed and publishes no Website while retaining the typed
   candidate.
3. S4 can inspect that candidate and only creates verified hiring identity
   after a current same-host Career chain is established. Shared-host suffixes
   and cross-site trace claims remain untrusted.
4. S5 can use the chain, but an unverified/search-only candidate remains
   unauthorized and cannot publish a Job Board or Exact opening.
5. Checkpoint round-trip is exact and stale `.226` checkpoints invalidate.
6. Focused live and replay cover the complete cluster; a recovery of only one
   or two companies rejects the cluster definition rather than declaring it
   closed.
7. Wrong URL, cross-company, and cross-tenant publication remain zero.

## Rollback

Remove the provisional context field and restore `.226` schema/version
constants. No durable website evidence is written for provisional candidates,
so rollback does not require cache migration.
