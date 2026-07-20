# Fresh 100 `.188` Cold-Start Run Manifest

- Run date: 2026-07-20 Asia/Shanghai
- Code commit: `ed4c9343ec382387542d7b917050acbc04096dda`
- Git tag: `frozen100-v188`
- Adapter version: `2026-07-20.188`
- Cohort: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Cohort SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Cohort identity: 100 unique LinkedIn job IDs, 95 companies, observed 2026-07-18
- Resume policy: disabled (`--no-resume`)
- Evidence policy: new empty run-local evidence store
- Isolation: all checkpoint, completion, snapshot, replay and output paths are below this directory

Frozen deterministic agent configuration:

- fetch timeout: 8 seconds
- fetch retries: 1
- retry base delay: 0.25 seconds
- career search timeout: 6 seconds
- career search queries: 5
- career candidates: 6
- career fetches: 5
- career transport calls: 32
- ATS board fetches: 5
- job pages: 8
- job board attempts: 3
- parallel candidate discovery: enabled
- company time budget: 120 seconds
- website time budget: 25 seconds
- company workers: 4

The code is frozen for the full live and replay gates. No output from this run
may modify the frozen-100 archive or its 69/100 result.
