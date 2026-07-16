# ADR-0026: Follow Career Inventory Before Provider Verification

- Status: accepted
- Date: 2026-07-16

## Context

A verified Career page often exposes its public job inventory one layer deeper than its landing
HTML. The handoff may be an explicit deeper navigation command, an embedded ATS URL, a
page-declared JSON endpoint, a first-party HTML listing, or a JavaScript-declared form transport.
Treating the landing page as the job list misses this evidence; treating every embedded URL or
script as trusted would weaken provider, tenant, privacy, and no-invented-URL guarantees.

The observed failures included an official page whose public JavaScript declares a native XHR
form search and another official page whose Greenhouse board is present in `script-src`. These
are reusable evidence shapes, not grounds for company-name branches.

## Decision

1. Preserve S1-S7. S4 verifies the Career page; S5 may follow bounded public inventory evidence;
   S6 matches only against the resulting verified inventory; S7 retains final hiring-entity,
   provider, tenant, board, opening, title, and location continuity checks.
2. A verified Career root may schedule explicit deeper job-list commands within the existing
   fetch/page budget. Unlabeled links, cross-site ordinary navigation, guessed routes, and generic
   employment language do not inherit official inventory status.
3. Page links, data attributes, iframes, embedded URLs, `script-src`, and static provider
   configuration are candidate provenance only. A candidate becomes a board only when a
   listing-capable registry adapter recognizes it and returns a canonical tenant board.
4. Generic first-party HTML inventory accepts strict listing records and follows only explicit
   next-page evidence. Pagination loops, cross-site next links, redirects, fetch errors, parser
   limits, page caps, candidate caps, and an unpaginated single page remain incomplete.
5. A page-declared JSON inventory may be fetched only when one bounded same-origin asset reads a
   page data attribute and performs one unambiguous anonymous GET to that exact public HTTPS URL.
   The response has fixed size, item, field, URL, and schema limits. Empty or malformed payloads
   do not establish authoritative empty inventory.
6. A JavaScript-declared inventory may inspect at most three bounded same-site script assets and
   execute exactly one statically recoverable same-origin public HTTPS transport. Supported
   transports are a literal anonymous request object or a form-encoded XHR POST with declared
   search fields. The runtime replaces only `searchTerm` with the target title and may copy only
   the bounded public page format value explicitly consumed by the script.
7. JavaScript is parsed as evidence and is never executed. Dynamic endpoints, multiple possible
   transports, credentials, sensitive fields, Authorization, cookies, `withCredentials`, private
   hosts, non-default ports, fragments, sensitive queries, response redirects, malformed schema,
   and candidate truncation fail closed.
8. Candidate opening URLs must remain on the first-party site or be a strict registered ATS detail
   URL. S6 still applies provider inventory, title, location, and canonical board identity checks;
   discovery rank or a declared transport cannot publish an exact opening by itself.
9. Provider-specific URL recognition, request shape, tenant isolation, pagination continuity,
   canonical board/detail construction, and inventory completeness remain inside provider
   adapters. Paycor and UltiPro are listing-capable adapters under the same registry contract;
   existing adapter variants use the same boundary. No central company or provider conditional is
   added.
10. A 403/429, bot challenge, transport error, ambiguous declaration, schema/tenant mismatch, or
    bounded truncation remains typed blocked, retryable, or incomplete evidence. It cannot be
    rewritten as `OPENING_NOT_FOUND`, `NO_PUBLIC_OPENINGS`, or exact success.

## Security And Privacy

- All navigated, asset, endpoint, board, and opening URLs must be public HTTPS without credentials,
  sensitive query keys, fragments, private hosts, or unsafe ports.
- Same-origin and provider-tenant redirect checks apply before response content is consumed.
- Requests do not read or forward browser state, cookies, tokens, Authorization values, arbitrary
  script headers, or authenticated payloads.
- Trace and checkpoints retain only bounded URLs, field names, counts, provider/tenant identity,
  completeness, and typed transport classification. Raw HTML, script bodies, response rows, and
  request secrets are not persisted as semantic evidence.
- A positive board or opening must pass the existing provider and identity gates. Incomplete or
  blocked inventory preserves a fallback job list when verified but never fabricates an opening.

## Consequences

Career surfaces can expose more of their already-public inventory without browser execution or
company exceptions. The broader evidence intake increases parser and adapter work, but every path
converges on the same registry, inventory-completeness, opening-selection, and S7 identity gates.
The bounded forms deliberately leave unsupported dynamic applications classified as incomplete.

## Validation

- Contract coverage includes explicit deep navigation, hidden/embedded registered ATS handoffs,
  page-declared anonymous inventory, native XHR form recovery, strict HTML pagination, Paycor and
  UltiPro tenant isolation, adapter canonicalization, malformed/ambiguous inputs, redirects,
  sensitive fields, credentialed requests, private URLs, caps, and typed transport failures.
- Tata Technologies' official Career JavaScript is recovered through the generic native XHR form
  contract. Its observed job list is verified, but opening discovery remains partial with
  `BOT_PROTECTION`; it is not an exact success.
- Banks Power's official page reaches its Greenhouse tenant through the generic `script-src`
  handoff and the normal Greenhouse adapter/identity checks, without a company-specific branch.
- The frozen observed 10-company live gate reaches 10/10 websites, 10/10 career pages, 10/10 job
  lists, and 9/10 exact openings in 147.2 seconds. Full-outcome replay matches, selects, exports,
  and reproduces all 10 records with the outcome gate passed.
- The cohort is observed and has no evaluation annotations. These numbers are raw funnel and
  replay evidence, not exact precision, conditional exact recall, or system-defect-rate claims.
