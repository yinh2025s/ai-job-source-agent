import json
from pathlib import Path
import unittest

from job_source_agent.providers.adp import ADAPTER, ADPAdapter
from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parent / "fixtures" / "adp"
CID = "6d761223-04f6-4d39-a498-276f6ca9389f"
OTHER_CID = "11111111-1111-4111-8111-111111111111"
CC_ID = "19000101_000001"
WFN_BOARD = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    f"recruitment.html?cid={CID}&ccId={CC_ID}&type=MP&lang=en_US&"
    "selectedMenuKey=CurrentOpenings"
)
WFN_API_1 = (
    "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/"
    f"staffing/v1/job-requisitions?cid={CID}&%24skip=1&%24top=20"
)
WFN_API_2 = WFN_API_1.replace("%24skip=1", "%24skip=3")
SRCCAR_BOARD = (
    "https://recruiting.adp.com/srccar/public/nghome.guid?"
    "c=1137307&d=ExternalCareerSite"
)
SRCCAR_DETAIL = SRCCAR_BOARD + "&prc=RMPOD1&r=5001234567890"
SRCCAR_MYJOBS_URL = (
    "https://myjobs.adp.com/examplecareers?"
    "c=1137307&d=ExternalCareerSite&sor=adprm"
)
SRCCAR_MYJOBS_CONFIG = (
    "https://myjobs.adp.com/public/staffing/v1/career-site/examplecareers"
)
SRCCAR_MYJOBS_API = (
    "https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/"
    "job-requisitions/apply-custom-filters?%24orderby=postingDate+desc&"
    "%24select=reqId%2CjobTitle%2CpublishedJobTitle%2CrequisitionLocations&"
    "%24top=20&%24skip=0&tz=America%2FNew_York"
)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class RecordingFetcher:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise FetchError(f"unexpected URL: {url}")
        response = self.responses.pop(0)
        return response if isinstance(response, Page) else Page(url, response, source="fixture")


class ADPAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ADPAdapter()
        self.wfn_board = JobBoard(WFN_BOARD, "adp", f"wfn|{CID}|{CC_ID}|en_US")

    def test_is_native_and_recognizes_both_public_variants(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}
        self.assertIs(native["adp"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        for url in (
            WFN_BOARD,
            WFN_BOARD.replace("&selectedMenuKey=CurrentOpenings", "&jobId=9201055969029_1"),
            SRCCAR_BOARD.replace("nghome.guid", "RTI.home"),
            SRCCAR_DETAIL,
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                board = self.adapter.identify_board(url)
                self.assertIsNotNone(board)
                self.assertTrue(board.replay_safe)

    def test_rejects_malicious_ambiguous_and_cross_host_locators(self):
        rejected = (
            WFN_BOARD.replace("https://", "http://"),
            WFN_BOARD.replace("workforcenow.adp.com", "workforcenow.adp.com.evil.test"),
            WFN_BOARD.replace("https://", "https://user@"),
            WFN_BOARD.replace(CID, "not-a-uuid"),
            WFN_BOARD.replace(CC_ID, "bad"),
            WFN_BOARD + "&token=secret",
            WFN_BOARD + f"&cid={OTHER_CID}",
            WFN_BOARD + "#jobs",
            SRCCAR_BOARD.replace("https://", "http://"),
            SRCCAR_BOARD.replace("recruiting.adp.com", "recruiting.adp.com.evil.test"),
            SRCCAR_BOARD.replace("c=1137307", "c=0"),
            SRCCAR_BOARD + "&token=secret",
            SRCCAR_BOARD + "&c=9999999",
            SRCCAR_BOARD + "#jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_wfn_paginates_complete_public_inventory_and_binds_tenant(self):
        fetcher = RecordingFetcher([fixture("wfn_page_1.json"), fixture("wfn_page_2.json")])
        result = self.adapter.list_jobs(
            fetcher,
            self.wfn_board,
            JobQuery(title="RN HOSPITAL"),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 3)
        self.assertEqual([request[0] for request in fetcher.requests], [WFN_API_1, WFN_API_2])
        self.assertEqual(fetcher.requests[0][2]["Referer"], WFN_BOARD)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "RN HOSPITAL")
        self.assertEqual(candidate.location, "Horizon Health Hospital - Paris, IL, US")
        self.assertIn(f"cid={CID}", candidate.url)
        self.assertIn("jobId=9201055969029_1", candidate.url)
        self.assertEqual(candidate.raw["cid"], CID)
        self.assertTrue(result.trace["exact_title_found"])

    def test_wfn_verified_empty_is_complete(self):
        raw = json.dumps(
            {"jobRequisitions": [], "meta": {"startSequence": 1, "totalNumber": 0}}
        )
        result = self.adapter.list_jobs(
            RecordingFetcher([raw]), self.wfn_board, JobQuery(title="Missing")
        )
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_wfn_fails_closed_on_cross_tenant_redirect_and_tampered_board(self):
        cross_url = WFN_API_1.replace(CID, OTHER_CID)
        cross_page = Page(WFN_API_1, fixture("wfn_page_1.json"), final_url=cross_url)
        redirect = self.adapter.list_jobs(
            RecordingFetcher([cross_page]), self.wfn_board, JobQuery()
        )
        tampered = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard(WFN_BOARD, "adp", f"wfn|{OTHER_CID}|{CC_ID}|en_US"),
            JobQuery(),
        )
        for result in (redirect, tampered):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

    def test_wfn_rejects_duplicate_invalid_and_drifting_inventory(self):
        page = json.loads(fixture("wfn_page_1.json"))
        duplicate = dict(page)
        duplicate["jobRequisitions"] = [page["jobRequisitions"][0]] * 2
        duplicate["meta"] = {"startSequence": 1, "totalNumber": 2}
        invalid_cases = (
            "not-json",
            json.dumps({"jobRequisitions": [], "meta": {"startSequence": 0, "totalNumber": 0}}),
            json.dumps(duplicate),
            json.dumps({
                "jobRequisitions": [{"itemID": "bad", "requisitionTitle": "Role", "requisitionLocations": []}],
                "meta": {"startSequence": 1, "totalNumber": 1},
            }),
        )
        for raw in invalid_cases:
            with self.subTest(raw=raw[-100:]):
                result = self.adapter.list_jobs(
                    RecordingFetcher([raw]), self.wfn_board, JobQuery()
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

        drifted = json.loads(fixture("wfn_page_2.json"))
        drifted["meta"]["totalNumber"] = 4
        result = self.adapter.list_jobs(
            RecordingFetcher([fixture("wfn_page_1.json"), json.dumps(drifted)]),
            self.wfn_board,
            JobQuery(),
        )
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(result.inventory_complete)

    def test_srccar_board_follows_bound_myjobs_inventory_to_canonical_details(self):
        board = self.adapter.identify_board(SRCCAR_BOARD)
        fetcher = RecordingFetcher([
            Page(board.url, "", final_url=SRCCAR_MYJOBS_URL),
            Page(SRCCAR_MYJOBS_CONFIG, fixture("srccar_myjobs_config.json")),
            Page(SRCCAR_MYJOBS_API, fixture("srccar_myjobs_page_1.json")),
        ])
        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Junior Business Intelligence Analyst- Digital"),
        )
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["variant"], "srccar_myjobs_public_requisitions")
        self.assertEqual(
            [request[0] for request in fetcher.requests],
            [board.url, SRCCAR_MYJOBS_CONFIG, SRCCAR_MYJOBS_API],
        )
        self.assertEqual(result.candidates[0].location, "New York, NY, US")
        self.assertEqual(
            result.candidates[0].url,
            "https://myjobs.adp.com/examplecareers/cx/job-details?reqId=5001234567890",
        )
        self.assertTrue(result.trace["exact_title_found"])

    def test_srccar_myjobs_rejects_cross_tenant_redirect_and_incomplete_inventory(self):
        board = self.adapter.identify_board(SRCCAR_BOARD)
        cross = self.adapter.list_jobs(
            RecordingFetcher([Page(board.url, "", final_url=SRCCAR_MYJOBS_URL.replace("c=1137307", "c=9999999"))]),
            board,
            JobQuery(),
        )
        incomplete = self.adapter.list_jobs(
            RecordingFetcher([
                Page(board.url, "", final_url=SRCCAR_MYJOBS_URL),
                Page(SRCCAR_MYJOBS_CONFIG, fixture("srccar_myjobs_config.json")),
                Page(SRCCAR_MYJOBS_API, '{"count": 2, "jobRequisitions": []}'),
            ]),
            board,
            JobQuery(),
        )
        redirected_inventory = self.adapter.list_jobs(
            RecordingFetcher([
                Page(board.url, "", final_url=SRCCAR_MYJOBS_URL),
                Page(SRCCAR_MYJOBS_CONFIG, fixture("srccar_myjobs_config.json")),
                Page(
                    SRCCAR_MYJOBS_API,
                    fixture("srccar_myjobs_page_1.json"),
                    final_url="https://evil.test/inventory",
                ),
            ]),
            board,
            JobQuery(),
        )
        self.assertEqual(cross.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(incomplete.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(redirected_inventory.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(cross.inventory_complete)
        self.assertFalse(incomplete.inventory_complete)
        self.assertFalse(redirected_inventory.inventory_complete)

    def test_srccar_direct_job_requires_same_tenant_structured_identity(self):
        board = self.adapter.identify_board(SRCCAR_DETAIL)
        result = self.adapter.list_jobs(
            RecordingFetcher([Page(board.url, fixture("srccar_detail.html"), source="fixture")]),
            board,
            JobQuery(title="Junior Business Intelligence Analyst- Digital"),
        )
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "single_opening")
        self.assertEqual(result.candidates[0].url, board.url)
        self.assertEqual(result.candidates[0].location, "New York, NY, US")

        cross_url = board.url.replace("c=1137307", "c=9999999")
        cross = self.adapter.list_jobs(
            RecordingFetcher([Page(board.url, fixture("srccar_detail.html"), final_url=cross_url)]),
            board,
            JobQuery(),
        )
        wrong_id = self.adapter.list_jobs(
            RecordingFetcher([Page(board.url, fixture("srccar_detail.html").replace("5001234567890", "5009999999999"))]),
            board,
            JobQuery(),
        )
        self.assertEqual(cross.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(wrong_id.reason_code, "INVALID_STRUCTURED_DATA")

    def test_fetch_failure_is_retryable_and_does_not_publish_candidates(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("HTTP Error 502", status=502, retryable=True)),
            self.wfn_board,
            JobQuery(),
        )
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])


if __name__ == "__main__":
    unittest.main()
