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

## Video Walkthrough Outline

1. State the goal: given LinkedIn job-source records, return company name, career page URL, and one open position URL.
2. Explain the LinkedIn boundary: direct LinkedIn crawling is isolated behind an adapter because real LinkedIn extraction often requires logged-in sessions or a third-party crawler API.
3. Run the deterministic demo command to show the stable fixture-based flow.
4. Run the live command to show that the same agent works against real company websites.
5. Open `results.json` or `live-results.json` and show the required output format.
6. Open the trace file and show how the agent scored career-page and job-opening candidates.
7. Mention production next steps: third-party LinkedIn crawler adapter, Playwright for JavaScript-heavy sites, and an LLM reranker for ambiguous pages.

## Key Talking Points

- The agent is controlled and explainable rather than a free-form browser clicker.
- Rules and ATS patterns handle common cases cheaply and reliably.
- The live examples prove the company-website-to-opening pipeline works beyond fixtures.
- Trace output makes failures debuggable.
- Offline fixtures keep the demo stable while the same fetcher can run against live websites.
