import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from job_source_agent.composition import FetcherConfig
from job_source_agent.extension_bridge import (
    MAX_REQUEST_BYTES,
    ExtensionBridgeConfig,
    ExtensionBridgeServer,
    ExtensionRunManager,
)


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-bridge-token"
ORIGIN = "chrome-extension://abcdefghijklmnop"


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
        self.assertEqual(payload["status"], "queued")
        self.assertTrue(payload["run_id"])
        self._assert_response_headers(headers)

        status, headers, run = self._request("GET", f"/v1/runs/{payload['run_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(run["run_id"], payload["run_id"])
        self.assertEqual(run["submitted"], 1)
        self.assertIn(run["status"], {"queued", "running", "complete", "failed"})
        self._assert_response_headers(headers)

        status, headers, payload = self._request("GET", "/v1/runs/unknown-run")
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "run_not_found"})
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
        headers = {
            "Authorization": authorization,
            "Origin": origin,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            return response.status, dict(response.getheaders()), json.loads(response_body)
        finally:
            connection.close()

    def _assert_response_headers(self, headers):
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)
        self.assertEqual(headers.get("Vary"), "Origin")
        self.assertEqual(headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
