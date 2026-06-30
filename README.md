# AI Job Source Agent

Take-home implementation for Part 2: discover companies hiring on LinkedIn, resolve their official websites, and navigate to their official career/job-list pages.

The implementation is intentionally agentic but controlled: deterministic link extraction and scoring do the first pass, then the agent follows promising career/job-listing pages for a few hops. The pipeline keeps trace data for every navigation decision.

The project supports two flows:

- `--linkedin-keywords`: search public LinkedIn job results, extract hiring companies, resolve official websites from LinkedIn company pages/search/domain hints, and find official job-list pages.
- `--input`: run the downstream website-to-careers pipeline from pre-extracted company records.

## What It Returns

For each input record:

```json
{
  "company_name": "Aurora Data",
  "company_website_url": "https://aurora-data.example",
  "linkedin_job_url": "https://www.linkedin.com/jobs/view/aurora-data-ai-engineer",
  "career_page_url": "https://jobs.lever.co/aurora-data",
  "job_list_page_url": "https://jobs.lever.co/aurora-data",
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

Example live output from June 30, 2026:

```text
OK Rolls-Royce
  linkedin job: Junior Machine Learning Engineer
  website: https://www.rolls-royce.com/
  career: https://www.rolls-royce.com/products-and-services/defence/submarines/careers-and-skills-in-rr-submarines.aspx
  job list: https://www.rolls-royce.com/products-and-services/defence/submarines/careers-and-skills-in-rr-submarines.aspx
  opening: None
OK Google
  linkedin job: Software Engineer, AI/ML, Google Research
  website: https://www.google.com/
  career: https://www.google.com/about/careers/applications/
  job list: https://www.google.com/about/careers/applications/
  opening: None
OK Nuro
  linkedin job: Software Engineer, AI Platform - New Grad
  website: https://www.nuro.ai
  career: https://www.nuro.ai/careers
  job list: https://www.nuro.ai/careers
  opening: None
```

Many modern career pages render the concrete job cards with JavaScript, so `open_position_url` may be `null` while `job_list_page_url` is still successful. The trace file records whether the agent reached an ATS page, a native careers page, or only the best official careers page.

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

On June 30, 2026, this successfully found live job-list/opening pages for Ekimetrics, PostHog, and Anthropic.

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
  - LinkedIn company page website signals
  - optional overrides
  - search/domain fallback
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
Job-list/opening finder
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
- The LinkedIn public jobs mode discovers hiring companies directly from LinkedIn guest job-search cards.
- Official website resolution prefers the LinkedIn company page's website signal before search/domain guessing.
- Career-page discovery uses deterministic scoring before any expensive browser/LLM-style behavior.
- Common ATS providers such as Lever and Greenhouse are recognized explicitly.
- The agent distinguishes listing pages, such as `/careers/jobs`, from concrete job-detail URLs.
- Social/job aggregator links, static assets, and ATS embeds are filtered out as false positives.
- Every decision is traceable through scored candidates and reasons.
- Failures are structured, for example `career_page_not_found`, `open_position_not_found`, or `fetch_failed`.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Next Production Steps

- Add Playwright rendering for heavily JavaScript-driven websites.
- Parallelize website resolution and career probing for larger LinkedIn searches.
- Add an LLM reranker only for ambiguous candidate sets.
- Store screenshots and final HTML snapshots for auditability.
