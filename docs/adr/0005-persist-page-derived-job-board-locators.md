# ADR-0005: Persist Replay-Safe Page-Derived Job Board Locators

- Status: accepted
- Date: 2026-07-14

## Context

S5 can identify an ATS hosted on a customer-owned domain by inspecting a public
career page. The provider adapter returns a `JobBoard` containing the canonical
board URL and the public tenant/configuration locator needed to query inventory.
The current S5 output keeps only the URL and provider name, so S6 must fetch and
classify the landing page again. A process restart or S6 checkpoint resume also
loses the provider-owned locator entirely.

The handoff crosses stage, checkpoint, replay, provider, and privacy boundaries.
It therefore needs a typed and versioned contract instead of an implicit trace
dependency.

## Decision

### Typed Handoff

1. S5 may emit a `DiscoveredJobBoard` containing a provider-owned `JobBoard`, a
   bounded detection method, and the public page URL that supplied the evidence.
2. S5 continues to emit `job_list_page_url` and `provider` for result and CLI
   compatibility. The typed handoff is internal pipeline state and is not added to
   the public result schema.
3. S6 prefers the typed board and asks the registry for the adapter named by the
   board. It does not reconstruct page-derived identity from trace. URL and page
   detection remain the compatibility fallback when no typed handoff is present.
4. Provider adapters remain responsible for validating locator shape, origin,
   tenant identity, response URLs, and returned job URLs before producing a
   candidate or authoritative no-match.

### Persistence Policy

1. Every `JobBoard` defaults to runtime-only. An adapter must explicitly mark a
   locator replay-safe before it may be written to a stage checkpoint.
2. A replay-safe locator may contain only a normalized public HTTPS board URL,
   provider name, bounded public tenant/configuration identifier, detection
   method, and public HTTPS evidence URL.
3. Each replay-safe provider has a registered locator policy that binds the
   identifier and nested URLs to the board origin and expected provider path.
   Unknown providers, sensitive query keys, credential-shaped values, raw HTML,
   control characters, and oversized values are rejected before adapter use.
4. Raw HTML, response bodies, request bodies, cookies, tokens, browser storage,
   authenticated content, request headers, and credentials are forbidden.
5. Runtime-only boards remain usable within the current process. Checkpoint
   serialization removes their typed handoff, so a resumed S6 run follows the
   existing page-detection path.
6. CEIPAL remains runtime-only because its current page-derived identity includes
   an API-key-shaped widget value. Public visibility alone is not enough to make a
   credential-like value checkpoint-safe.
7. A failure bundle is not a stage-checkpoint export. If its first failed stage is
   S6 and the successful S5 board required page evidence, replay starts at S5 and
   revalidates the locator from sanitized snapshots. It never reconstructs a
   typed locator from diagnostic trace. URL-native handoffs may continue at S6.
8. `results.json` and `trace.json` are both supported failure-bundle inputs. When
   diagnostic detection method is absent, replay may use only the stable S5
   provider, public board URL, and registry capabilities to decide that a
   page-aware/page-probe provider whose URL is not self-identifying must restart
   at S5. This is a recovery-boundary decision, not locator reconstruction;
   unknown, malformed, or URL-native records retain the existing path.

### Compatibility And Recovery

1. The typed object has strict JSON encode/decode validation. Unknown fields,
   unknown detection methods, unsafe URLs, invalid provider names, unbounded
   identifiers, and non-boolean persistence flags are rejected as checkpoint
   misses.
2. The checkpoint and adapter versions change whenever the persisted locator
   contract changes; the internal stage contract is versioned independently.
   Older stage checkpoints safely miss; there is no heuristic migration from
   trace data.
3. A missing or intentionally omitted typed handoff is valid and preserves the
   prior URL/page detection behavior.
4. Corrupt or provider-incompatible locators cannot produce a URL. The selected
   adapter must reject them with typed incomplete/unsupported semantics.

## Consequences

Positive:

- Page-aware providers can move from S5 to S6, including after a checkpoint
  resume, without repeating landing-page classification.
- Stage trace remains diagnostic output instead of becoming an undocumented
  runtime API.
- Provider-specific identity stays inside adapters and the registry remains open
  for extension without central provider branches.

Costs and limits:

- Each page-aware adapter must make an explicit replay-safety decision.
- A runtime-only provider may still repeat one public page fetch after resume.
- This contract improves execution reliability; it does not make blocked or
  incomplete provider inventory authoritative.

## Validation

- Contract tests cover strict encode/decode, unknown fields, unsafe URLs, bounds,
  and runtime-only omission.
- Stage tests cover typed S5 output and URL-only compatibility fallback.
- Checkpoint tests prove replay-safe round trips reconstruct dataclasses and
  runtime-only identifiers are absent from persisted JSON.
- Provider tests cover Sitecore/Next resume without a second landing-page fetch
  and CEIPAL runtime-only persistence.
- Main runs the complete offline gates and one serialized frozen live cohort after
  integration. Authenticated LinkedIn extension acceptance remains a separate
  real-Chrome gate.
- Completed on iteration `2026-07-14.51`: 774 tests, 23/23 provider benchmark,
  6/6 resolver benchmark, 23 adapters / 0 architecture issues, and serialized
  frozen-30 live rates of 30 websites, 28 career pages, 26 verified job lists,
  and 20 exact openings. The non-success replay gate reported 6 reproduced,
  2 fixture gaps, and 2 mismatches and exited nonzero as required.
- Iteration `.61` adds page-evidence S5 replay and URL-native S6 boundary tests.
  The `.59` capture improves from 5 reproduced / 2 fixture gaps to 6 reproduced /
  1 fixture gap without weakening the Akkodis hard-timeout evidence gate.
- Iteration `.73` gives results-only and trace-based artifacts the same recovery
  semantics without persisting provider locators in public results. The frozen-30
  capture replays 30/30 from either input with zero fixture gaps or mismatches.
