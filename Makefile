PYTHON ?= python3.12
REVIEWER_HOST ?= 127.0.0.1
REVIEWER_PORT ?= 8765

.PHONY: runtime test offline-gates beta-demo extension-bridge reviewer-start reviewer-check beta-package live-gate

runtime:
	$(PYTHON) scripts/check_runtime.py --release

test: runtime
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests

offline-gates: test
	$(PYTHON) scripts/benchmark_eval.py
	$(PYTHON) scripts/resolver_benchmark.py
	$(PYTHON) scripts/validate_architecture.py

beta-demo: runtime
	mkdir -p /tmp/ai-job-source-agent-beta-demo
	$(PYTHON) -m job_source_agent \
		--input samples/linkedin_jobs.json \
		--fixtures-dir samples/sites \
		--offline \
		--output /tmp/ai-job-source-agent-beta-demo/results.json \
		--trace-output /tmp/ai-job-source-agent-beta-demo/trace.json

extension-bridge: runtime
	$(PYTHON) -m scripts.extension_bridge \
		--port 8765 \
		--workers 4 \
		--fetch-timeout 8

reviewer-start:
	$(PYTHON) scripts/reviewer_start.py \
		--host $(REVIEWER_HOST) \
		--port $(REVIEWER_PORT)

reviewer-check:
	$(PYTHON) scripts/reviewer_start.py \
		--host $(REVIEWER_HOST) \
		--port $(REVIEWER_PORT) \
		--check-only

beta-package: runtime
	$(PYTHON) scripts/build_beta_release.py --output-dir dist

live-gate: runtime
	$(PYTHON) scripts/live_batch_eval.py \
		--input samples/live_benchmark_companies.json \
		--expectations samples/live_benchmark_expectations.json \
		--limit 51 \
		--fetch-timeout 5 \
		--career-search-timeout 7 \
		--company-time-budget 45 \
		--website-time-budget 20 \
		--fetch-retries 1 \
		--retry-base-delay 0.25 \
		--workers 4 \
		--skip-sitemap \
		--output /tmp/live-fixed-release-results.json \
		--trace-output /tmp/live-fixed-release-trace.json \
		--summary-output /tmp/live-fixed-release-summary.json \
		--checkpoint-dir /tmp/live-fixed-release-checkpoints \
		--batch-checkpoint-dir /tmp/live-fixed-release-batch \
		--no-resume
