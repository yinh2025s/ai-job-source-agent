# AI Job Source Agent

[![CI](https://github.com/yinh2025s/ai-job-source-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/yinh2025s/ai-job-source-agent/actions/workflows/ci.yml)

Take-home implementation for Part 2: discover companies hiring on LinkedIn, resolve their official websites or hiring entities, and navigate to official career/job-list pages.

The implementation is intentionally agentic but controlled: deterministic link extraction and scoring do the first pass, then the agent follows promising career/job-listing pages for a few hops. The pipeline keeps trace data for every navigation decision.

Project documentation:

- [Implementation plan](IMPLEMENTATION_PLAN.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Development governance](DEVELOPMENT_GOVERNANCE.md)
- [Changelog](CHANGELOG.md)
- [Architecture decisions](docs/adr/README.md)

The project supports two flows:

- `--linkedin-keywords`: search public LinkedIn job results, extract hiring companies, resolve official websites from LinkedIn company pages/search/domain hints, map brands to parent hiring systems when needed, and find official job-list pages.
- `--input`: run the downstream website-to-careers pipeline from pre-extracted company records.

## Python Runtime

The supported range is CPython 3.10 through 3.13. Release gates are pinned to CPython 3.12; the repository includes `.python-version` and defaults `make` targets to `python3.12`. Homebrew Python 3.14.2 produced reproducible native crashes during long live batches and is intentionally excluded until that runtime is validated.

```bash
python3.12 scripts/check_runtime.py --release
make offline-gates
make live-gate
```

Set `PYTHON=/path/to/python3.12` when `python3.12` is not on `PATH`.

GitHub Actions runs the test suite across CPython 3.10-3.13 and repeats all offline release gates on 3.12 for every push and pull request. The network-dependent 51-company gate is a manual `Live release gate` workflow and retains its result, trace, and summary JSON as artifacts even when the gate fails.

The first Linux CI run passed all jobs: [run 29240521415](https://github.com/yinh2025s/ai-job-source-agent/actions/runs/29240521415).

## What It Returns

For each input record:

```json
{
  "company_name": "Aurora Data",
  "company_website_url": "https://aurora-data.example",
  "linkedin_job_url": "https://www.linkedin.com/jobs/view/aurora-data-ai-engineer",
  "external_apply_url": null,
  "hiring_entity_name": null,
  "career_root_url": null,
  "career_page_url": "https://jobs.lever.co/aurora-data",
  "job_list_page_url": "https://jobs.lever.co/aurora-data",
  "open_position_url": "https://jobs.lever.co/aurora-data/d9d64766-3d42-4ba9-94d4-f74cdaf20065",
  "status": "success",
  "error": null,
  "error_code": null,
  "pipeline_status": "success",
  "career_page_status": "success",
  "job_board_status": "success",
  "opening_match_status": "success",
  "stages": ["... seven structured stage results ..."]
}
```

`status` and `error` remain for compatibility with the original demo. New consumers should use `pipeline_status`, `error_code`, and `stages`: every stage records its status, reason code, retryability, owner, provider, duration, counts, and evidence. The CLI also writes `trace.json`, which includes candidate links, scores, reasons, and the selected page source.

## Run the Deterministic Demo

This uses local HTML fixtures, so it works without network access:

```bash
python3 -m job_source_agent \
  --input samples/linkedin_jobs.json \
  --fixtures-dir samples/sites \
  --offline \
  --output results.json \
  --trace-output trace.json
```

Expected output:

```text
OK Aurora Data
  website: https://aurora-data.example
  career: https://jobs.lever.co/aurora-data
  job list: https://jobs.lever.co/aurora-data
  opening: https://jobs.lever.co/aurora-data/d9d64766-3d42-4ba9-94d4-f74cdaf20065
OK Nimbus Robotics
  website: https://nimbus-robotics.example
  career: https://nimbus-robotics.example/careers
  job list: https://nimbus-robotics.example/careers
  opening: https://boards.greenhouse.io/nimbusrobotics/jobs/5012345001
```

The standard CLI can persist and reuse compatible per-stage checkpoints:

```bash
python3 -m job_source_agent \
  --input samples/linkedin_jobs.json \
  --fixtures-dir samples/sites \
  --offline \
  --checkpoint-dir /tmp/job-source-checkpoints \
  --output results.json \
  --trace-output trace.json

python3 -m job_source_agent \
  --input samples/linkedin_jobs.json \
  --fixtures-dir samples/sites \
  --offline \
  --checkpoint-dir /tmp/job-source-checkpoints \
  --rerun-stage opening_match \
  --output rerun-results.json \
  --trace-output rerun-trace.json
```

`--resume-from-stage` restores compatible upstream stage updates, while `--rerun-stage` invalidates the selected stage and everything after it. `--stop-after-stage` is useful for inspecting a partial pipeline.

## Discover From LinkedIn Jobs

This mode starts from public LinkedIn job search results:

```bash
python3 -m job_source_agent \
  --linkedin-keywords "AI Engineer" \
  --linkedin-location "United States" \
  --limit 3 \
  --linkedin-pages 1 \
  --fetch-timeout 5 \
  --output linkedin-results.json \
  --trace-output linkedin-trace.json
```

Example live output from July 10, 2026:

```text
OK Instagram
  linkedin job: Product Manager
  website: https://www.instagram.com/
  career: https://www.metacareers.com/jobs/
  job list: https://www.metacareers.com/jobs/?q=Product+Manager
  opening: None
FAIL Tessera Labs
  linkedin job: Product Manager, Intern
  website: https://tesseralabs.ai
  career: None
  job list: None
  opening: None
  error: career_page_not_found
```

Many modern career pages render concrete job cards with JavaScript or do not expose a public official career page at all. In those cases, `open_position_url` may be `null` while `job_list_page_url` is still useful. If no official career page can be verified, the agent returns a structured failure instead of inventing a job URL.

LinkedIn's public guest job HTML does not reliably expose the off-site target behind its Apply control. An authenticated browser extension, a saved authenticated page, or another trusted extractor can provide that target as `external_apply_url`. The backend does not trust an arbitrary external URL as a result: S5 must map it to a supported native provider board, and S6 must read that provider's public inventory before returning a concrete opening. This fallback can therefore recover a job when the marketing website is blocked or unresolved without weakening normal website verification.

Example input for the fallback:

```json
{
  "linkedin_job_url": "https://www.linkedin.com/jobs/view/example",
  "external_apply_url": "https://company.wd5.myworkdayjobs.com/Site/job/Role_R123",
  "company_name": "Example Company",
  "job_title": "Machine Learning Engineer",
  "job_location": "New York, NY",
  "source": "linkedin_browser_extension"
}
```

### Local Chrome Extension

Start the loopback bridge from the repository root. Set an explicit token for a stable local setup:

```bash
JOB_SOURCE_BRIDGE_TOKEN="replace-with-a-local-secret" \
  python3.12 -m scripts.extension_bridge \
  --port 8765 \
  --workers 2 \
  --fetch-timeout 8
```

In `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select the repository's `extension/` directory. Open the extension's **Connection** section, keep `http://127.0.0.1:8765`, enter the same token, and save. On a LinkedIn Jobs detail or search-results page, **Scan page** collects up to 30 visible records; **Run discovery** submits an asynchronous run and reports verified job-list and exact-opening rates. Results, trace, and summary files are stored under `~/.ai-job-source-agent/runs/<run-id>/`.

The extension never reads cookies or sends LinkedIn page data to a remote service. The bridge binds only to a loopback host, requires the bearer token, accepts Chrome-extension origins, limits request size and record count, and delegates all network/provider decisions to the Python application. DOM-visible External Apply links are optional evidence; missing links remain empty.

For checkpointed LinkedIn batches, `scripts/live_batch_eval.py` freezes the discovered companies, ordering, and target titles in a versioned `linkedin-discovery.json` manifest inside the batch checkpoint directory. A resumed command reuses that cohort instead of rerunning a changing public search. `--no-resume` explicitly refreshes both the cohort and company executions; `--linkedin-manifest` can place the manifest at a chosen path. The summary records whether the cohort was `saved`, `restored`, or `refreshed`.

For JavaScript-heavy or bot-protected pages, install the optional browser module:

```bash
pip install -e ".[browser]"
playwright install chromium
```

If the Playwright-managed Chromium download is unavailable, the renderer will try the local Chrome channel when Google Chrome is installed on the machine.

Then run with smart browser fallback. Static HTML is tried first; Playwright is used only when the page looks like a JavaScript shell or the static request fails:

```bash
python3 -m job_source_agent \
  --linkedin-keywords "AI Engineer" \
  --linkedin-location "United States" \
  --limit 3 \
  --render-js \
  --render-budget 3 \
  --render-screenshot \
  --output linkedin-results.json \
  --trace-output linkedin-trace.json
```

For debugging, `--render-js-always` forces every live HTML page through Playwright. `--render-screenshot` records screenshot artifacts for rendered pages; pair it with `--snapshot-dir` in batch runs to persist the files.

## Run Against Live Websites

Create an input file with records like:

```json
[
  {
    "linkedin_job_url": "https://www.linkedin.com/jobs/view/example",
    "company_name": "Example Company",
    "company_website_url": "https://example.com"
  }
]
```

Then run without `--offline`:

```bash
python3 -m job_source_agent --input my_jobs.json --output results.json --trace-output trace.json
```

There is also a live smoke-test input:

```bash
python3 -m job_source_agent \
  --input samples/live_examples.json \
  --output live-results.json \
  --trace-output live-trace.json
```

On July 10, 2026, this successfully found live job-list/opening pages for Ekimetrics, PostHog, and Anthropic:

```text
OK Ekimetrics
  career: https://www.ekimetrics.com/join-ekimetrics
  job list: https://jobs.lever.co/ekimetrics
OK PostHog
  career: https://posthog.com/careers/jobs
  opening: https://posthog.com/careers/ai-research-engineer
OK Anthropic
  career: https://www.anthropic.com/careers/jobs
  opening: https://job-boards.greenhouse.io/anthropic/jobs/5271428008
```

## Batch Live Evaluation

Before running noisy live tests, run the fixed offline benchmark. It verifies the provider adapter set against deterministic fixtures and writes a funnel summary:

```bash
python3 scripts/benchmark_eval.py \
  --output /tmp/benchmark-results.json \
  --trace-output /tmp/benchmark-trace.json \
  --summary-output /tmp/benchmark-summary.json
```

Expected summary:

```text
benchmark summary:
  total: 15
  success: 15
  with_job_list: 15
  with_opening: 15
  expectations: 15/15 passed
```

The companion [benchmark expectations](samples/benchmark_expectations.json) declares the provider, minimum successful stage, and whether an exact opening is required for each fixture. The evaluator exits nonzero if a declared expectation regresses.

Pass `--baseline-summary previous-summary.json` to either evaluator to add rate, pipeline-status, and per-stage success deltas to its summary. Each stage also reports duration count, P50, and P95 in milliseconds.

Archive summaries into an atomic, content-addressed history and compare each run with the latest baseline:

```bash
python3 scripts/archive_evaluation.py \
  --summary /tmp/live-batch-summary.json \
  --history-dir /tmp/job-source-evaluation-history \
  --label "live-46" \
  --benchmark-command "python3 scripts/live_batch_eval.py --input samples/live_benchmark_companies.json --workers 4"
```

Run the independent offline website resolver benchmark for inputs that provide only a company name and LinkedIn company URL:

```bash
python3 scripts/resolver_benchmark.py \
  --output /tmp/resolver-benchmark-results.json
```

Its six fixed cases cover short names, non-`.com` domains, canonical migration, parent-domain rejection, and a negative no-selection case.

Render a human-readable Markdown report from any evaluator summary:

```bash
python3 scripts/render_summary_report.py \
  --summary /tmp/benchmark-summary.json \
  --output /tmp/benchmark-report.md \
  --title "Offline Benchmark Report"
```

The report includes overview rates, the S1-S7 stage funnel, provider-by-stage reliability, provider reason-code counts, expectation results, and a company-by-stage matrix for quick review.

For larger live checks, use the checkpointing evaluator instead of one long CLI run. It writes results after every company, so a slow or blocked website does not erase earlier progress:

```bash
python3 scripts/live_batch_eval.py \
  --linkedin-keywords "Product Manager" \
  --linkedin-location "United States" \
  --limit 10 \
  --linkedin-pages 1 \
  --fetch-timeout 2 \
  --career-search-timeout 7 \
  --max-career-search-queries 5 \
  --verify-limit 3 \
  --max-career-candidates 5 \
  --max-career-fetches 5 \
  --max-ats-board-fetches 5 \
  --max-job-pages 2 \
  --company-time-budget 45 \
  --website-time-budget 20 \
  --checkpoint-dir /tmp/product10-stage-checkpoints \
  --batch-checkpoint-dir /tmp/product10-company-completions \
  --render-js \
  --render-budget 2 \
  --skip-sitemap \
  --output /tmp/product10-fast-results.json \
  --trace-output /tmp/product10-fast-trace.json \
  --summary-output /tmp/product10-fast-summary.json
```

The live runner executes S1-S3 and S4-S7 in separate killable processes while both phases use the same `PipelineApplication` and filesystem stage store. Add `--rerun-stage opening_match` to invalidate and recompute that stage for every company without repeating compatible upstream work. `--fixtures-dir ... --offline` runs the same two-phase path deterministically.

Each completed company is also published as a versioned atomic envelope. Restarting the same command restores compatible envelopes and submits only unfinished companies; final results and traces are rebuilt in original input order. Use `--no-resume` for a clean batch. Any `--rerun-stage` request bypasses company-level completion reuse while retaining stage-level checkpoint semantics.

If the optional browser dependency is not installed, omit `--render-js` and `--render-budget`.

Latest live checks on July 12, 2026:

- `Product Manager`, first LinkedIn page: 8 unique companies, 8/8 official websites, 6/8 official job-list pages, 1/8 exact opening.
- `Data Analyst`, first LinkedIn page after fast-domain and ATS-root routing improvements: 9 unique companies, 9/9 official websites, 8/9 official job-list pages, 1/9 exact opening. The remaining failure was a consulting/intermediary posting that exhausted the company budget after website resolution.
- Fixed live benchmark: 6 named companies, 6/6 official websites, 6/6 official job-list pages, 1/6 exact opening, and 6/6 expectation checks passed. Providers covered in that small set are Greenhouse, Lever, Ashby, PostHog's first-party careers page, and Brex's first-party careers page.
- July 12 rerun after the stage-runner migration: 6/6 official websites, 6/6 job-list pages, 5/6 exact openings, and 6/6 expectation checks. Provider attribution now follows stage evidence, so Greenhouse roles with an external CareerPuck apply URL remain classified as Greenhouse.
- Expanded July 12 fixed live benchmark: 9/9 official websites, 9/9 job-list pages, 7/9 exact openings, and 9/9 expectation checks in 17.6 seconds. The added samples cover SanDisk/SmartRecruiters, ONEOK/Workday, and Carv/Rippling.
- Current fixed live benchmark: 51/51 official websites, 51/51 career/job-list pages, 50/51 exact openings, and 51/51 expectation checks. The clean `.27` four-worker run completed in 97.5 seconds with zero restored companies. Greenhouse, Ashby, Lever, Workday, SmartRecruiters, Workable, Rippling, BambooHR, iCIMS, and SuccessFactors each have five fixed live companies.
- July 13 exploratory LinkedIn batch: 19 unique companies, 14/19 official job-list pages, and 6/19 exact openings. Preserving completed S1-S3 evidence showed that the next dominant cluster is hidden ATS/list-root discovery and structured job-card association, rather than website resolution alone.
- July 13 focused replay after that cluster: Plaid parent-card extraction and Snowflake Phenom structured state both reached exact openings. Follow-up provenance-aware root validation and verified ATS fallback separately reached exact openings for Glean, Reddit, Zillow, and Twitch. Uber remains on its first-party public list and Starbucks routes toward Eightfold, but their traced Seattle/Nashville titles are not currently confirmed; Zillow search also remains network-sensitive in the combined batch.
- July 13 S5 traversal replay: seven former generic opening misses were first reclassified as 0/7 verified job lists under the stricter evidence gate, then improved to 3/7 job lists and 1/7 exact opening. Epistemix now canonicalizes its Ashby embed and matches the exact AI Engineer role; Quest Global and Viking reach locale-preserving Phenom search-results pages.
- July 13 Phenom provider replay: both Quest Global and Viking are identified from customer-owned page evidence. Quest Global's SSR keyword inventory resolves the exact Agentic AI Engineer opening; Viking returns a verified title-filtered no-match without claiming that its entire public inventory is empty. The deterministic provider benchmark now covers 14/14 exact openings.
- July 13 Paycom provider replay: ReturnPro traverses from its first-party careers page to the canonical Paycom portal and resolves the live AI/ML Engineer detail URL. The adapter uses Paycom's public portal-session API with bounded title-filtered pagination, tenant/redirect validation, and token-free trace output. The deterministic provider benchmark now covers 15/15 exact openings; 473 tests, the 13-adapter architecture gate, 6/6 resolver benchmark, and checkpointed 51/51 fixed live expectations also pass.
- July 13 Lever embed replay: first-party pages that configure `leverJobsOptions.accountName` beside a Lever embed now derive a verified board ahead of the generic link budget. Influur resolves from its Webflow careers page to the live Lever AI Engineer detail URL; 475 tests and the clean `.25` 51-company fixed live gate pass.
- July 13 career-taxonomy replay: bounded first-party traversal prioritizes staff/business-services/professional audience pages and accepts only explicit jobs/careers subdomain portals on the same registrable company domain. Kirkland now reaches its official U.S. staff jobs portal while correctly leaving `AI Engineer II` unconfirmed; 477 tests and the clean `.26` 51-company fixed live gate pass.
- July 13 fresh LinkedIn `AI Engineer` batch under `.26`: 25 unique companies, 21/25 websites, 17/25 career pages, 11/25 verified job lists, and 9/25 exact openings. The six S5 misses split into RippleHire (Mphasis), Taleo (Kforce), a Netflix-owned portal, hidden first-party data (Nuro/Melotech), and Nashville's application-only page; this replaces the exhausted seven-company cluster as the next prioritization baseline.
- July 13 RippleHire provider replay: Mphasis now traverses from its current first-party careers page to the canonical RippleHire board and reads the public candidate inventory through an anonymous cookie session. The stale LinkedIn title is not present in the 91 filtered candidates, so the result correctly remains `OPENING_NOT_FOUND` while S5 improves from no board to a verified job list. Redirect snapshots now materialize request-URL aliases and reproduce the same result offline. The deterministic provider benchmark is 16/16; 487 tests, 14-adapter architecture validation, 6/6 resolver benchmark, and clean `.27` 51/51 fixed live expectations pass.
- July 13 Taleo provider replay: Kforce now traverses from its first-party Careers at Kforce page to the custom-domain Taleo FacetedSearch board. The adapter validates shell `portalNo/urlCode`, uses the anonymous REST inventory with bounded 25-item pagination, and rebuilds same-tenant detail URLs without sending or tracing the page CSRF token. `AI Engineer` is absent from the current official filtered inventory, so Kforce correctly reaches S5 and returns `OPENING_NOT_FOUND`. The sanitized eight-record capture replays the same result offline in 0.3 seconds. The provider benchmark is 17/17; 495 tests, 15-adapter architecture validation, and 6/6 resolver pass.
- The `.28` fixed-live release gate ran twice with 51/51 verified job lists. Rotating provider timeouts produced 49/51 and 48/51 exact openings in the two full runs; Peraton, then Harvey and Datadog, immediately recovered to exact openings in focused 1/1 and 2/2 reruns. Expectations remain strict. Ashby no longer converts an API failure plus an empty embedded fallback into a false `NO_PUBLIC_OPENINGS`; that evidence is now retryable `discovery_incomplete`.
- July 13 Eightfold provider replay: Netflix now follows the official `VIEW ROLES` link from `jobs.netflix.com` to its customer-owned Eightfold portal and resolves the exact Full-Stack AI Platform opening. The adapter validates `smartApplyData` tenant/PCS evidence, reads filtered SSR inventory, and uses the public bounded jobs API only when another page is needed; hosted tenant slugs are resolved against verified page state. Explicit cross-site job commands are probe-only until native provider evidence verifies the destination. Meta `_csrf` values are sanitized from captures; the accepted eight-record capture replays from three fixtures in 0.2 seconds. The provider benchmark is 18/18; 506 tests, 16-adapter architecture validation, and 6/6 resolver pass. The clean `.29` live gate kept 51/51 job lists and 50/51 expectations; its only strict miss was an iCIMS timeout for Ardent Health, which immediately recovered exact in a focused rerun.
- July 13 Greenhouse Nuxt replay: a reusable page-probe adapter extension lets opaque first-party career pages verify a bounded same-origin public payload. Nuro now reads 91 Greenhouse-shaped records from its Nuxt static payload and resolves the exact AI Platform new-grad opening, while payload redirects and non-`www` cross-origin job URLs remain rejected. The ten-record sanitized capture becomes three fixtures and replays the full result offline in 0.3 seconds. The `.30` gates pass 508 tests, 18/18 provider cases, 6/6 resolver cases, and the 16-adapter architecture validator. The resumed 51-company live gate finished with 51/51 job lists, 50/51 exact openings, and 51/51 expectations.
- July 13 bounded discovery and replay: sitemap indexes can schedule at most ten files per company and report truncated fan-out. Snapshot fixtures now fingerprint sanitized query parameters, so provider pagination pages remain distinct, and redirect aliases retain their own verified immutable response. Cisco's existing Phenom integration replays all five filtered pages with the same 50 candidates and honestly reports that the stale LinkedIn title is not present. The `.31` gates pass 513 tests, 18/18 provider cases, 6/6 resolver cases, and the 16-adapter architecture validator. Homebrew Python 3.14.2 repeatedly terminated long live processes natively; crash-safe completion recovery preserved results, and the three strict fragmented-run misses recovered 3/3 exact in a clean focused run. Use a stable supported Python runtime for release automation.
- July 13 LinkedIn cohort manifests: dynamic batch discovery is atomically frozen before downstream work, keyed by an independent LinkedIn discovery contract and exact search parameters. Reconnecting a real three-company smoke restored 3/3 completions without another public search. A new stable 30-company AI Engineer cohort completed across multiple PTY reconnects without changing membership: 27 websites, 17 career pages, 14 verified job lists, and 11 exact openings. These deliberately unpolished rates are the current improvement baseline. The gates pass 521 tests, 18/18 provider cases, 6/6 resolver cases, and the 16-adapter architecture validator.
- July 13 JazzHR replay: `*.applytojob.com/apply/jobs` is now a native provider boundary with canonical tenant boards, public full-inventory HTML parsing, exact-title evidence, and strict same-tenant redirect/detail validation. Waltonen traverses from its first-party careers page to JazzHR and resolves the live `AI Programmer` detail in 8.0 seconds. The four-record sanitized capture becomes three fixtures and replays the same result offline in 0.2 seconds. The `.32` gates pass 527 tests, 19/19 provider cases, 6/6 resolver cases, and the 17-adapter architecture validator.
- July 13 regional resolver and Avature replay: S2 now passes the LinkedIn job location into website resolution, rejects verified regional redirects that conflict with a U.S. posting, and performs a bounded same-host U.S. root recovery before relying on noisy search results. S4 keeps same-domain sitemap and navigation candidates aligned with the verified homepage locale, rejects cross-region sitemap wins, avoids homepage self-links, and prefers the general careers root over mismatched executive/student channels. The new page-aware Avature adapter verifies `avature.portal.*` metadata plus same-host search-route evidence, runs title-filtered `SearchJobs`, and accepts only same-portal numeric `JobDetail` links. Deloitte now resolves from Grand Rapids to the U.S. site and exact live job `355577`; the eight-record capture replays exact offline in 0.3 seconds. A full live with global sitemap discovery also passes exact in 44.1 seconds. The `.33` gates pass 538 tests, 20/20 provider cases, 6/6 resolver cases, and the 18-adapter architecture validator.
- July 13 ambiguous-brand resolver replay: for a short company name, an exact LinkedIn company-slug/domain-label match with a verified homepage is now a strong identity anchor only when the slug adds real disambiguating text. This keeps ordinary exact-brand and regional scoring unchanged while preventing a speculative short domain from beating the LinkedIn-linked organization. Finch now resolves from the wrong `finch.com` marketing company to `finchlegal.com`, follows its official careers page to Ashby, and matches the exact Machine Learning Engineer opening. The eight-record live capture materializes as seven fixtures and replays the full chain offline in 0.2 seconds. The `.34` gates pass 539 tests, 20/20 provider cases, 6/6 resolver cases, and the 18-adapter architecture validator.
- July 13 abbreviated-brand and hidden-provider replay: S2 rejects Salesforce Experience Cloud and link-shortener hosts as company homepages both before and after redirects. Multiword brands can generate a constrained initials-plus-final-token abbreviation, and that candidate is accepted only when the verified homepage title repeats the same abbreviation; LinkedIn `-ai`, `-app`, and `-tech` slug suffixes can expose the underlying candidate. S5 now promotes an unlabelled ATS detail URL embedded in an official first-party careers payload to its native canonical board before generic detail handling. Standard Template Labs moves from a false `l.ink` homepage to `stlabs.com`, its official careers page, the `st-labs` Ashby board, and the exact AI Engineer opening. Five live records materialize as three fixtures and replay exact offline in 0.3 seconds. The `.35` gates pass 543 tests, 20/20 provider cases, 6/6 resolver cases, and the 18-adapter architecture validator.
- July 13 External Apply fallback: a trusted browser or saved-page extractor can pass LinkedIn's off-site Apply target through `external_apply_url`. S5 accepts it only when the provider registry can derive a supported native board, and S6 still verifies the public inventory and title. ModMed succeeds even though website resolution produces no result: the Workday `ModMed12` board resolves the exact Machine Learning Engineer `R4352` opening in 11.4 seconds live, and the five-record sanitized snapshot reproduces it in 0.2 seconds offline. The `.36` gates pass 550 tests, 20/20 provider cases, 6/6 resolver cases, and the 18-adapter architecture validator.
- July 13 local extension bridge: the Manifest V3 popup scans up to 30 visible LinkedIn Jobs records and submits them to a token-protected loopback run manager. A real HTTP smoke returned health `200`, submission `202`, then persisted a completed 1/1 job-list and 1/1 exact-opening fixture run. The release gates pass 556 tests, 20/20 provider cases, 6/6 resolver cases, and the 18-adapter architecture validator. Installation and one authenticated LinkedIn DOM scan remain the explicit manual browser acceptance step.
- Fixed JS-heavy browser cohort: five companies across five providers and five technologies (Plum, Meta, Apple Jobs, Spotify, and IIC Lakshya). The strict saved/live evidence gate requires a successful render event, structured selector evidence, optional expected URL, sufficient visible text, no loading state, and no final classified error. Saved replay and the 15-second live gate both pass 5/5 within the shared render budget; Meta exercises static HTTP 400 to browser fallback, and Meta, Apple, and IIC require exact job URLs.

The live evaluator intentionally reports exact openings separately from job-list success. For many websites, the reliable product outcome is the official job board plus trace evidence; exact job-detail matching is only marked `success` when the LinkedIn title can be matched confidently.

Run the deterministic JS-heavy contract cohort without browser dependencies, or add `--live` after installing the optional Playwright dependency:

```bash
python3 scripts/js_heavy_cohort_eval.py --output /tmp/js-heavy-contract.json
python3 scripts/js_heavy_cohort_eval.py --live --timeout 15 --output /tmp/js-heavy-live.json
```

The command exits nonzero unless every case triggers browser rendering, passes the same strict evidence gate, and stays within the shared render budget. The summary records render outcome, error class, visible-text length, selector/URL matches, forbidden loading evidence, and per-case pass status.

To avoid relying only on LinkedIn's current random search results, run the fixed live benchmark set:

```bash
python3 scripts/live_batch_eval.py \
  --input samples/live_benchmark_companies.json \
  --expectations samples/live_benchmark_expectations.json \
  --limit 51 \
  --fetch-timeout 5 \
  --career-search-timeout 7 \
  --company-time-budget 45 \
  --website-time-budget 20 \
  --fetch-retries 1 \
  --retry-base-delay 0.25 \
  --workers 2 \
  --skip-sitemap \
  --output /tmp/live-fixed-results.json \
  --trace-output /tmp/live-fixed-trace.json \
  --summary-output /tmp/live-fixed-summary.json
```

Add `--snapshot-dir /tmp/job-source-snapshots --failure-bundle-dir /tmp/job-source-failures` to make the batch automatically select up to 20 partial, failed, or unsupported records and execute them as an offline replay bundle. Use `--failure-bundle-limit N` to change that bound. The final summary links the bundle manifest; a fully green run writes a `status: skipped` manifest instead of treating the absence of failures as an error.

To turn a prior run into a focused replay input, export the subset you want to investigate:

```bash
python3 scripts/export_replay_input.py \
  --input /tmp/live-fixed-results.json \
  --output /tmp/live-fixed-opening-misses.json \
  --stage opening_match \
  --stage-status partial \
  --reason-code OPENING_NOT_FOUND
```

The exported records preserve the verified website, career root, LinkedIn title, and replay metadata, so the next run can start from known-good upstream evidence instead of rediscovering everything. Each replay record also includes checkpoint metadata with schema versions, adapter version, and a stable input fingerprint for later resume/cache compatibility checks.

Validate replay compatibility before reusing an old replay file:

```bash
python3 scripts/validate_replay_input.py \
  --input /tmp/live-fixed-opening-misses.json \
  --summary-output /tmp/live-fixed-opening-misses-validation.json
```

Then resume from known-good upstream evidence:

```bash
python3 scripts/live_batch_eval.py \
  --input /tmp/live-fixed-opening-misses.json \
  --resume-from-stage opening_match \
  --output /tmp/live-fixed-rerun-results.json \
  --trace-output /tmp/live-fixed-rerun-trace.json \
  --summary-output /tmp/live-fixed-rerun-summary.json
```

To capture sanitized page snapshots while running a live batch, add `--snapshot-dir`:

```bash
python3 scripts/live_batch_eval.py \
  --input samples/live_benchmark_companies.json \
  --expectations samples/live_benchmark_expectations.json \
  --render-js \
  --render-screenshot \
  --snapshot-dir /tmp/job-source-snapshots \
  --output /tmp/live-fixed-results.json \
  --trace-output /tmp/live-fixed-trace.json \
  --summary-output /tmp/live-fixed-summary.json
```

Convert a completed snapshot set into verified deterministic fixtures:

```bash
python3 scripts/replay_snapshots.py \
  --snapshot-dir /tmp/job-source-snapshots \
  --output-dir /tmp/job-source-replay

python3 -m job_source_agent \
  --input samples/live_benchmark_companies.json \
  --fixtures-dir /tmp/job-source-replay/sites \
  --offline \
  --output /tmp/replay-results.json \
  --trace-output /tmp/replay-trace.json
```

Replay conversion verifies metadata, hashes, byte counts, URL sanitization and path containment before copying files. Snapshot bodies and browser artifacts are also stored as immutable content-addressed blobs, so repeated requests that share a fixture path cannot invalidate earlier manifest hashes. Replay selects the last complete version for each fixture path, reports identical duplicates and superseded versions separately, and rejects missing artifacts, symlink/path escapes or a canonical view that does not match the selected blob.

Build and execute a focused offline bundle directly from failed results and their snapshots:

```bash
python3 scripts/replay_failure_bundle.py \
  --results /tmp/live-fixed-trace.json \
  --snapshot-dir /tmp/job-source-snapshots \
  --output-dir /tmp/opening-failure-bundle \
  --stage opening_match \
  --stage-status partial \
  --reason-code OPENING_NOT_FOUND
```

The bundle contains filtered replay input, verified fixtures, stage checkpoints, offline results/trace/summary and a relative-path manifest. Summary reports also include checkpoint save/restore/miss/invalidate activity.

Snapshots are written under `/tmp/job-source-snapshots/sites` using the same layout as offline fixtures, plus `/tmp/job-source-snapshots/snapshots.jsonl` metadata. Rendered screenshots are written under `/tmp/job-source-snapshots/artifacts` and referenced from the same metadata file. Sensitive query values and common token-like values are redacted before writing.

`--fetch-retries` retries only retryable fetch failures such as timeouts, DNS failures, rate limits, and server errors. Non-retryable external blockers such as HTTP 403, login walls, bot protection, and parser/title-match failures are not retried.

`--workers` processes multiple companies concurrently while preserving per-company hard budgets and checkpoint writes after each completed company. Keep it small, for example 2-4, for live websites.

## Optional Saved LinkedIn HTML Input

If you save a LinkedIn job page HTML locally, a record may provide `linkedin_html_path`. The parser can extract company identity and an explicitly labeled company website from the saved HTML:

```json
[
  {
    "linkedin_job_url": "https://www.linkedin.com/jobs/view/example",
    "linkedin_html_path": "samples/linkedin_saved_job.html"
  }
]
```

The parser does not treat arbitrary external, apply, tracking, CDN, or ATS links as the company website. If LinkedIn does not expose a labeled website field, provide `company_website_url` explicitly or let the verified resolver use the LinkedIn company URL.

## Architecture

```text
LinkedIn extractor adapter
        |
        v
Hiring company discovery
  - public LinkedIn jobs search
  - company name, job URL, LinkedIn company URL
        |
        v
Official website resolver
  - fast verified domain candidates from company name and LinkedIn slug
  - LinkedIn company page website signals
  - LinkedIn company slug TLD hints
  - Bing RSS / HTML and DuckDuckGo fallback search
  - optional overrides
  - canonical homepage verification and false-positive rejection
        |
        v
Company identity resolver
  - brand-to-hiring-entity mapping
  - Instagram/WhatsApp/Threads -> Meta Careers
  - YouTube/Google -> Google Careers
  - selected high-signal career roots such as Notion, Netflix, Hudl, Snap, Roku, Home Depot, Brex, and Lyft
        |
        v
Company website fetcher
        |
        v
Career page finder
  - homepage link extraction
  - common path probing
  - brand-style join path probing, for example /join-{brand}
- search fallback for "{company} careers jobs" when common paths fail
- bounded ATS board probes for common providers when website discovery fails
- sitemap and robots sitemap discovery
- ATS domain detection
- Rippling public job-board detection and title matching
  - scored candidates with reasons
        |
        v
Job-list/opening finder
  - auto-discovered native adapters for Greenhouse, Lever, SmartRecruiters, Workday, Ashby, BambooHR, iCIMS, SuccessFactors, Workable, Rippling, and Google Careers
  - Workday CXS jobs API adapter with title search payloads
  - structured JSON-LD / embedded JSON extraction for iCIMS, SuccessFactors, and Workable-style pages
  - native server-rendered search for Google Careers and provider-aware compatibility search for Meta Careers
  - provider adapters for Lever, Greenhouse, Ashby, Workable, SmartRecruiters, iCIMS, Workday, and SuccessFactors-style systems
  - target-title matching from LinkedIn job cards
  - job-detail path scoring
  - multi-hop traversal from career page to ATS/listing page to job detail
  - provider-specific job-detail patterns
  - negative filters for privacy/blog/benefits pages
        |
        v
results.json + trace.json
```

## Design Choices

- LinkedIn extraction is adapter-based because production LinkedIn crawling typically needs login/session handling or a third-party crawler API.
- The LinkedIn public jobs mode discovers hiring companies directly from LinkedIn guest job-search cards.
- Public guest job HTML does not reliably reveal the off-site Apply target; authenticated browser/saved-page integrations may supply `external_apply_url`, which must pass native provider and inventory validation.
- Brand identity is resolved before website crawling so product brands can route to parent hiring systems.
- Official website resolution first verifies high-confidence domain candidates, then falls back to LinkedIn page signals and search. This keeps obvious domains such as `lyft.com` and `brex.com` fast while still rejecting unverified guesses.
- LinkedIn company slugs can break domain ties, for example `tesseralabsai` favoring `tesseralabs.ai` over `tesseralabs.com`.
- Derived ATS boards must be verified by a provider API response or concrete job-detail evidence before they can count as successful.
- Career-page discovery uses deterministic scoring before any expensive browser/LLM-style behavior.
- Career-page discovery combines homepage links, common path probes, brand-specific join paths, and sitemap URLs.
- When direct navigation fails, career-page discovery can fall back to search results while preserving full career/job paths.
- Common ATS providers such as Lever, Greenhouse, Ashby, Workable, SmartRecruiters, iCIMS, Workday, SuccessFactors, and Rippling are recognized explicitly.
- Greenhouse, Lever, SmartRecruiters, Workday, Ashby, and BambooHR use native structured API adapters before falling back to HTML link extraction.
- iCIMS, SuccessFactors, and Workable use native structured-page adapters for JSON-LD, embedded application JSON, or verified job links.
- Rippling uses a native structured-page adapter that merges verified same-tenant anchors with Next.js job state while preserving location and department metadata.
- Provider-specific matchers build provider-appropriate search URLs and preserve stable job-board fallbacks when a concrete title match is not available.
- Concrete opening selection is gated by the LinkedIn target title to avoid false-positive job URLs.
- Error and 404 pages are rejected even if their URL or HTML contains career-like keywords.
- The agent distinguishes listing pages, such as `/careers/jobs`, from concrete job-detail URLs.
- Social/job aggregator links, static assets, and ATS embeds are filtered out as false positives.
- Every decision is traceable through scored candidates and reasons.
- Failures are structured with standard reason codes, for example `CAREER_PAGE_NOT_FOUND`, `OPENING_NOT_FOUND`, `HTTP_FORBIDDEN`, or `COMPANY_TIME_BUDGET_EXHAUSTED`.
- The live batch runner uses per-company checkpoints and process-level deadlines so one blocked website does not lose previous results.

## Tests

```bash
python3 -m unittest discover -s tests
python3 scripts/validate_architecture.py
```

## Next Production Steps

- Use `--render-js` for heavily JavaScript-driven websites or bot-protected pages.
- Build a fixed live benchmark set by provider and company type instead of relying only on LinkedIn's current random search results.
- Add an LLM reranker only for ambiguous candidate sets.
- Store screenshots and final HTML snapshots for auditability.
