# AI Job Source Agent

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

## What It Returns

For each input record:

```json
{
  "company_name": "Aurora Data",
  "company_website_url": "https://aurora-data.example",
  "linkedin_job_url": "https://www.linkedin.com/jobs/view/aurora-data-ai-engineer",
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
  total: 13
  success: 13
  with_job_list: 13
  with_opening: 13
  expectations: 13/13 passed
```

The companion [benchmark expectations](samples/benchmark_expectations.json) declares the provider, minimum successful stage, and whether an exact opening is required for each fixture. The evaluator exits nonzero if a declared expectation regresses.

Pass `--baseline-summary previous-summary.json` to either evaluator to add rate, pipeline-status, and per-stage success deltas to its summary. Each stage also reports duration count, P50, and P95 in milliseconds.

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

Latest live checks on July 11, 2026:

- `Product Manager`, first LinkedIn page: 8 unique companies, 8/8 official websites, 6/8 official job-list pages, 1/8 exact opening.
- `Data Analyst`, first LinkedIn page after fast-domain and ATS-root routing improvements: 9 unique companies, 9/9 official websites, 8/9 official job-list pages, 1/9 exact opening. The remaining failure was a consulting/intermediary posting that exhausted the company budget after website resolution.
- Fixed live benchmark: 6 named companies, 6/6 official websites, 6/6 official job-list pages, 1/6 exact opening, and 6/6 expectation checks passed. Providers covered in that small set are Greenhouse, Lever, Ashby, PostHog's first-party careers page, and Brex's first-party careers page.
- July 12 rerun after the stage-runner migration: 6/6 official websites, 6/6 job-list pages, 5/6 exact openings, and 6/6 expectation checks. Provider attribution now follows stage evidence, so Greenhouse roles with an external CareerPuck apply URL remain classified as Greenhouse.
- Expanded July 12 fixed live benchmark: 9/9 official websites, 9/9 job-list pages, 7/9 exact openings, and 9/9 expectation checks in 17.6 seconds. The added samples cover SanDisk/SmartRecruiters, ONEOK/Workday, and Carv/Rippling.
- Current fixed live benchmark: 46/46 official websites, 46/46 job-list pages, 45/46 exact openings, and 46/46 expectation checks in 66.8 seconds with four workers. Greenhouse, Ashby, Lever, Workday, SmartRecruiters, Workable, Rippling, and BambooHR each have five fixed live companies; iCIMS/SuccessFactors also has five combined samples spanning Jibe, traditional hosted HTML, and SAP Career Site v1.

The live evaluator intentionally reports exact openings separately from job-list success. For many websites, the reliable product outcome is the official job board plus trace evidence; exact job-detail matching is only marked `success` when the LinkedIn title can be matched confidently.

To avoid relying only on LinkedIn's current random search results, run the fixed live benchmark set:

```bash
python3 scripts/live_batch_eval.py \
  --input samples/live_benchmark_companies.json \
  --expectations samples/live_benchmark_expectations.json \
  --fetch-timeout 5 \
  --career-search-timeout 7 \
  --company-time-budget 45 \
  --website-time-budget 10 \
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

If you save a LinkedIn job page HTML locally, a record may provide `linkedin_html_path`. The parser will try to infer the company name and external website from the saved HTML:

```json
[
  {
    "linkedin_job_url": "https://www.linkedin.com/jobs/view/example",
    "linkedin_html_path": "samples/linkedin_saved_job.html"
  }
]
```

If LinkedIn does not expose the company website in the saved HTML, provide `company_website_url` explicitly or use a third-party crawler/API adapter.

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
  - auto-discovered native adapters for Greenhouse, Lever, SmartRecruiters, Workday, Ashby, BambooHR, iCIMS, SuccessFactors, Workable, and Rippling
  - Workday CXS jobs API adapter with title search payloads
  - structured JSON-LD / embedded JSON extraction for iCIMS, SuccessFactors, and Workable-style pages
  - provider-aware search URLs for Google Careers and Meta Careers
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
