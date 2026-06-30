# Submission Notes

## Demo Command

```bash
python3 -m job_source_agent \
  --input samples/linkedin_jobs.json \
  --fixtures-dir samples/sites \
  --offline \
  --output results.json \
  --trace-output trace.json
```

## Live Command

```bash
python3 -m job_source_agent \
  --input samples/live_examples.json \
  --output live-results.json \
  --trace-output live-trace.json
```

## LinkedIn Discovery Command

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

## Video Walkthrough Outline

1. State the upgraded goal: discover hiring companies from LinkedIn, resolve their official websites, and navigate to the official career/job-list page.
2. Run the deterministic demo command to show the stable fixture-based flow.
3. Run the LinkedIn discovery command to show end-to-end discovery from public LinkedIn job results.
4. Open `linkedin-results.json` and point to `linkedin_job_url`, `company_website_url`, `career_page_url`, and `job_list_page_url`.
5. Open `linkedin-trace.json` and show the stages: LinkedIn job card, website resolution, career-page scoring, and job-list/opening traversal.
6. Mention production next steps: Playwright for JavaScript-heavy job lists, parallel website resolution, and an LLM reranker for ambiguous pages.

## Key Talking Points

- The agent is controlled and explainable rather than a free-form browser clicker.
- It now starts from LinkedIn public job-search cards, not only from pre-known company websites.
- The official website resolver uses LinkedIn company-page website signals first, then fallback strategies.
- Rules and ATS patterns handle common cases cheaply and reliably.
- The live examples prove the LinkedIn-to-official-careers pipeline works beyond fixtures.
- Trace output makes failures debuggable.
- Offline fixtures keep the demo stable while the same fetcher can run against live websites.
