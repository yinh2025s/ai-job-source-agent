import unittest

from job_source_agent.completion_resume import classify_completion_resume
from job_source_agent.models import PIPELINE_STAGES


def stage_chain(
    failure_stage: str | None = None,
    *,
    retryable: bool = False,
    reason_code: str | None = None,
) -> list[dict]:
    records = []
    failed = False
    for stage in PIPELINE_STAGES:
        if stage == failure_stage:
            records.append(
                {
                    "stage": stage,
                    "status": "failed",
                    "retryable": retryable,
                    "reason_code": reason_code,
                }
            )
            failed = True
        elif failed and stage != "result_validation":
            records.append({"stage": stage, "status": "not_run", "retryable": False})
        else:
            records.append({"stage": stage, "status": "success", "retryable": False})
    return records


class CompletionResumePolicyTests(unittest.TestCase):
    def test_success_is_restored_without_requiring_stage_details(self):
        decision = classify_completion_resume(
            {"pipeline_status": "success"},
            {"trace": {}},
        )

        self.assertEqual(decision.action, "completion_restore")
        self.assertEqual(decision.reason, "pipeline_success")

    def test_first_retryable_failure_is_resubmitted_from_that_stage(self):
        decision = classify_completion_resume(
            {"pipeline_status": "failed"},
            {
                "stages": stage_chain(
                    "career_discovery",
                    retryable=True,
                    reason_code="NETWORK_TIMEOUT",
                )
            },
        )

        self.assertEqual(decision.action, "retryable_resubmit")
        self.assertEqual(decision.retry_stage, "career_discovery")
        self.assertEqual(decision.reason_code, "NETWORK_TIMEOUT")

    def test_non_retryable_failure_is_restored(self):
        decision = classify_completion_resume(
            {"pipeline_status": "partial"},
            {
                "stages": stage_chain(
                    "opening_match",
                    retryable=False,
                    reason_code="OPENING_NOT_FOUND",
                )
            },
        )

        self.assertEqual(decision.action, "non_retryable_restore")
        self.assertEqual(decision.retry_stage, "opening_match")

    def test_malformed_or_ambiguous_evidence_fails_closed(self):
        cases = [
            {},
            {"stages": []},
            {"stages": stage_chain("career_discovery", retryable=True)[:-1]},
            {
                "stages": [
                    {**item, **({"retryable": "yes"} if item["stage"] == "career_discovery" else {})}
                    for item in stage_chain("career_discovery", retryable=True)
                ]
            },
        ]

        for trace in cases:
            with self.subTest(trace=trace):
                decision = classify_completion_resume(
                    {"pipeline_status": "failed"},
                    trace,
                )
                self.assertEqual(decision.action, "unclassified_restore")


if __name__ == "__main__":
    unittest.main()
