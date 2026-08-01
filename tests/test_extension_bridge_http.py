import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from job_source_agent.composition import FetcherConfig
from job_source_agent.extension_bridge import (
    MAX_PAIR_REQUEST_BYTES,
    PAIRING_CLIENT_ID,
    PAIRING_PROTOCOL_VERSION,
    MAX_REQUEST_BYTES,
    ExtensionBridgeConfig,
    ExtensionBridgeServer,
    ExtensionRunManager,
)


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-bridge-token-0123456789-ABCDE"
ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
OTHER_ORIGIN = "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PAIR_PAYLOAD = json.dumps(
    {
        "client": PAIRING_CLIENT_ID,
        "protocol_version": PAIRING_PROTOCOL_VERSION,
    }
).encode("utf-8")


class ExtensionBridgeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.output_dir = Path(cls.temporary_directory.name) / "output"
        cls.manager = ExtensionRunManager(
            ExtensionBridgeConfig(
                fetcher=FetcherConfig(
                    fixtures_dir=ROOT / "samples" / "sites",
                    offline=True,
                ),
                workers=1,
                output_dir=cls.output_dir,
            )
        )
        cls.server = ExtensionBridgeServer(("127.0.0.1", 0), cls.manager, TOKEN)
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever,
            name="extension-bridge-http-test",
        )
        cls.server_thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server_thread.join(timeout=5)
        cls.server.server_close()
        cls.manager.close()
        cls.temporary_directory.cleanup()
        if cls.server_thread.is_alive():
            raise AssertionError("Extension bridge server thread did not stop.")

    def test_health_returns_200_with_cors_and_no_store_headers(self):
        status, headers, payload = self._request("GET", "/v1/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})
        self._assert_response_headers(headers)

    def test_authorized_post_and_run_lookup_contract(self):
        record = {
            "company_name": "Aurora Data",
            "company_website_url": "https://aurora-data.example",
            "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
            "job_title": "AI Engineer",
            "job_location": "Remote",
            "source": "linkedin_browser_extension",
        }
        status, headers, payload = self._request(
            "POST",
            "/v1/runs",
            body=json.dumps({"records": [record]}).encode("utf-8"),
        )

        self.assertEqual(status, 202)
        self.assertIn(payload["status"], {"queued", "running", "complete", "failed"})
        self.assertTrue(payload["run_id"])
        self.assertEqual(payload["submitted"], 1)
        self.assertIn(payload["completed"], {0, 1})
        self._assert_response_headers(headers)

        status, headers, run = self._request("GET", f"/v1/runs/{payload['run_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(run["run_id"], payload["run_id"])
        self.assertEqual(run["submitted"], 1)
        self.assertIn(run["status"], {"queued", "running", "complete", "failed"})
        self.assertIn(run["completed"], {0, 1})
        self._assert_response_headers(headers)

        status, headers, payload = self._request("GET", "/v1/runs/unknown-run")
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "run_not_found"})
        self._assert_response_headers(headers)

    def test_executor_rejection_returns_queryable_failed_run(self):
        record = {
            "company_name": "Example Robotics",
            "linkedin_job_url": "https://www.linkedin.com/jobs/view/999",
        }
        with patch.object(
            self.manager._executor,
            "submit",
            side_effect=RuntimeError("cannot schedule new futures after shutdown"),
        ):
            status, headers, payload = self._request(
                "POST",
                "/v1/runs",
                body=json.dumps({"records": [record]}).encode("utf-8"),
            )

        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "bridge_executor_unavailable")
        self._assert_response_headers(headers)

        status, headers, run = self._request("GET", f"/v1/runs/{payload['run_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(run, payload)
        self._assert_response_headers(headers)

    def test_wrong_token_returns_401(self):
        status, headers, payload = self._request(
            "GET",
            "/v1/health",
            authorization="Bearer wrong-token",
        )

        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})
        self._assert_response_headers(headers)

    def test_wrong_origin_returns_403(self):
        status, headers, payload = self._request(
            "GET",
            "/v1/health",
            origin="https://attacker.example",
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload, {"error": "origin_not_allowed"})
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        self.assertIsNone(headers.get("Vary"))
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_oversize_payload_returns_413(self):
        status, headers, payload = self._request(
            "POST",
            "/v1/runs",
            content_length=MAX_REQUEST_BYTES + 1,
        )

        self.assertEqual(status, 413)
        self.assertEqual(payload, {"error": "invalid_request_size"})
        self._assert_response_headers(headers)

    def test_pair_rejects_missing_and_web_origins(self):
        for origin in (None, "https://attacker.example"):
            with self.subTest(origin=origin):
                status, headers, payload = self._request(
                    "POST",
                    "/v1/pair",
                    body=PAIR_PAYLOAD,
                    authorization=None,
                    origin=origin,
                )
                self.assertEqual(status, 403)
                self.assertEqual(payload, {"error": "origin_not_allowed"})
                self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_pair_rejects_invalid_or_oversized_payload(self):
        status, _, payload = self._request(
            "POST",
            "/v1/pair",
            body=json.dumps({
                "client": PAIRING_CLIENT_ID,
                "protocol_version": PAIRING_PROTOCOL_VERSION,
                "extra": True,
            }).encode("utf-8"),
            authorization=None,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "invalid_pairing_request"})

        status, _, payload = self._request(
            "POST",
            "/v1/pair",
            authorization=None,
            content_length=MAX_PAIR_REQUEST_BYTES + 1,
        )
        self.assertEqual(status, 413)
        self.assertEqual(payload, {"error": "invalid_request_size"})

    def _request(
        self,
        method,
        path,
        *,
        body=None,
        authorization=f"Bearer {TOKEN}",
        origin=ORIGIN,
        content_length=None,
    ):
        headers = {}
        if authorization is not None:
            headers["Authorization"] = authorization
        if origin is not None:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = "application/json"
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            payload = json.loads(response_body) if response_body else None
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def _assert_response_headers(self, headers):
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)
        self.assertEqual(headers.get("Vary"), "Origin")
        self.assertEqual(headers.get("Cache-Control"), "no-store")


class ExtensionBridgePairingHttpTests(unittest.TestCase):
    def setUp(self):
        self.manager = ExtensionRunManager(
            ExtensionBridgeConfig(fetcher=FetcherConfig(offline=True), workers=1)
        )
        self.now = 1000.0
        self.server = ExtensionBridgeServer(
            ("127.0.0.1", 0),
            self.manager,
            TOKEN,
            monotonic=lambda: self.now,
        )
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        self.server.server_close()
        self.manager.close()

    def test_first_claim_same_origin_recovery_and_different_origin_conflict(self):
        status, headers, payload = self._request(
            "POST", "/v1/pair", body=PAIR_PAYLOAD, authorization=None
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "paired")
        self.assertEqual(payload["protocol_version"], "1")
        self.assertEqual(payload["token"], TOKEN)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)

        self.now += 1000
        status, _, recovered = self._request(
            "POST", "/v1/pair", body=PAIR_PAYLOAD, authorization=None
        )
        self.assertEqual(status, 200)
        self.assertEqual(recovered["token"], TOKEN)

        status, headers, payload = self._request(
            "POST",
            "/v1/pair",
            body=PAIR_PAYLOAD,
            authorization=None,
            origin=OTHER_ORIGIN,
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload, {"error": "pairing_conflict"})
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), OTHER_ORIGIN)

        status, headers, payload = self._request(
            "OPTIONS",
            "/v1/pair",
            authorization=None,
            origin=OTHER_ORIGIN,
        )
        self.assertEqual(status, 204)
        self.assertIsNone(payload)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), OTHER_ORIGIN)

    def test_unclaimed_default_pairing_remains_available_until_first_claim(self):
        self.now += 24 * 60 * 60
        status, headers, payload = self._request(
            "POST", "/v1/pair", body=PAIR_PAYLOAD, authorization=None
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "paired")
        self.assertEqual(payload["token"], TOKEN)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)

    def test_configured_pairing_window_expires_with_typed_failure(self):
        manager = ExtensionRunManager(
            ExtensionBridgeConfig(fetcher=FetcherConfig(offline=True), workers=1)
        )
        now = [1000.0]
        server = ExtensionBridgeServer(
            ("127.0.0.1", 0),
            manager,
            TOKEN,
            pairing_window_seconds=120,
            monotonic=lambda: now[0],
        )
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        host, port = server.server_address
        try:
            now[0] += 121
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request(
                "POST",
                "/v1/pair",
                body=PAIR_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "Origin": ORIGIN,
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            headers = dict(response.getheaders())
            connection.close()
            self.assertEqual(response.status, 410)
            self.assertEqual(payload, {"error": "pairing_expired"})
            self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            manager.close()

    def test_auth_and_cors_are_pinned_after_pairing(self):
        status, _, _ = self._request(
            "POST", "/v1/pair", body=PAIR_PAYLOAD, authorization=None
        )
        self.assertEqual(status, 200)

        status, headers, payload = self._request(
            "GET", "/v1/health", origin=OTHER_ORIGIN
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload, {"error": "origin_not_allowed"})
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

        status, _, payload = self._request(
            "GET", "/v1/health", origin=None
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

        status, headers, payload = self._request(
            "OPTIONS", "/v1/runs", authorization=None
        )
        self.assertEqual(status, 204)
        self.assertIsNone(payload)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)

        status, headers, payload = self._request(
            "OPTIONS",
            "/v1/runs",
            authorization=None,
            origin=OTHER_ORIGIN,
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload, {"error": "origin_not_allowed"})
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_explicit_bearer_flow_claims_and_pins_origin(self):
        status, _, payload = self._request("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

        status, _, payload = self._request(
            "GET", "/v1/health", origin=OTHER_ORIGIN
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload, {"error": "origin_not_allowed"})

    def test_pairing_disabled_never_returns_explicit_token(self):
        server = ExtensionBridgeServer(
            ("127.0.0.1", 0),
            self.manager,
            "manual-token",
            pairing_enabled=False,
        )
        try:
            self.assertEqual(server.pair(ORIGIN), ("pairing_disabled", None))
        finally:
            server.server_close()

    def test_concurrent_first_claim_has_exactly_one_winner(self):
        barrier = threading.Barrier(3)
        results = []

        def claim(origin):
            barrier.wait()
            results.append((origin, *self.server.pair(origin)))

        first = threading.Thread(target=claim, args=(ORIGIN,))
        second = threading.Thread(target=claim, args=(OTHER_ORIGIN,))
        first.start()
        second.start()
        barrier.wait()
        first.join(timeout=5)
        second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(sorted(result[1] for result in results), ["paired", "pairing_conflict"])
        winner = next(result for result in results if result[1] == "paired")
        loser = next(result for result in results if result[1] == "pairing_conflict")
        self.assertEqual(winner[2], TOKEN)
        self.assertIsNone(loser[2])

    def _request(
        self,
        method,
        path,
        *,
        body=None,
        authorization=f"Bearer {TOKEN}",
        origin=ORIGIN,
        content_length=None,
    ):
        headers = {}
        if authorization is not None:
            headers["Authorization"] = authorization
        if origin is not None:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = "application/json"
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            payload = json.loads(response_body) if response_body else None
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

if __name__ == "__main__":
    unittest.main()
