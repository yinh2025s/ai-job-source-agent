#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse


SCHEMA_VERSION = "1.0"
SCAN_SUFFIXES = frozenset({
    ".csv", ".html", ".json", ".jsonl", ".md", ".py", ".toml", ".txt",
    ".xml", ".yaml", ".yml",
})
SKIP_PARTS = frozenset({".git", ".mypy_cache", ".pytest_cache", ".venv", "node_modules"})
COMPANY_KEYS = frozenset({"company", "company_name", "companyname"})
JOB_URL_KEYS = frozenset({"job_url", "linkedin_job_url", "linkedinjoburl"})


class BlindHoldoutError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze an unseen blind holdout after auditing historical artifacts."
    )
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--history-root", action="append", default=[])
    parser.add_argument("--history-cutoff", required=True)
    parser.add_argument("--run-config", required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--output-cohort", required=True)
    parser.add_argument("--output-manifest", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        cohort, manifest = prepare_holdout(
            candidates_path=Path(args.candidates),
            repo_root=Path(args.repo_root),
            history_roots=[Path(path) for path in args.history_root],
            history_cutoff=args.history_cutoff,
            run_config_path=Path(args.run_config),
            limit=args.limit,
            excluded_paths={
                Path(args.candidates),
                Path(args.output_cohort),
                Path(args.output_manifest),
            },
        )
    except (BlindHoldoutError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"blind holdout preparation failed: {error}") from error
    _write_json_atomic(Path(args.output_cohort), cohort)
    _write_json_atomic(Path(args.output_manifest), manifest)
    print(
        json.dumps(
            {
                "cohort_sha256": manifest["cohort_sha256"],
                "historical_file_count": manifest["historical_audit"]["file_count"],
                "record_count": manifest["record_count"],
                "rejected_overlap_count": manifest["selection"]["rejected_overlap_count"],
            },
            sort_keys=True,
        )
    )


def prepare_holdout(
    *,
    candidates_path: Path,
    repo_root: Path,
    history_roots: list[Path],
    history_cutoff: str,
    run_config_path: Path,
    limit: int,
    excluded_paths: set[Path] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if limit < 30 or limit > 50:
        raise BlindHoldoutError("holdout limit must be between 30 and 50")
    cutoff = _parse_timestamp(history_cutoff)
    git_evidence = _git_evidence(repo_root)
    candidates_bytes = candidates_path.read_bytes()
    candidates = json.loads(candidates_bytes)
    if not isinstance(candidates, list):
        raise BlindHoldoutError("candidate pool must be a JSON array")
    normalized_candidates = [_normalize_candidate(candidate) for candidate in candidates]
    candidate_company_keys = {
        _company_key(record["company_name"]): record["company_name"]
        for record in normalized_candidates
    }
    candidate_job_ids = {
        _linkedin_job_id(record["linkedin_job_url"])
        for record in normalized_candidates
    }
    run_config_bytes = run_config_path.read_bytes()
    run_config = json.loads(run_config_bytes)
    if not isinstance(run_config, dict):
        raise BlindHoldoutError("run configuration must be a JSON object")

    excluded = {path.resolve() for path in (excluded_paths or set())}
    roots = [repo_root, *history_roots]
    files = list(_historical_files(roots, cutoff, excluded))
    seen_companies: set[str] = set()
    seen_job_ids: set[str] = set()
    text_mentioned_companies: set[str] = set()
    text_mentioned_job_ids: set[str] = set()
    file_digests: list[tuple[str, str]] = []
    for path in files:
        content = path.read_bytes()
        file_digests.append((_stable_path(path, roots), hashlib.sha256(content).hexdigest()))
        text = content.decode("utf-8", errors="ignore")
        folded = text.casefold()
        for company_key, company_name in candidate_company_keys.items():
            if _company_mentioned(company_name, folded):
                text_mentioned_companies.add(company_key)
        for job_id in candidate_job_ids:
            if job_id in text:
                text_mentioned_job_ids.add(job_id)
        if path.suffix.casefold() in {".json", ".jsonl"}:
            for payload in _json_payloads(text):
                _collect_structured_identities(payload, seen_companies, seen_job_ids)
    git_text = git_evidence["history_bytes"].decode("utf-8", errors="ignore")
    git_folded = git_text.casefold()
    for company_key, company_name in candidate_company_keys.items():
        if _company_mentioned(company_name, git_folded):
            text_mentioned_companies.add(company_key)
    for job_id in candidate_job_ids:
        if job_id in git_text:
            text_mentioned_job_ids.add(job_id)

    selected: list[dict[str, Any]] = []
    selected_companies: set[str] = set()
    rejected: list[dict[str, str]] = []
    for record in normalized_candidates:
        company_key = _company_key(record["company_name"])
        job_id = _linkedin_job_id(record["linkedin_job_url"])
        overlap_reason = None
        if company_key in selected_companies:
            overlap_reason = "duplicate_candidate_company"
        elif company_key in seen_companies:
            overlap_reason = "historical_structured_company"
        elif job_id in seen_job_ids or job_id in text_mentioned_job_ids:
            overlap_reason = "historical_linkedin_job"
        elif company_key in text_mentioned_companies:
            overlap_reason = "historical_text_company"
        if overlap_reason:
            rejected.append(
                {
                    "company_name": record["company_name"],
                    "linkedin_job_id": job_id,
                    "reason": overlap_reason,
                }
            )
            continue
        selected.append(record)
        selected_companies.add(company_key)
        if len(selected) == limit:
            break
    if len(selected) != limit:
        raise BlindHoldoutError(
            f"only {len(selected)} unseen unique companies remain; {limit} required"
        )

    cohort_bytes = _canonical_json_bytes(selected)
    file_set_bytes = _canonical_json_bytes(file_digests)
    identity_rows = [_record_identity(record) for record in selected]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "cohort_provenance": "blind_unseen",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "history_cutoff": cutoff.isoformat(),
        "code_commit": git_evidence["head"],
        "source_tree_sha256": git_evidence["tree"],
        "record_count": len(selected),
        "cohort_sha256": hashlib.sha256(cohort_bytes).hexdigest(),
        "candidate_pool_sha256": hashlib.sha256(candidates_bytes).hexdigest(),
        "run_configuration_sha256": hashlib.sha256(run_config_bytes).hexdigest(),
        "run_configuration": run_config,
        "cohort_identity_sha256": hashlib.sha256(
            _canonical_json_bytes(identity_rows)
        ).hexdigest(),
        "historical_audit": {
            "roots": [str(path.resolve()) for path in roots],
            "file_count": len(files),
            "file_set_sha256": hashlib.sha256(file_set_bytes).hexdigest(),
            "git_history_sha256": hashlib.sha256(git_evidence["history_bytes"]).hexdigest(),
            "structured_company_count": len(seen_companies),
            "linkedin_job_id_count": len(seen_job_ids),
            "scan_suffixes": sorted(SCAN_SUFFIXES),
            "skipped_file_count": 0,
        },
        "selection": {
            "policy": "first unseen unique company in frozen candidate order",
            "company_overlap_policy": "reject structured or case-insensitive text mention",
            "linkedin_job_overlap_policy": "reject canonical LinkedIn job id",
            "rejected_overlap_count": len(rejected),
            "rejected_overlap_sha256": hashlib.sha256(
                _canonical_json_bytes(rejected)
            ).hexdigest(),
            "post_selection_overlap_count": 0,
        },
        "records": identity_rows,
    }
    return selected, manifest


def _historical_files(
    roots: list[Path], cutoff: datetime, excluded: set[Path]
) -> Iterable[Path]:
    found: list[Path] = []
    _ = cutoff  # The cutoff is provenance, not a mutable-mtime filter.
    for root in roots:
        if not root.exists():
            raise BlindHoldoutError(f"historical root does not exist: {root}")
        for current, directories, filenames in os.walk(root):
            directories[:] = [name for name in directories if name not in SKIP_PARTS]
            base = Path(current)
            for filename in filenames:
                path = base / filename
                resolved = path.resolve()
                if resolved in excluded or path.suffix.casefold() not in SCAN_SUFFIXES:
                    continue
                found.append(path)
    return sorted(set(found), key=lambda path: str(path))


def _json_payloads(text: str) -> Iterable[Any]:
    try:
        yield json.loads(text)
        return
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _collect_structured_identities(
    value: Any, companies: set[str], job_ids: set[str]
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).rsplit(".", 1)[-1].casefold()
            if normalized_key in COMPANY_KEYS and isinstance(item, str) and item.strip():
                companies.add(_company_key(item))
            if normalized_key in JOB_URL_KEYS and isinstance(item, str):
                job_id = _linkedin_job_id(item, required=False)
                if job_id:
                    job_ids.add(job_id)
            _collect_structured_identities(item, companies, job_ids)
    elif isinstance(value, list):
        for item in value:
            _collect_structured_identities(item, companies, job_ids)


def _normalize_candidate(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise BlindHoldoutError("each candidate must be an object")
    required = ("company_name", "linkedin_job_url", "job_title", "job_location")
    if any(not isinstance(candidate.get(field), str) or not candidate[field].strip() for field in required):
        raise BlindHoldoutError(f"candidate is missing required fields: {candidate!r}")
    _linkedin_job_id(candidate["linkedin_job_url"])
    forbidden_prefills = {
        "company_website_url", "career_root_url", "career_page_url",
        "job_list_page_url", "open_position_url",
    }
    if any(candidate.get(field) for field in forbidden_prefills):
        raise BlindHoldoutError("blind discovery input cannot contain website/career/board/opening prefills")
    allowed = {
        "company_name",
        "linkedin_job_url",
        "linkedin_company_url",
        "job_title",
        "job_location",
        "source",
        "source_trace",
    }
    normalized = {key: candidate.get(key) for key in allowed if candidate.get(key) is not None}
    normalized["source"] = "linkedin_public_jobs_blind_holdout"
    source_trace = dict(normalized.get("source_trace") or {})
    source_trace["blind_holdout"] = {"provenance": "blind_unseen", "frozen": True}
    normalized["source_trace"] = source_trace
    return normalized


def _record_identity(record: dict[str, Any]) -> dict[str, str]:
    return {
        "company_name": record["company_name"],
        "linkedin_job_id": _linkedin_job_id(record["linkedin_job_url"]),
        "linkedin_job_url": _canonical_linkedin_url(record["linkedin_job_url"]),
        "job_title": record["job_title"],
        "job_location": record["job_location"],
    }


def _company_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _company_mentioned(company_name: str, text: str) -> bool:
    name = " ".join(company_name.casefold().split())
    if len(name) < 4:
        return True
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])")
    return bool(pattern.search(text))


def _linkedin_job_id(value: str, *, required: bool = True) -> str:
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        parsed = None
    host = (parsed.hostname or "").casefold() if parsed else ""
    match = re.match(r"^/jobs/view/(?:.*-)?([0-9]{6,})/?$", parsed.path) if parsed else None
    if host in {"linkedin.com", "www.linkedin.com"} and match:
        return match.group(1)
    if required:
        raise BlindHoldoutError(f"invalid LinkedIn job URL: {value!r}")
    return ""


def _canonical_linkedin_url(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse(("https", "www.linkedin.com", parsed.path.rstrip("/"), "", "", ""))


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BlindHoldoutError("history cutoff must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise BlindHoldoutError("history cutoff must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _stable_path(path: Path, roots: list[Path]) -> str:
    resolved = path.resolve()
    for index, root in enumerate(roots):
        try:
            return f"root{index}/{resolved.relative_to(root.resolve())}"
        except ValueError:
            continue
    return str(resolved)


def _git_evidence(repo_root: Path) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if status.stdout.strip():
        raise BlindHoldoutError("tracked worktree must be clean before cohort freeze")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo_root, check=True,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    history = subprocess.run(
        ["git", "log", "--all", "-p", "--no-ext-diff", "--text"],
        cwd=repo_root, check=True, capture_output=True, timeout=120,
    ).stdout
    return {"head": head, "tree": tree, "history_bytes": history}


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    main()
