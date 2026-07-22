from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from job_source_agent.candidate_reasoning_contracts import (
    StructuredLLMRequest,
    TokenUsage,
)
from job_source_agent.deepseek_reasoning_client import (
    DEFAULT_DEEPSEEK_MODEL,
    DEEPSEEK_CHAT_COMPLETIONS_URL,
    DeepSeekHTTPResponse,
    DeepSeekReasoningClient,
)


SECRET = "deepseek-test-secret-never-log"


class RecordingTransport:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[tuple[object, float]] = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if not self.results:
            raise AssertionError("unexpected retry")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class DeepSeekReasoningClientTest(unittest.TestCase):
    def test_per_invocation_timeout_clamps_configured_transport_limit(self):
        transport = RecordingTransport(self._response(self._planner_decision()))
        client = DeepSeekReasoningClient(
            api_key=SECRET,
            timeout_seconds=7.0,
            transport=transport,
        )

        client.complete(self._planner_request(), timeout_seconds=2.5)

        self.assertEqual(transport.calls[0][1], 2.5)

    def test_query_planner_emits_exact_bounded_non_thinking_request(self):
        transport = RecordingTransport(self._response(self._planner_decision()))
        client = DeepSeekReasoningClient(api_key=SECRET, transport=transport)

        response = client.complete(self._planner_request())

        self.assertEqual(response.payload["queries"][0]["purpose"], "career_site")
        self.assertEqual(len(transport.calls), 1)
        request, timeout = transport.calls[0]
        self.assertEqual(request.full_url, DEEPSEEK_CHAT_COMPLETIONS_URL)
        self.assertEqual(request.method, "POST")
        self.assertEqual(timeout, 8.0)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {SECRET}")
        body = json.loads(request.data)
        self.assertEqual(
            set(body),
            {
                "max_tokens",
                "messages",
                "model",
                "response_format",
                "stream",
                "temperature",
                "thinking",
            },
        )
        self.assertEqual(body["model"], DEFAULT_DEEPSEEK_MODEL)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["stream"], False)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], 1_000)
        self.assertNotIn("tools", body)
        self.assertNotIn("user_id", body)
        self.assertNotIn(SECRET, request.data.decode("utf-8"))
        self.assertEqual([item["role"] for item in body["messages"]], ["system", "user"])
        system = body["messages"][0]["content"]
        self.assertIn("untrusted data", system)
        self.assertIn("Return one JSON object only", system)
        self.assertIn("Decision kind: query_plan", system)
        self.assertIn("never a URL", system)
        self.assertIn('"queries"', system)
        self.assertEqual(
            json.loads(body["messages"][1]["content"]),
            {
                "decision_kind": "query_plan",
                "schema_name": "company_query_planner_v1",
                "payload": {
                    "normalized_company_name": "Example Labs",
                    "schema_version": "1",
                },
            },
        )

    def test_candidate_rank_prompt_limits_output_to_existing_candidate_ids(self):
        transport = RecordingTransport(self._response(self._ranker_decision()))
        client = DeepSeekReasoningClient(api_key=SECRET, transport=transport)

        client.complete(self._ranker_request())

        body = json.loads(transport.calls[0][0].data)
        self.assertEqual(body["max_tokens"], 1_600)
        system = body["messages"][0]["content"]
        self.assertIn("Decision kind: candidate_rank", system)
        self.assertIn("only a candidate_id", system)
        self.assertIn("Do not output URLs or create new candidates", system)
        user = json.loads(body["messages"][1]["content"])
        self.assertEqual(user["payload"]["candidates"][0]["candidate_id"], "candidate-1")

    def test_key_is_loaded_only_at_construction_and_missing_key_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY is required"):
                DeepSeekReasoningClient(transport=RecordingTransport())

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": SECRET}, clear=True):
            transport = RecordingTransport(self._response(self._planner_decision()))
            client = DeepSeekReasoningClient(transport=transport)
        with patch.dict(os.environ, {}, clear=True):
            client.complete(self._planner_request())
        self.assertEqual(
            transport.calls[0][0].get_header("Authorization"), f"Bearer {SECRET}"
        )

    def test_timeout_is_sanitized_and_never_retried(self):
        transport = RecordingTransport(TimeoutError(SECRET), self._response({}))
        client = DeepSeekReasoningClient(api_key=SECRET, transport=transport)

        with self.assertRaises(TimeoutError) as raised:
            client.complete(self._planner_request())

        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(len(transport.calls), 1)

    def test_url_timeout_is_normalized(self):
        transport = RecordingTransport(URLError(TimeoutError("socket stalled")))
        client = DeepSeekReasoningClient(api_key=SECRET, transport=transport)
        with self.assertRaises(TimeoutError):
            client.complete(self._planner_request())
        self.assertEqual(len(transport.calls), 1)

    def test_http_and_provider_errors_are_sanitized_without_retry(self):
        http_transport = RecordingTransport(
            HTTPError(DEEPSEEK_CHAT_COMPLETIONS_URL, 429, SECRET, {}, None)
        )
        with self.assertRaisesRegex(ValueError, "status 429") as raised:
            DeepSeekReasoningClient(
                api_key=SECRET, transport=http_transport
            ).complete(self._planner_request())
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(len(http_transport.calls), 1)

        provider_transport = RecordingTransport(
            DeepSeekHTTPResponse(
                200,
                json.dumps({"error": {"message": SECRET}}).encode(),
            )
        )
        with self.assertRaisesRegex(ValueError, "provider returned an error") as raised:
            DeepSeekReasoningClient(
                api_key=SECRET, transport=provider_transport
            ).complete(self._planner_request())
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(len(provider_transport.calls), 1)

    def test_transport_error_cannot_leak_secret(self):
        transport = RecordingTransport(RuntimeError(SECRET))
        with self.assertRaisesRegex(ValueError, "transport error") as raised:
            DeepSeekReasoningClient(api_key=SECRET, transport=transport).complete(
                self._planner_request()
            )
        self.assertNotIn(SECRET, str(raised.exception))

    def test_rejects_malformed_provider_envelope_and_unknown_shape(self):
        bad_responses = (
            DeepSeekHTTPResponse(200, b"not-json"),
            DeepSeekHTTPResponse(200, b"[]"),
            DeepSeekHTTPResponse(200, b'{"choices":[],"usage":{},"unexpected":1}'),
            DeepSeekHTTPResponse(200, b'{"choices":[]}'),
        )
        for response in bad_responses:
            with self.subTest(body=response.body):
                with self.assertRaises(ValueError):
                    DeepSeekReasoningClient(
                        api_key=SECRET,
                        transport=RecordingTransport(response),
                    ).complete(self._planner_request())

    def test_malformed_model_content_raises_json_decode_error(self):
        transport = RecordingTransport(self._response_body(f"{SECRET} not-json"))
        with self.assertRaises(json.JSONDecodeError) as raised:
            DeepSeekReasoningClient(api_key=SECRET, transport=transport).complete(
                self._planner_request()
            )
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn(SECRET, raised.exception.doc)

    def test_rejects_empty_non_object_and_nonempty_reasoning_content(self):
        responses = (
            self._response_body(""),
            self._response_body("[]"),
            self._response_body(json.dumps({"reasoning_content": "hidden"})),
            self._response_body(
                json.dumps(self._planner_decision()),
                message_extra={"reasoning_content": "hidden"},
            ),
        )
        for response in responses:
            with self.subTest(body=response.body):
                with self.assertRaises(ValueError):
                    DeepSeekReasoningClient(
                        api_key=SECRET,
                        transport=RecordingTransport(response),
                    ).complete(self._planner_request())

    def test_accepts_empty_reasoning_field_and_zero_reasoning_usage_only(self):
        accepted = self._response_body(
            json.dumps(self._planner_decision()),
            message_extra={"reasoning_content": None},
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        )
        response = DeepSeekReasoningClient(
            api_key=SECRET,
            transport=RecordingTransport(accepted),
        ).complete(self._planner_request())
        self.assertEqual(response.token_usage, TokenUsage(10, 5, 15))

        rejected = self._response_body(
            json.dumps(self._planner_decision()),
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
        )
        with self.assertRaisesRegex(ValueError, "non-thinking usage"):
            DeepSeekReasoningClient(
                api_key=SECRET,
                transport=RecordingTransport(rejected),
            ).complete(self._planner_request())

    def test_rejects_unknown_or_malformed_prompt_token_details(self):
        details = (
            {"unexpected": 0},
            {"cached_tokens": -1},
            {"cached_tokens": True},
            {"cached_tokens": "0"},
        )
        for prompt_details in details:
            with self.subTest(prompt_details=prompt_details):
                with self.assertRaisesRegex(ValueError, "prompt token usage"):
                    DeepSeekReasoningClient(
                        api_key=SECRET,
                        transport=RecordingTransport(
                            self._response(
                                self._planner_decision(),
                                usage={
                                    "prompt_tokens": 10,
                                    "completion_tokens": 5,
                                    "total_tokens": 15,
                                    "prompt_tokens_details": prompt_details,
                                },
                            )
                        ),
                    ).complete(self._planner_request())

    def test_rejects_non_stop_finish_reason(self):
        response = self._response(self._planner_decision())
        payload = json.loads(response.body)
        payload["choices"][0]["finish_reason"] = "length"
        with self.assertRaisesRegex(ValueError, "did not finish normally"):
            DeepSeekReasoningClient(
                api_key=SECRET,
                transport=RecordingTransport(
                    DeepSeekHTTPResponse(200, json.dumps(payload).encode())
                ),
            ).complete(self._planner_request())

    def test_token_usage_and_raw_response_id_come_from_provider(self):
        transport = RecordingTransport(
            self._response(
                self._planner_decision(),
                response_id="chatcmpl-123",
                usage={
                    "prompt_tokens": 21,
                    "completion_tokens": 8,
                    "total_tokens": 29,
                    "prompt_cache_hit_tokens": 4,
                },
            )
        )
        response = DeepSeekReasoningClient(
            api_key=SECRET, transport=transport
        ).complete(self._planner_request())
        self.assertEqual(response.raw_response_id, "chatcmpl-123")
        self.assertEqual(response.token_usage, TokenUsage(21, 8, 29))

    def test_rejects_missing_non_integer_and_inconsistent_usage(self):
        usages = (
            {"prompt_tokens": 1, "completion_tokens": 2},
            {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
            {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4},
            {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
                "secret_tokens": 1,
            },
        )
        for usage in usages:
            with self.subTest(usage=usage):
                with self.assertRaises(ValueError):
                    DeepSeekReasoningClient(
                        api_key=SECRET,
                        transport=RecordingTransport(
                            self._response(self._planner_decision(), usage=usage)
                        ),
                    ).complete(self._planner_request())

    def test_request_size_is_bounded_before_transport(self):
        transport = RecordingTransport(self._response(self._planner_decision()))
        client = DeepSeekReasoningClient(api_key=SECRET, transport=transport)
        request = StructuredLLMRequest(
            "query_plan",
            "company_query_planner_v1",
            {f"field_{index}": "x" * 4_000 for index in range(17)},
        )
        with self.assertRaisesRegex(ValueError, "request exceeds size limit"):
            client.complete(request)
        self.assertEqual(transport.calls, [])

    @staticmethod
    def _planner_request() -> StructuredLLMRequest:
        return StructuredLLMRequest(
            "query_plan",
            "company_query_planner_v1",
            {"schema_version": "1", "normalized_company_name": "Example Labs"},
        )

    @staticmethod
    def _ranker_request() -> StructuredLLMRequest:
        return StructuredLLMRequest(
            "candidate_rank",
            "company_candidate_ranker_v1",
            {
                "schema_version": "1",
                "candidates": [
                    {
                        "candidate_id": "candidate-1",
                        "url": "https://example.invalid/careers",
                    }
                ],
            },
        )

    @staticmethod
    def _planner_decision() -> dict[str, object]:
        return {
            "schema_version": "1",
            "normalized_company_name": "Example Labs",
            "core_brand_tokens": ["Example"],
            "legal_or_descriptive_suffixes": ["Labs"],
            "possible_aliases": [],
            "queries": [{"query": '"Example Labs" careers', "purpose": "career_site"}],
            "ambiguous": False,
            "reason_codes": ["DESCRIPTIVE_SUFFIX"],
        }

    @staticmethod
    def _ranker_decision() -> dict[str, object]:
        return {
            "schema_version": "1",
            "ranked_candidates": [
                {
                    "candidate_id": "candidate-1",
                    "confidence_bucket": "high",
                    "evidence_ids": ["candidate-1"],
                    "reason_codes": ["BRAND_MATCH"],
                }
            ],
            "ambiguous": False,
        }

    @classmethod
    def _response(
        cls,
        payload: dict[str, object],
        *,
        response_id: str = "chatcmpl-1",
        usage: dict[str, object] | None = None,
    ) -> DeepSeekHTTPResponse:
        return cls._response_body(
            json.dumps(payload), response_id=response_id, usage=usage
        )

    @staticmethod
    def _response_body(
        content: str,
        *,
        response_id: str = "chatcmpl-1",
        usage: dict[str, object] | None = None,
        message_extra: dict[str, object] | None = None,
    ) -> DeepSeekHTTPResponse:
        message = {"role": "assistant", "content": content}
        message.update(message_extra or {})
        envelope = {
            "id": response_id,
            "object": "chat.completion",
            "created": 1,
            "model": DEFAULT_DEEPSEEK_MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
            "usage": usage
            or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return DeepSeekHTTPResponse(
            status=200,
            body=json.dumps(envelope, separators=(",", ":")).encode("utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
