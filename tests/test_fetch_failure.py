import json
import http.client
import socket
import ssl
import unittest
from urllib.error import HTTPError, URLError

from job_source_agent.fetch_failure import project_fetch_error
from job_source_agent.request_identity import build_request_identity
from job_source_agent.web import FetchError, normalize_transport_exception


class FetchFailureProjectionTests(unittest.TestCase):
    def test_incomplete_read_is_typed_as_replayable_read_failure(self):
        error = normalize_transport_exception(
            http.client.IncompleteRead(b"partial", 20),
            url="https://jobs.example.com/openings?token=private",
        )

        self.assertIsNotNone(error)
        projection = project_fetch_error(error)
        self.assertEqual(projection["reason_code"], "FETCH_FAILED")
        self.assertEqual(projection["transport_phase"], "read")
        self.assertTrue(projection["retryable"])
        self.assertNotIn("private", str(projection["request_identity"]))

    def test_transport_phase_matrix_is_stable(self):
        cases = (
            (URLError(socket.gaierror(-2, "name resolution failed")), "dns"),
            (http.client.RemoteDisconnected("closed"), "connect"),
            (URLError(ssl.SSLError("TLS handshake failed")), "tls"),
            (HTTPError("https://example.com", 403, "Forbidden", {}, None), "http"),
            (http.client.IncompleteRead(b"partial", 4), "read"),
            (TimeoutError("timed out"), "timeout"),
        )
        for raw_error, expected_phase in cases:
            with self.subTest(phase=expected_phase):
                error = normalize_transport_exception(
                    raw_error,
                    url="https://example.com",
                )
                self.assertIsNotNone(error)
                self.assertEqual(error.transport_phase, expected_phase)

    def test_typed_reason_and_retryability_override_conflicting_message(self):
        projection = project_fetch_error(
            FetchError(
                "HTTP Error 404: Not Found",
                reason_code="NETWORK_TIMEOUT",
                retryable=True,
            )
        )

        self.assertEqual(projection["reason_code"], "NETWORK_TIMEOUT")
        self.assertEqual(projection["reason_code_source"], "exception")
        self.assertTrue(projection["retryable"])

    def test_untyped_error_classifies_message_and_uses_reason_retryability(self):
        projection = project_fetch_error(FetchError("HTTP Error 403: Forbidden"))

        self.assertEqual(projection["reason_code"], "HTTP_FORBIDDEN")
        self.assertEqual(projection["reason_code_source"], "classified_message")
        self.assertFalse(projection["retryable"])

    def test_preserves_status_and_safe_request_identity(self):
        identity = build_request_identity(
            "https://jobs.example.com/api?api_key=private",
            data=b'{"token":"hidden","page":2}',
            headers={"Content-Type": "application/json"},
        ).as_dict()
        projection = project_fetch_error(
            FetchError(
                "rate limited",
                status=429,
                reason_code="RATE_LIMITED",
                retryable=True,
                request_identity=identity,
            )
        )

        self.assertEqual(projection["status"], 429)
        self.assertEqual(projection["request_identity"], identity)
        self.assertNotIn("private", str(projection["request_identity"]))
        self.assertNotIn("hidden", str(projection["request_identity"]))

    def test_projection_is_json_safe(self):
        projection = project_fetch_error(
            FetchError(
                "connection reset",
                request_identity=build_request_identity(
                    "https://jobs.example.com/?token=private"
                ).as_dict(),
            )
        )

        encoded = json.dumps(projection, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("private", encoded)
        self.assertEqual(json.loads(encoded), projection)


if __name__ == "__main__":
    unittest.main()
