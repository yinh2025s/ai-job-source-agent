# ADR-0038: Bound Extension Run Concurrency And Report Progress

Status: accepted

Date: 2026-08-01

## Context

The extension can submit up to 30 LinkedIn records in one run. The bridge
previously processed every company serially and reported only `running` until
the entire batch finished. The `--workers` option controlled concurrent whole
runs rather than records inside one run, so a 25-record batch could remain
visibly unchanged for several minutes. The popup could not distinguish useful
work from a stalled worker.

Allowing multiple full runs to execute together would increase shared network
pressure and complicate local evidence-store ownership. Publishing partial
results before their individual pipelines finish would also weaken the existing
result contract.

## Decision

The bridge serializes whole extension runs through one run executor. Within the
active run, it processes companies with bounded concurrency controlled by
`--workers`; the local reviewer command uses four company workers. Each company
gets an isolated `PipelineApplication`, while existing filesystem evidence
stores retain their process locks and atomic writes.

Run responses use `queued`, `running`, `complete`, or `failed` and include
integer `submitted` and `completed` counters. `completed` advances only after a
company pipeline returns and is monotonic within the run. Final results remain
ordered like the submitted records and are written only through the existing
terminal artifact contract. The popup validates the counters and renders, for
example, `Running 7/25`.

## Consequences

- one extension batch no longer waits for every prior company serially;
- the reviewer can see whether a long run is making progress;
- at most four company pipelines share the local network by default;
- two complete extension runs do not execute concurrently;
- provider, tenant, URL safety, identity continuity, and S7 behavior are
  unchanged;
- this decision does not provide hard process cancellation or a whole-run
  deadline. A future cancellation contract must use cooperative stage checks or
  isolated worker processes rather than pretending Python threads can be killed.

## Non-Goals

- increasing live benchmark concurrency;
- changing provider discovery or publication behavior;
- treating an External Apply button without a real target URL as a link;
- claiming that progress alone proves a run will finish.
