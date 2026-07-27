# Coordinator `.234/.235` Phase C

## Result

The LinkedIn-official Website alias cluster is closed at its causal boundary.
The resolver no longer rejects an official site merely because a LinkedIn
display name contains a redundant acronym, a legal suffix, or a verified
LinkedIn slug uses the public brand name.

| Company | Website | Career | Job List | Current downstream boundary |
| --- | --- | --- | --- | --- |
| Rider Levett Bucknall RLB | `rlb.com` | verified | not found | S5 discovery |
| Jushi Holdings Inc. | `jushico.com` | verified | Lever verified | hiring relationship remains fail closed |
| Heritage Companies | `hhandr.com` | verified | Paylocity verified | S6 company budget |

The first three-record live run recovered RLB and Jushi. Heritage stopped in an
independent TLS handshake timeout before resolver identity evaluation. A clean,
single-record retry with new checkpoint, snapshot, evidence and completion
roots recovered its Website, Career page and Paylocity board.

## Safety

- Tata Technologies remains rejected as a true parent-company case.
- Domain, search snippet, provider tenant and company-name similarity do not
  establish this alias relationship.
- Provider, tenant, opening, title, location and S7 gates are unchanged.
- No opening URL was published by this focused cohort, so the fix creates no
  wrong URL, cross-company or cross-tenant publication.

## Replay

The first replay attempt exposed a separate deterministic replay defect for
Jushi's valid multi-hop first-party chain:

```text
jushico.com/careers -> jushico.com/job-listings -> jobs.lever.co/jushico
```

`.235` now restores the captured provider producer state only when the
relationship page shares the verified Career HTTPS host and the company,
LinkedIn identity, provider, tenant and canonical board all remain identical.
The original three-record capture then replayed 3/3 with zero mismatch or
fixture gap. Heritage's clean retry replayed 1/1 with the same S6 budget
terminal.

Artifacts:

- `/private/tmp/fresh3-v234-linkedin-alias-run1`
- `/private/tmp/fresh3-v234-linkedin-alias-run1/replay-bundle-v235`
- `/private/tmp/fresh1-v234-heritage-run2`

## Gates

- Resolver tests: 148/148.
- Replay bundle tests: 107/107.
- Integrated resolver/upstream/checkpoint/evaluation slice: 232/232.
- Resolver benchmark: 6/6.
- Architecture gate: 46 adapters, 0 issues.
- `git diff --check`: passed.

This closes the common resolver defect, not the three records' complete product
outcomes. Aggregate Exact and acceptable-terminal projections therefore remain
unchanged.
