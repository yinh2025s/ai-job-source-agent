PYTHON ?= python3.12

.PHONY: runtime test offline-gates live-gate

runtime:
	$(PYTHON) scripts/check_runtime.py --release

test: runtime
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests

offline-gates: test
	$(PYTHON) scripts/benchmark_eval.py
	$(PYTHON) scripts/resolver_benchmark.py
	$(PYTHON) scripts/validate_architecture.py

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
