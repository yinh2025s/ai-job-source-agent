# v249 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts: `/private/tmp/v249-diagnostic-run1`

- adapter version: `2026-07-27.246`;
- records: 30;
- independent companies: 28;
- Website: 30/30;
- Career: 22/30;
- verified Job List: 18/30;
- S7 Exact: 6/30;
- elapsed: 868.7 seconds.

The input has zero LinkedIn job-ID overlap with Fresh100 and v245-v248. The run
uses public search-card inputs, isolated state roots and unchanged product code.

## Exact Safety Audit

All six Exact openings pass the evidence and current-status audit:

| Company | Provider / tenant | Opening identity |
| --- | --- | --- |
| Assembled | Ashby / `assembledhq` | exact title, New York City, listed |
| Fanatics | Oracle HCM / `fa-exki-saasfaprod1`, `CX_1` | exact title, Philadelphia, open |
| Zynga | Greenhouse / `zyngacareers` | exact title, Austin included, open |
| Attentive | Greenhouse / `attentive` | exact title, United States, open |
| Bumble Inc. | Lever / `bumbleinc` | exact title, New York included, open |
| LaBella Associates | Workable / `labella-associates` | exact title, New York, approved |

Aggregate safety:

- correct company, title, location, provider, tenant and URL: 6/6;
- wrong or non-specific opening URL: 0;
- cross-company or cross-tenant publication: 0;
- wrong location or closed-opening publication: 0;
- Exact live/replay opening mismatch: 0.

## Non-Exact Causal Audit

The 24 non-Exact records represent 23 independent companies. No group satisfies
the three-company implementation contract.

### Correct negative controls

Google has two records whose official Google inventory lacks the target title.
Bumble has the title but not the target location. Del Monte's complete ADP
inventory has no match. These records must remain non-Exact; relaxing title or
location would reduce precision.

### Distinct downstream paths

- Tebra: verified Greenhouse board followed by opening fetch failure.
- Constellation West: ADP/generic inventory consumes the company deadline.
- Fiverr: Comeet detail links.
- Odoo: first-party custom job pages.
- LANDED, Starbucks and DSD Recruitment: distinct generic listing transports.
- Starface: Greenhouse evidence points to a general-interest form rather than a
  specific target opening.
- InterEx: no reliable hiring handoff.
- Solomon Page: custom Herefish/application surface.
- Roku: first-party `/jobs/search` evidence remains below relationship and
  inventory authorization.
- LinkedIn: the diagnostic execution stops after verified SmartRecruiters board
  discovery; it is not evidence of a shared opening parser defect.

These paths cannot be repaired by one parser, adapter or evidence rule.

### Upstream budget exits

Crossing Hurdles, Studio Rose, Hasbro, Medix, KINO, SBH Fashion, Metabase and
Private Equity Fund share budget labels but not triggers. Their evidence spans
access blocking, weak or incorrect homepages, dynamic sites and ambiguous
employer identity. Increasing a shared deadline does not establish an expected
three-company recovery.

## Candidate-Cluster Check

v249 supplies no third recovery company for the three nearest hypotheses:

1. Workable numeric embed remains American Battery plus ClassWallet, 2/3.
2. Strict structured job cards remain Opstergo plus Funhouse, 2/3.
3. Same-origin dynamic GET remains Confidential plus sweetgreen, 2/3.

No v249 snapshot contains the matching numeric embed, strict-card trigger or
dynamic endpoint declaration. Visually similar CSS, generic Career pages and
unrelated custom inventories are retained as negative controls.

## Replay Integrity

All 30 records, traces and scoped tapes were exported and replayed:

- complete reproduction: 22;
- budget-normalized comparison: 5;
- mismatch: 3;
- fixture gap: 0;
- tape divergence: 0;
- missing snapshot boundary: 0.

The mismatches are:

1. Tebra: live `FETCH_FAILED`, replay `COMPANY_TIME_BUDGET_EXHAUSTED`; company,
   Greenhouse tenant and absence of an opening are unchanged.
2. Constellation West: the same terminal-code drift on a different ADP/generic
   provider path.
3. Fanatics: Exact identity and URL are unchanged; only relationship
   `evidence_url` selection changes between the Oracle board and the first-party
   Career page.

Studio Rose, Hasbro, KINO, SBH Fashion and Metabase are budget-normalized from
live `COMPANY_TIME_BUDGET_EXHAUSTED` to replay `CAREER_PAGE_NOT_FOUND`. They
share a wall-clock-versus-tape effect, not an evidenced product recovery path.

## Decision

No Phase B product change is authorized. v249 confirms 100% Exact precision but
does not produce a three-company shared recall or replay defect. Continue
targeted backend evidence collection on unchanged `.246`; do not alter title,
location, identity, provider, tenant or replay gates.

Fresh100 remains 37 Exact, 12 Verified No Match, 1 External Blocked and 50
unresolved. The extension, coordinator-v2, LLM branch and sealed holdouts remain
frozen.
