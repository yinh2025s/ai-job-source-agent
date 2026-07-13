# ADR-0008: Publish Budgeted Results Before The Deadline

## Status

Accepted, 2026-07-14.

## Context

The live runner executes each company in killable child processes. A child could finish its
pipeline and write valid snapshots or stage checkpoints just before the company deadline, then
block while serializing a large `DiscoveryResult` through a multiprocessing pipe. The parent
would report a timeout and terminate only the Python worker PID. Browser descendants could
survive and continue writing snapshots after the parent had published the timeout result.

Snapshot records had a related durability gap. Page, blob, artifact, and sequence files used
atomic rename but did not fsync their file and parent directory before the JSONL index record was
made durable. A crash could therefore leave durable metadata that referenced non-durable content.

## Decision

- A budget worker becomes the leader of a dedicated POSIX process group before running user
  code. Timeout and final cleanup signal the whole group with TERM and then KILL when required;
  non-POSIX platforms retain the direct-process fallback.
- Large return values no longer travel through the pipe. The child serializes the result into an
  attempt-local envelope, copies it to a destination-local temporary file, fsyncs it, atomically
  replaces the destination, fsyncs the destination directory, and only then records a shared
  monotonic publication timestamp. The pipe carries a small readiness notification only.
- The parent accepts an envelope only when its publication timestamp is nonzero and no later than
  the absolute deadline. A delayed or lost notification cannot discard a result that was already
  durably published before the deadline. Serialization or publication that finishes after the
  deadline remains a timeout.
- `AttemptArtifactTransaction` owns safe attempt paths and single-file publication mechanics. It
  rejects traversal and symlink escapes, copies across source filesystems into a destination-local
  temporary file, supports replace or require-identical policy, and never claims multi-file
  atomicity.
- Snapshot content-addressed blobs, canonical fixture views, artifacts, and sequence are fsynced
  before a page index record is appended. JSONL records are file-fsynced and followed by a parent
  directory fsync. Failed content publication may leave an unreferenced immutable blob, but never
  an index record that references missing content.
- Completed stage checkpoints remain canonical and reusable when a later stage or company attempt
  times out. They already represent validated stage boundaries and are keyed by execution
  configuration. The runner does not roll back useful upstream evidence.
- After a hard timeout, the parent loads checkpoints in canonical stage order using the same
  execution fingerprint and current schema/adapter versions. It accepts only a contiguous prefix
  whose stage results are `success` or `not_applicable`; corruption, incompatibility, a failed
  stage, or a missing checkpoint stops recovery. The first missing stage receives
  `COMPANY_TIME_BUDGET_EXHAUSTED`, later domain stages remain `not_run`, and already published
  website/career/job-list/provider evidence is retained. A complete S1-S7 prefix reconstructs the
  real result instead of publishing a false timeout.
- Company batch completion is the durable commit marker for a result and remains the last
  authoritative publication after child evidence is complete. Derived results, trace, and summary
  files are rebuilt only after that completion succeeds.

## Consequences

Deadline behavior is conservative and deterministic: only a fully serialized and durably
published result can win the deadline race. Browser descendants cannot continue mutating shared
snapshot roots after timeout. Snapshot replay may ignore harmless unreferenced blobs after a
crash, while every indexed record has durable content. Existing stage resume semantics are
preserved instead of trading reliability for whole-attempt rollback.

The parent never infers an unfinished stage from snapshots, later checkpoints, or child memory.
This keeps timeout recovery conservative while preventing a durable S5 success from being
misreported as an S4 failure.

The process envelope uses local pickle because both writer and reader are trusted processes from
the same invocation; it is never exposed as a replay or user-supplied artifact. The private
notification-delay hook exists only for deterministic race testing.
