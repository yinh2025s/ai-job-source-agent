# ADR-0021: Probe Acquired-Brand Job Portals

- Status: accepted
- Date: 2026-07-14

## Context

An acquired company's verified career page may direct candidates to the parent
company's job portal. Ordinary same-site traversal correctly rejects that
cross-brand target, while globally rewriting the hiring entity would allow the
parent's entire inventory to masquerade as the acquired company's openings.

## Decision

1. This is an S5 probe-only handoff. It does not update S3 hiring identity,
   company website, or career root.
2. The source must be an already verified first-party career page. A visible
   semantic container must contain both a bounded relationship statement naming
   the input brand and parent brand, and an explicit job-list command linking to
   the target. Script, JSON, hidden content, news, partnership, `powered by`, and
   page-wide loose co-occurrence are not evidence.
3. The target must be public HTTPS on the default port, without credentials,
   sensitive query fields, or redirect parameters. At most one acquired-brand
   target is probed per discovery attempt.
4. The fetched target must remain on its final origin, present strong metadata
   for the extracted parent brand, and produce a listing-capable typed provider
   board. Text mentioning jobs, a generic page, detection-only adapters, and ATS
   auxiliary routes are insufficient.
5. Success emits the existing `DiscoveredJobBoard` using page evidence and keeps
   the original company identity. S6 must independently validate provider
   tenant, complete inventory, title, location, and exact opening. A same-title
   parent-company job is not automatically an acquired-company exact match.
6. Failure consumes only the single probe budget and then retains the existing
   typed retry/fallback behavior. No company-specific acquisition map is stored.

## Consequences

- CyberArk-shaped handoffs can reach a verified Palo Alto Networks provider
  board without treating every Palo Alto Networks role as a CyberArk role.
- The rule is intentionally narrower than ordinary cross-site job commands.
- Acquisition evidence remains an ephemeral navigation authorization, not a new
  organizational identity assertion.

## Validation

- Parser tests cover supported relationship language, exact brand binding,
  same-container commands, hidden/script evidence, partnerships, ambiguity,
  unsafe URLs, and the one-probe cap.
- Pipeline tests require strong parent metadata plus a listing-capable provider,
  reject auxiliary and untyped pages, and assert that S3 identity fields remain
  unchanged.
- Replay rebuilds the S5 probe from sanitized page evidence rather than a
  synthesized parent-company locator.
