import json
import tempfile
import unittest
from pathlib import Path

from job_source_agent.batch_discovery import LinkedInDiscoveryManifestStore


class LinkedInDiscoveryManifestStoreTests(unittest.TestCase):
    def test_resolve_saves_then_restores_without_discovery(self):
        request = {"keywords": "AI Engineer", "location": "US", "limit": 30, "pages": 5}
        companies = [{"company_name": "A", "linkedin_company_url": "https://linkedin.com/company/a"}]
        with tempfile.TemporaryDirectory() as directory:
            store = LinkedInDiscoveryManifestStore(Path(directory) / "manifest.json")
            saved, first_action = store.resolve(request, lambda: companies)
            restored, second_action = store.resolve(
                request,
                lambda: self.fail("discovery should not run while the manifest is compatible"),
            )

        self.assertEqual(saved, companies)
        self.assertEqual(restored, companies)
        self.assertEqual(first_action, "saved")
        self.assertEqual(second_action, "restored")

    def test_refresh_replaces_the_cohort_atomically(self):
        request = {"keywords": "AI Engineer", "location": "US", "limit": 1, "pages": 1}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            store = LinkedInDiscoveryManifestStore(path)
            store.resolve(request, lambda: [{"company_name": "A"}])
            refreshed, action = store.resolve(
                request,
                lambda: [{"company_name": "B"}],
                refresh=True,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(refreshed, [{"company_name": "B"}])
        self.assertEqual(payload["companies"], refreshed)
        self.assertEqual(action, "refreshed")

    def test_request_mismatch_is_a_safe_cache_miss(self):
        first = {"keywords": "AI", "location": "US", "limit": 1, "pages": 1}
        second = {**first, "keywords": "ML"}
        with tempfile.TemporaryDirectory() as directory:
            store = LinkedInDiscoveryManifestStore(Path(directory) / "manifest.json")
            store.resolve(first, lambda: [{"company_name": "A"}])
            replaced, action = store.resolve(second, lambda: [{"company_name": "B"}])

        self.assertEqual(replaced, [{"company_name": "B"}])
        self.assertEqual(action, "saved")


if __name__ == "__main__":
    unittest.main()
