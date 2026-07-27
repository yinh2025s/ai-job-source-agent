# `.225` Job Board And Forbidden Audit

## Decision

The Job Board-not-verified and HTTP-forbidden labels each cover three
independent companies, but neither is a causal implementation cluster. No code
change is justified.

## Job Board Not Verified

- CHAMP: Freshteam widget/provider declaration was not recovered from current
  page evidence.
- Fabric: static Career content produced no trusted board; dynamic inventory or
  a missing declaration remains unproven.
- Hawaiian Electric: the page contains an explicit official
  `careers.hawaiianelectric.com/go/All-Jobs/...` handoff, but it was not
  descended and bootstrapped as SuccessFactors.

Hawaiian Electric has two postings but counts as one company. The three causes
use different extraction and provider paths; a shared S5 terminal does not
justify wider traversal, script parsing or cross-domain authorization.

## HTTP Forbidden

- Altec: Website, Career and `jobs.altec.com` are verified; the official job
  inventory itself persistently returns 403.
- City of Sioux Falls: the verified government Website and Career paths return
  403 before any authorized provider bypass exists.
- North Dakota Information Technology: the correct `ndit.nd.gov` page was
  fetched; the published 403 came from wrong candidate `kxnet.com` and must not
  be interpreted as official-site blocking.

The failures occur at different stages and hosts. A 403 may be attributed only
to a verified official host/provider; Website blocking and inventory blocking
remain separate, and an unverified ATS tenant cannot bypass either.

## Next Evidence Strategy

The current development cohort's broad labels are now causally split. The next
step is read-only recurrence mining across existing non-sealed fixtures and
artifacts for:

1. explicit official cross-domain ATS handoffs missed before provider
   bootstrap;
2. Freshteam widget/declaration recovery;
3. wrong-candidate transport failure overriding already verified official
   evidence.

Implementation begins only if one exact shape reaches three independent
companies. Sealed blind cohorts and the LLM branch remain untouched.
