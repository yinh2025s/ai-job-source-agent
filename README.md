# AI Job Source Agent

Take-home implementation for Part 2: starting from LinkedIn job-source records, discover the company career page and one open position URL.

The implementation is intentionally agentic but controlled: deterministic link extraction and scoring do the first pass, then the agent follows promising career/job-listing pages for a few hops. The pipeline keeps trace data for every navigation decision.

LinkedIn extraction is isolated behind an adapter-shaped input because direct LinkedIn scraping is brittle and commonly blocked. In real usage, the adapter can be fed by a third-party LinkedIn crawler API, a saved LinkedIn HTML page, or a manually verified company URL.

## What It Returns

For each input record:

```json
{
  "company_name": "Aurora Data",
  "company_website_url": "https://aurora-data.example",
  "career_page_url": "https://jobs.lever.co/aurora-data",
  "open_position_url": "https://jobs.lever.co/aurora-data/d9d64766-3d42-4ba9-94d4-f74cdaf20065",
  "status": "success",
  "error": null
}
```

The CLI also writes `trace.json`, which includes candidate links, scores, reasons, and the selected page source.

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
  career: https://jobs.lever.co/aurora-data
  opening: https://jobs.lever.co/aurora-data/d9d64766-3d42-4ba9-94d4-f74cdaf20065
OK Nimbus Robotics
  career: https://nimbus-robotics.example/careers
  opening: https://boards.greenhouse.io/nimbusrobotics/jobs/5012345001
```

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

On June 22, 2026, this successfully found openings for Ekimetrics, PostHog, and Anthropic.

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
Company website fetcher
        |
        v
Career page finder
  - homepage link extraction
  - common path probing
  - ATS domain detection
  - scored candidates with reasons
        |
        v
Open position finder
  - job-detail path scoring
  - multi-hop traversal from career page to ATS/listing page to job detail
  - Lever/Greenhouse/Ashby-style ATS patterns
  - negative filters for privacy/blog/benefits pages
        |
        v
results.json + trace.json
```

## Design Choices

- LinkedIn extraction is adapter-based because production LinkedIn crawling typically needs login/session handling or a third-party crawler API.
- Career-page discovery uses deterministic scoring before any expensive browser/LLM-style behavior.
- Common ATS providers such as Lever and Greenhouse are recognized explicitly.
- The agent distinguishes listing pages, such as `/careers/jobs`, from concrete job-detail URLs.
- Every decision is traceable through scored candidates and reasons.
- Failures are structured, for example `career_page_not_found`, `open_position_not_found`, or `fetch_failed`.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Next Production Steps

- Add a third-party LinkedIn crawler adapter that emits the current JSON schema.
- Add Playwright rendering for heavily JavaScript-driven websites.
- Add an LLM reranker only for ambiguous candidate sets.
- Store screenshots and final HTML snapshots for auditability.
