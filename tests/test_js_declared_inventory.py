import json
import unittest
from urllib.parse import parse_qs

from job_source_agent.js_declared_inventory import discover_js_declared_inventory
from job_source_agent.web import FetchError, Page


LISTING_URL = "https://careers.example.com/search"
ASSET_URL = "https://careers.example.com/assets/job-search.js"
ENDPOINT_URL = "https://careers.example.com/bin/public/jobs"


def listing(*scripts: str) -> Page:
    tags = "".join(f'<script src="{value}"></script>' for value in scripts)
    return Page(LISTING_URL, tags, final_url=LISTING_URL)


def declared_script(endpoint: str = "/bin/public/jobs", extra: str = "") -> str:
    return f"""
        const pageLimit = 25;
        $.ajax({{
            url: "{endpoint}",
            type: "POST",
            data: {{
                searchMode: "search",
                searchTerm: requestedTitle,
                paginationStart: 0,
                paginationLimit: pageLimit
            }},
            {extra}
            success: function (response) {{
                response.jobPostings.forEach(renderJob);
            }}
        }});
    """


class RecordingFetcher:
    def __init__(self, values):
        self.values = values
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        value = self.values.get(url)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise FetchError(f"missing fixture: {url}")
        return value


def asset(body: str, *, final_url: str = ASSET_URL) -> Page:
    return Page(ASSET_URL, body, final_url=final_url)


def response(payload, *, final_url: str = ENDPOINT_URL) -> Page:
    return Page(ENDPOINT_URL, json.dumps(payload), final_url=final_url)


class JSDeclaredInventoryTests(unittest.TestCase):
    def test_recovers_declared_native_xhr_form_transport(self):
        script = '''
            let searchMode = "search";
            let model = {
                "url": "/bin/public/jobs?",
                "urlSearch": "/bin/public/jobs?"
            };
            function makeRequest(url, postParameters) {
                const xhr = new XMLHttpRequest();
                xhr.open("POST", url, true);
                xhr.setRequestHeader(
                    "Content-type", "application/x-www-form-urlencoded"
                );
                postParameters += "&searchMode=" + searchMode;
                xhr.send(postParameters + "&jobFormat=" + jobFormat);
            }
            function search(title) {
                const postParameters = "searchTerm=" + encodeURIComponent(title);
                return makeRequest(model.url, postParameters);
            }
            function render(result) { return result.jobPostings; }
        '''
        endpoint = ENDPOINT_URL
        fetcher = RecordingFetcher({
            ASSET_URL: asset(script),
            endpoint: Page(
                endpoint,
                json.dumps({"jobPostings": [{
                    "title": "AI Engineer",
                    "location": "Austin, TX",
                    "url": "/jobs/ai-engineer",
                }]}),
                final_url=endpoint,
            ),
        })

        result = discover_js_declared_inventory(
            fetcher,
            listing(ASSET_URL),
            "AI Engineer",
        )

        self.assertEqual(result.trace.status, "verified")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            parse_qs(fetcher.requests[1][1].decode()),
            {
                "searchTerm": ["AI Engineer"],
                "searchMode": ["search"],
                "jobFormat": ["undefined"],
            },
        )

    def test_recovers_tata_shaped_literal_post_without_provider_special_case(self):
        payload = {
            "data": {
                "jobPostings": [
                    {
                        "title": "AI Engineer",
                        "location": "Pune, India",
                        "url": "/jobs/ai-engineer",
                    },
                    {
                        "jobTitle": "Platform Engineer",
                        "jobLocation": "Remote",
                        "jobUrl": "https://jobs.lever.co/example/1234",
                    },
                ]
            }
        }
        fetcher = RecordingFetcher(
            {ASSET_URL: asset(declared_script()), ENDPOINT_URL: response(payload)}
        )

        result = discover_js_declared_inventory(fetcher, listing(ASSET_URL), "AI Engineer")

        self.assertEqual([item.title for item in result.candidates], ["AI Engineer", "Platform Engineer"])
        self.assertEqual(result.candidates[0].location, "Pune, India")
        self.assertEqual(result.candidates[0].url, "https://careers.example.com/jobs/ai-engineer")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace.status, "verified")
        self.assertFalse(result.trace.retryable)
        self.assertFalse(result.trace.blocked)
        post_url, body, headers = fetcher.requests[1]
        self.assertEqual(post_url, ENDPOINT_URL)
        self.assertEqual(
            parse_qs(body.decode()),
            {
                "searchMode": ["search"],
                "searchTerm": ["AI Engineer"],
                "paginationStart": ["0"],
                "paginationLimit": ["25"],
            },
        )
        self.assertEqual(set(headers), {"Accept", "Content-Type"})
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Authorization", headers)

    def test_accepts_one_named_literal_data_object_and_resets_dynamic_offset(self):
        script = """
            const pageLimit = 50;
            const requestData = {
                searchTerm: title,
                searchMode: "search",
                paginationStart: currentOffset,
                paginationLimit: pageLimit
            };
            $.ajax({
                url: "/bin/public/jobs",
                method: "POST",
                data: requestData,
                success: result => render(result.jobPostings)
            });
        """
        fetcher = RecordingFetcher(
            {
                ASSET_URL: asset(script),
                ENDPOINT_URL: response({"jobPostings": []}),
            }
        )

        result = discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Architect")

        self.assertEqual(result.trace.status, "verified")
        self.assertEqual(
            parse_qs(fetcher.requests[1][1].decode()),
            {
                "searchTerm": ["Architect"],
                "searchMode": ["search"],
                "paginationStart": ["0"],
                "paginationLimit": ["50"],
            },
        )

    def test_fetches_only_bounded_same_site_javascript(self):
        unrelated = "https://careers.example.com/assets/vendor.js"
        fourth = "https://careers.example.com/assets/z.js"
        alphabetic_first = "https://careers.example.com/assets/fourth.js"
        fetcher = RecordingFetcher(
            {
                ASSET_URL: asset("const nope = true;"),
                unrelated: Page(unrelated, "", final_url=unrelated),
                fourth: Page(fourth, "", final_url=fourth),
                alphabetic_first: Page(alphabetic_first, "", final_url=alphabetic_first),
            }
        )

        result = discover_js_declared_inventory(
            fetcher,
            listing(
                "https://cdn.example.net/evil.js",
                unrelated,
                fourth,
                ASSET_URL,
                alphabetic_first,
            ),
            "Engineer",
        )

        self.assertEqual(result.trace.status, "transport_not_declared")
        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(fetcher.requests[0][0], ASSET_URL)
        self.assertNotIn("https://cdn.example.net/evil.js", [item[0] for item in fetcher.requests])

    def test_fails_closed_for_unproven_transport_shapes(self):
        unsafe_scripts = {
            "cross_origin": declared_script("https://api.example.net/jobs"),
            "credentials": declared_script(extra='credentials: "include",'),
            "sensitive_endpoint": declared_script("/bin/jobs?access_token=secret"),
            "dynamic_url": declared_script("${apiBase}/jobs"),
            "missing_response_container": declared_script().replace("jobPostings", "items"),
            "missing_search_mode": declared_script().replace("searchMode", "mode"),
            "dynamic_field": declared_script().replace('searchMode: "search"', "searchMode: runtimeMode"),
            "unbounded": declared_script().replace("paginationLimit: pageLimit", "pageNumber: 1"),
            "oversized_page": declared_script().replace("const pageLimit = 25", "const pageLimit = 5001"),
            "sensitive_field": declared_script().replace("paginationStart: 0", 'token: "secret"'),
        }
        for name, script in unsafe_scripts.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({ASSET_URL: asset(script)})
                result = discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Engineer")
                self.assertEqual(result.candidates, ())
                self.assertEqual(result.trace.status, "transport_not_declared")
                self.assertEqual(len(fetcher.requests), 1)

    def test_rejects_asset_and_transport_redirects(self):
        with self.subTest(kind="asset"):
            fetcher = RecordingFetcher(
                {ASSET_URL: asset(declared_script(), final_url=ASSET_URL + "?redirected=1")}
            )
            result = discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Engineer")
            self.assertEqual(result.trace.status, "asset_redirect_rejected")
            self.assertEqual(len(fetcher.requests), 1)

        with self.subTest(kind="transport"):
            fetcher = RecordingFetcher(
                {
                    ASSET_URL: asset(declared_script()),
                    ENDPOINT_URL: response({"jobPostings": []}, final_url=ENDPOINT_URL + "/redirect"),
                }
            )
            result = discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Engineer")
            self.assertEqual(result.trace.status, "transport_redirect_rejected")

    def test_returns_typed_blocked_and_retryable_trace(self):
        for status, expected_status, retryable in (
            (403, "blocked", False),
            (429, "rate_limited", True),
        ):
            with self.subTest(status=status):
                fetcher = RecordingFetcher(
                    {
                        ASSET_URL: asset(declared_script()),
                        ENDPOINT_URL: FetchError("denied", status=status),
                    }
                )
                result = discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Engineer")
                self.assertEqual(result.trace.status, expected_status)
                self.assertTrue(result.trace.blocked)
                self.assertEqual(result.trace.retryable, retryable)
                self.assertFalse(result.inventory_complete)

    def test_restricts_candidate_urls_and_caps_results(self):
        jobs = [
            {"title": f"Engineer {index}", "url": f"/jobs/{index}"}
            for index in range(5_002)
        ]
        jobs.extend(
            [
                {"title": "HTTP", "url": "http://careers.example.com/jobs/http"},
                {"title": "Private", "url": "https://127.0.0.1/jobs/private"},
                {"title": "Other", "url": "https://other.example.net/jobs/other"},
                {"title": "Secret", "url": "/jobs/secret?token=value"},
            ]
        )
        fetcher = RecordingFetcher(
            {
                ASSET_URL: asset(declared_script()),
                ENDPOINT_URL: response({"jobPostings": jobs}),
            }
        )

        result = discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Engineer")

        self.assertEqual(len(result.candidates), 5_000)
        self.assertEqual(result.trace.status, "candidate_cap_reached")
        self.assertFalse(result.inventory_complete)

    def test_invalid_payload_and_limits_fail_closed(self):
        fetcher = RecordingFetcher(
            {
                ASSET_URL: asset(declared_script()),
                ENDPOINT_URL: Page(ENDPOINT_URL, "not json", final_url=ENDPOINT_URL),
            }
        )
        result = discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Engineer")
        self.assertEqual(result.trace.status, "invalid_job_postings_payload")

        with self.assertRaises(ValueError):
            discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Engineer", max_assets=4)
        with self.assertRaises(ValueError):
            discover_js_declared_inventory(fetcher, listing(ASSET_URL), "Engineer", max_candidates=5001)


if __name__ == "__main__":
    unittest.main()
