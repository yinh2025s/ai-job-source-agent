# ADR-0017: Bound Multi-Board Opening Discovery

- Status: accepted
- Date: 2026-07-14

## Context

One company can publish independent general, university, early-career, regional,
or acquired-business inventories. S5 previously selected one verified board,
so S6 could mistake a complete empty response from a scoped board for
company-wide evidence.

## Decision

1. S5 may publish an ordered `JobBoardPortfolio` of one to eight typed
   `DiscoveredJobBoard` values plus `eligible_set_complete`.
2. Expansion occurs only when the primary board has a bounded audience mismatch
   with the target title. Each alternative must be recognized by a
   listing-capable native adapter and return positive candidates or a complete
   inventory; URL text alone is insufficient.
3. Search errors, circuit breaks, provider failures, and fetch-cap truncation
   make the eligible set incomplete.
4. S6 attempts at most the versioned `max_job_board_attempts`. Company-wide
   `OPENING_NOT_FOUND` or `NO_PUBLIC_OPENINGS` requires every eligible board to
   be attempted completely; otherwise the result remains incomplete.
5. A single complete board retains the legacy S6 result, evidence, and trace.
6. Portfolio checkpoints are all-or-nothing. Workday and SmartRecruiters public
   locators are replay-safe only when HTTPS host, canonical path, provider, and
   tenant/company identifier agree. No HTML, job payload, cookie, token, or
   browser state is persisted.
7. Run configuration, contract, and checkpoint schemas become `1.2`, `1.4`, and
   `1.6`; adapter version becomes `2026-07-14.84`.

## Consequences

- A scoped-board empty result cannot establish company-wide absence while an
  eligible board remains unchecked.
- Extra search and provider probes stay bounded and occur only for a scoped
  primary board.
- A failed S2 website-only input is cleared before downstream stages; explicit
  career-root or external-apply handoffs retain their independent evidence path.

## Validation

- Tests cover ordering, duplicates, case-sensitive paths, strict payloads,
  corruption, runtime-only omission, attempt truncation, later-board exact
  recovery, complete no-match, and single-board compatibility.
- A focused Visa capture reached the official general Workday board and exact
  `Sr Data Scientist` opening while exposing the S2 fail-closed handoff defect.
- Final gates pass 1249 tests, 25/25 production provider cases, 6/6 resolver
  cases, and 24 adapters / 0 architecture issues.
