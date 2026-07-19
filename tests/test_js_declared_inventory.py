import json
import unittest
from urllib.parse import parse_qs

from job_source_agent.js_declared_inventory import (
    discover_js_declared_inventory,
    inspect_js_declared_inventory_transport,
)
from job_source_agent.web import FetchError, Page


LISTING_URL = "https://careers.example.com/search"
ASSET_URL = "https://careers.example.com/assets/job-search.js"
ENDPOINT_URL = "https://careers.example.com/bin/public/jobs"
GET_ENDPOINT_URL = "https://careers.example.com/wp-json/example/jobs"
FETCH_ENDPOINT_URL = "https://careers.example.com/bin/careersSearch"
HTML_ENDPOINT_URL = "https://careers.example.com/ajax.php"
DRUPAL_LISTING_URL = "https://careers.example.com/en/careers/job-openings"
DRUPAL_ENDPOINT_URL = "https://careers.example.com/en/dd_job_search"
JTABLE_ENDPOINT_URL = "https://careers.example.com/Search/SearchResults"


def listing(*scripts: str) -> Page:
    tags = "".join(f'<script src="{value}"></script>' for value in scripts)
    return Page(LISTING_URL, tags, final_url=LISTING_URL)


def fetch_listing(*paths: str) -> Page:
    settings = "".join(
        '<script type="application/json">'
        f'{{"careerSearchPath":"{path}"}}'
        "</script>"
        for path in paths
    )
    html = f'<script src="{ASSET_URL}"></script>{settings}'
    return Page(LISTING_URL, html, final_url=LISTING_URL)


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


def declared_fetch_script() -> str:
    return """
        SearchService.prototype.buildCareersSearchQuery = function(endpoint) {
            var url = endpoint +
                "?pageApp=getCareers&count=20&display=20";
            var hash = this.getHash();
            if (hash.locations) {
                url += "&careerLocation=" + hash.locations;
            }
            if (hash.position) {
                url += "&careerPosition=" + encodeURIComponent(hash.position);
            }
            url += "&isLink=false";
            return url;
        };
        const requestUrl = service.buildCareersSearchQuery(careerSearchPath);
        fetch(requestUrl).then(function (response) {
            return response.json();
        }).then(function (payload) {
            state.total = payload.Total;
            state.results = payload.OpenPositions;
            state.empty = payload.OpenPositions.length === 0;
        });
    """


def declared_jtable_script(page_size: int = 2, endpoint="/Search/SearchResults") -> str:
    return f'''
        window.CONFIG = {{ PAGE_SIZE: {page_size} }};
        function initializeTableData(model) {{
            return {{ Keyword: model.Keyword ?? "" }};
        }}
        function getJobHref(rowId, titleJson) {{
            const title = convertToSlug(titleJson);
            const detailsUrl = `/search/jobdetails/${{title}}/${{rowId}}`;
            return detailsUrl;
        }}
        const jobHref = getJobHref(
            data.record.ID,
            data.record.TrackingObject.TitleJson
        );
        ["jtStartIndex", "jtPageSize", "jtSorting"].forEach((param) => {{
            if (jTableParams[param] !== undefined) {{
                params.set(param, jTableParams[param]);
            }}
        }});
        return $.ajax({{
            url: "{endpoint}?" + params.toString(),
            type: "GET",
            dataType: "json"
        }});
    '''


def drupal_listing(path="/dd_job_search", *, raw_settings=None) -> Page:
    settings = raw_settings
    if settings is None:
        settings = json.dumps(
            {
                "path": {"currentLanguage": "en"},
                "dd_job_search": {"hero_input_search_path": path},
            }
        )
    html = (
        '<script type="application/json" '
        'data-drupal-selector="drupal-settings-json">'
        f"{settings}</script>"
    )
    return Page(DRUPAL_LISTING_URL, html, final_url=DRUPAL_LISTING_URL)


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
    def test_executes_same_origin_declared_ajax_slug_inventory(self):
        page_url = "https://careers.example.com/home/our-job-offers"
        endpoint = "https://careers.example.com/home/ajax_filter_offers"
        page = Page(
            page_url,
            """
            <form method="post" data-ajax="/home/ajax_filter_offers">
              <select name="offerFilter[profession]"><option value="">All</option></select>
              <select name="offerFilter[location]"><option value="">Anywhere</option></select>
            </form>
            <a href="/apply/offer/{{slug}}">{{ title }}</a>
            """,
            final_url=page_url,
        )
        fetcher = RecordingFetcher({
            endpoint: Page(
                endpoint,
                json.dumps({
                    "status": "success",
                    "code": 200,
                    "results": [
                        {
                            "slug": "CJ5G5Z",
                            "title": "Account Executive, NYC",
                            "location": "Americas",
                        }
                    ],
                }),
                final_url=endpoint,
            )
        })

        result = discover_js_declared_inventory(fetcher, page, "Account Executive")

        self.assertEqual(result.trace.status, "verified")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates[0].title, "Account Executive, NYC")
        self.assertEqual(
            result.candidates[0].url,
            "https://careers.example.com/apply/offer/CJ5G5Z",
        )
        self.assertEqual(
            parse_qs(fetcher.requests[0][1].decode(), keep_blank_values=True),
            {
                "offerFilter[profession]": [""],
                "offerFilter[location]": [""],
            },
        )

        trace = inspect_js_declared_inventory_transport(
            RecordingFetcher({}),
            page,
        )
        self.assertEqual(trace.status, "declared")
        self.assertEqual(trace.endpoint_url, endpoint)

    def test_declared_ajax_inventory_rejects_cross_origin_or_missing_detail_template(self):
        page_url = "https://careers.example.com/jobs"
        for html in (
            """
            <form method="post" data-ajax="https://unrelated.example/jobs">
              <select name="location"><option value="">All</option></select>
            </form>
            <a href="/apply/offer/{{slug}}">{{ title }}</a>
            """,
            """
            <form method="post" data-ajax="/ajax/jobs">
              <select name="location"><option value="">All</option></select>
            </form>
            """,
        ):
            with self.subTest(html=html):
                fetcher = RecordingFetcher({})
                result = discover_js_declared_inventory(
                    fetcher,
                    Page(page_url, html, final_url=page_url),
                    "Engineer",
                )
                self.assertEqual(result.trace.status, "transport_not_declared")
                self.assertEqual(fetcher.requests, [])

    def test_declared_ajax_inventory_rejects_sensitive_or_nonempty_default_fields(self):
        page_url = "https://careers.example.com/jobs"
        cases = (
            """
            <form method="post" data-ajax="/ajax/jobs">
              <input name="csrf_token" value="secret">
              <select name="location"><option value="">All</option></select>
            </form>
            <a href="/apply/offer/{{slug}}">{{ title }}</a>
            """,
            """
            <form method="post" data-ajax="/ajax/jobs">
              <select name="location">
                <option value="">All</option>
                <option selected value="4">France</option>
              </select>
            </form>
            <a href="/apply/offer/{{slug}}">{{ title }}</a>
            """,
        )
        for html in cases:
            with self.subTest(html=html):
                fetcher = RecordingFetcher({})
                result = discover_js_declared_inventory(
                    fetcher,
                    Page(page_url, html, final_url=page_url),
                    "Engineer",
                )
                self.assertEqual(result.trace.status, "transport_not_declared")
                self.assertEqual(fetcher.requests, [])

    def test_declared_ajax_inventory_rejects_field_overflow(self):
        page_url = "https://careers.example.com/jobs"
        fields = "".join(
            f'<input name="filter_{index}" value="">' for index in range(33)
        )
        page = Page(
            page_url,
            (
                '<form method="post" data-ajax="/ajax/jobs">'
                f"{fields}</form>"
                '<a href="/apply/offer/{{slug}}">{{ title }}</a>'
            ),
            final_url=page_url,
        )

        result = discover_js_declared_inventory(
            RecordingFetcher({}),
            page,
            "Engineer",
        )

        self.assertEqual(result.trace.status, "transport_not_declared")

    def test_declared_ajax_inventory_rejects_malformed_slug_record(self):
        page_url = "https://careers.example.com/jobs"
        endpoint = "https://careers.example.com/ajax/jobs"
        page = Page(
            page_url,
            """
            <form method="post" data-ajax="/ajax/jobs">
              <select name="location"><option value="">All</option></select>
            </form>
            <a href="/apply/offer/{{slug}}">{{ title }}</a>
            """,
            final_url=page_url,
        )
        fetcher = RecordingFetcher({
            endpoint: Page(
                endpoint,
                json.dumps({
                    "status": "success",
                    "results": [{"slug": "../admin", "title": "Engineer"}],
                }),
                final_url=endpoint,
            )
        })

        result = discover_js_declared_inventory(fetcher, page, "Engineer")

        self.assertEqual(result.trace.status, "invalid_job_postings_payload")
        self.assertEqual(result.candidates, ())

    def test_recovers_and_paginates_declared_jtable_json_inventory(self):
        def record(index, title):
            return {
                "ID": f"job-{index}",
                "TrackingObject": {
                    "TitleJson": title,
                    "LocationNamesJson": ["Kennesaw, Georgia"],
                },
            }

        class PaginatedFetcher(RecordingFetcher):
            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, data, headers))
                if url == ASSET_URL:
                    return asset(declared_jtable_script())
                query = parse_qs(url.split("?", 1)[1])
                offset = int(query["jtStartIndex"][0])
                records = (
                    [record(1, "Mechanical Engineer I"), record(2, "Engineer II")]
                    if offset == 0
                    else [record(3, "Senior Engineer")]
                )
                payload = {
                    "Result": "OK",
                    "Records": records,
                    "TotalRecordCount": 3,
                }
                return Page(url, json.dumps(payload), final_url=url)

        fetcher = PaginatedFetcher({})
        page = listing(ASSET_URL)

        trace = inspect_js_declared_inventory_transport(fetcher, page)
        result = discover_js_declared_inventory(
            fetcher, page, "Mechanical Engineer I"
        )

        self.assertEqual(trace.status, "declared")
        self.assertEqual(trace.endpoint_url, JTABLE_ENDPOINT_URL)
        self.assertEqual(
            trace.request_fields, ("Keyword", "jtStartIndex", "jtPageSize")
        )
        self.assertTrue(result.inventory_complete)
        self.assertEqual([item.title for item in result.candidates], [
            "Mechanical Engineer I", "Engineer II", "Senior Engineer"
        ])
        self.assertEqual(result.candidates[0].location, "Kennesaw, Georgia")
        self.assertEqual(
            result.candidates[0].url,
            "https://careers.example.com/search/jobdetails/"
            "mechanical-engineer-i/job-1",
        )
        inventory_requests = fetcher.requests[-3:]
        self.assertEqual(
            [parse_qs(url.split("?", 1)[1]) for url, _, _ in inventory_requests[1:]],
            [
                {
                    "Keyword": ["Mechanical Engineer I"],
                    "jtStartIndex": ["0"],
                    "jtPageSize": ["2"],
                },
                {
                    "Keyword": ["Mechanical Engineer I"],
                    "jtStartIndex": ["2"],
                    "jtPageSize": ["2"],
                },
            ],
        )

    def test_recovers_one_extra_json_string_encoding_layer(self):
        payload = {
            "Result": "OK",
            "Records": [
                {
                    "ID": "job-1",
                    "TrackingObject": {
                        "TitleJson": "Mechanical Engineer I",
                        "LocationNamesJson": ["Kennesaw, Georgia"],
                    },
                }
            ],
            "TotalRecordCount": 1,
        }
        request_url = (
            JTABLE_ENDPOINT_URL
            + "?Keyword=Mechanical+Engineer+I&jtStartIndex=0&jtPageSize=2"
        )
        fetcher = RecordingFetcher({
            ASSET_URL: asset(declared_jtable_script()),
            request_url: Page(
                request_url,
                json.dumps(json.dumps(payload)),
                final_url=request_url,
            ),
        })

        result = discover_js_declared_inventory(
            fetcher,
            listing(ASSET_URL),
            "Mechanical Engineer I",
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual([item.title for item in result.candidates], [
            "Mechanical Engineer I"
        ])

    def test_rejects_more_than_one_extra_json_string_encoding_layer(self):
        payload = {
            "Result": "OK",
            "Records": [],
            "TotalRecordCount": 0,
        }
        request_url = (
            JTABLE_ENDPOINT_URL
            + "?Keyword=Mechanical+Engineer+I&jtStartIndex=0&jtPageSize=2"
        )
        fetcher = RecordingFetcher({
            ASSET_URL: asset(declared_jtable_script()),
            request_url: Page(
                request_url,
                json.dumps(json.dumps(json.dumps(payload))),
                final_url=request_url,
            ),
        })

        result = discover_js_declared_inventory(
            fetcher,
            listing(ASSET_URL),
            "Mechanical Engineer I",
        )

        self.assertEqual(result.trace.status, "invalid_job_postings_payload")

    def test_bounds_and_stops_repeated_jtable_pages(self):
        repeated = {
            "ID": "same-job",
            "TrackingObject": {"TitleJson": "Engineer", "LocationNamesJson": []},
        }

        class RepeatingFetcher(RecordingFetcher):
            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, data, headers))
                if url == ASSET_URL:
                    return asset(declared_jtable_script(page_size=1))
                payload = {
                    "Result": "OK",
                    "Records": [repeated],
                    "TotalRecordCount": 100,
                }
                return Page(url, json.dumps(payload), final_url=url)

        fetcher = RepeatingFetcher({})
        result = discover_js_declared_inventory(
            fetcher, listing(ASSET_URL), "Engineer"
        )

        self.assertEqual(result.trace.status, "candidate_cap_reached")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(fetcher.requests), 3)

        capped = RepeatingFetcher({})
        capped_result = discover_js_declared_inventory(
            capped, listing(ASSET_URL), "Engineer", max_candidates=1
        )
        self.assertEqual(capped_result.trace.status, "candidate_cap_reached")
        self.assertEqual(len(capped.requests), 2)

    def test_rejects_unsafe_or_incomplete_jtable_declarations(self):
        base = declared_jtable_script()
        variants = (
            base.replace(
                "/Search/SearchResults",
                "https://evil.example/Search/SearchResults",
            ),
            base.replace(
                "/Search/SearchResults",
                "http://careers.example.com/Search/SearchResults",
            ),
            base.replace('type: "GET"', 'type: "POST"'),
            base.replace('dataType: "json"', 'dataType: "html"'),
            base.replace('"jtPageSize"', '"pageSize"'),
            base.replace("TrackingObject.TitleJson", "TrackingObject.TeamJson"),
            base.replace("PAGE_SIZE: 2", "PAGE_SIZE: 5001"),
            base.replace("${title}/${rowId}", "${title}"),
        )
        for script in variants:
            with self.subTest(script=script[-180:]):
                fetcher = RecordingFetcher({ASSET_URL: asset(script)})
                result = discover_js_declared_inventory(
                    fetcher, listing(ASSET_URL), "Engineer"
                )
                self.assertEqual(result.trace.status, "transport_not_declared")
                self.assertEqual(len(fetcher.requests), 1)

    def test_rejects_jtable_redirect_and_malformed_payloads(self):
        request_url = (
            JTABLE_ENDPOINT_URL
            + "?Keyword=Engineer&jtStartIndex=0&jtPageSize=2"
        )
        malformed_payloads = (
            "not-json",
            {"Result": "ERROR", "Records": [], "TotalRecordCount": 0},
            {"Result": "OK", "Records": {}, "TotalRecordCount": 0},
            {"Result": "OK", "Records": [], "TotalRecordCount": True},
            {
                "Result": "OK",
                "Records": [{"ID": "job-1", "TrackingObject": {}}],
                "TotalRecordCount": 1,
            },
        )
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                body = payload if isinstance(payload, str) else json.dumps(payload)
                fetcher = RecordingFetcher(
                    {
                        ASSET_URL: asset(declared_jtable_script()),
                        request_url: Page(request_url, body, final_url=request_url),
                    }
                )
                result = discover_js_declared_inventory(
                    fetcher, listing(ASSET_URL), "Engineer"
                )
                self.assertEqual(result.trace.status, "invalid_job_postings_payload")

        redirect = RecordingFetcher(
            {
                ASSET_URL: asset(declared_jtable_script()),
                request_url: Page(
                    request_url, "{}", final_url="https://evil.example/results"
                ),
            }
        )
        result = discover_js_declared_inventory(
            redirect, listing(ASSET_URL), "Engineer"
        )
        self.assertEqual(result.trace.status, "transport_redirect_rejected")

        oversized = RecordingFetcher(
            {
                ASSET_URL: asset(declared_jtable_script()),
                request_url: Page(
                    request_url, " " * 5_000_001, final_url=request_url
                ),
            }
        )
        result = discover_js_declared_inventory(
            oversized, listing(ASSET_URL), "Engineer"
        )
        self.assertEqual(result.trace.status, "invalid_job_postings_payload")

    def test_recovers_bounded_same_origin_solr_title_inventory(self):
        script = r'''
            var urlPath = "/bin/solrResultServlet?searchType=select&searchTerm=";
            var param = encodeURIComponent(searchInput.value)
                + "&start=" + recordStartIndex
                + "&rows=" + recordRowCount
                + "&wt=json";
            xhr.open("GET", urlPath + param, true);
            var data = JSON.parse(xhr.responseText);
            var recordsList = data.response.docs;
            var itemCount = data.response.numFound;
            recordsList.forEach(function(record, index) {
                render(recordsList[index].url, recordsList[index].title);
            });
        '''
        endpoint = "https://careers.example.com/bin/solrResultServlet"
        request_url = (
            endpoint
            + "?searchType=select&searchTerm=Mechanical+Engineer"
            + "&start=0&rows=5000&wt=json"
        )
        fetcher = RecordingFetcher(
            {
                ASSET_URL: asset(script),
                request_url: Page(
                    request_url,
                    json.dumps(
                        {
                            "response": {
                                "numFound": 1,
                                "docs": [
                                    {
                                        "title": "Mechanical Engineer",
                                        "location": "Detroit, MI",
                                        "url": "/careers/jobs/mechanical-engineer",
                                    }
                                ],
                            }
                        }
                    ),
                    final_url=request_url,
                ),
            }
        )

        result = discover_js_declared_inventory(
            fetcher, listing(ASSET_URL), "Mechanical Engineer"
        )

        self.assertEqual(result.trace.status, "verified")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace.endpoint_url, endpoint)
        self.assertEqual(result.candidates[0].title, "Mechanical Engineer")
        self.assertEqual(
            result.candidates[0].url,
            "https://careers.example.com/careers/jobs/mechanical-engineer",
        )

    def test_rejects_incomplete_solr_declaration_and_truncated_payload(self):
        base = r'''
            var urlPath = "/bin/solrResultServlet?searchType=select&searchTerm=";
            var param = encodeURIComponent(searchInput.value)
                + "&start=" + start + "&rows=" + rows + "&wt=json";
            var data = JSON.parse(xhr.responseText);
            var recordsList = data.response.docs;
            var total = data.response.numFound;
            recordsList.forEach(function(record, index) {
                render(recordsList[index].url, recordsList[index].title);
            });
        '''
        incomplete = base.replace("recordsList[index].title", "recordsList[index].team")
        self.assertEqual(
            discover_js_declared_inventory(
                RecordingFetcher({ASSET_URL: asset(incomplete)}),
                listing(ASSET_URL),
                "Engineer",
            ).trace.status,
            "transport_not_declared",
        )

        endpoint = "https://careers.example.com/bin/solrResultServlet"
        request_url = (
            endpoint
            + "?searchType=select&searchTerm=Engineer"
            + "&start=0&rows=5000&wt=json"
        )
        fetcher = RecordingFetcher(
            {
                ASSET_URL: asset(base),
                request_url: Page(
                    request_url,
                    json.dumps(
                        {
                            "response": {
                                "numFound": 2,
                                "docs": [
                                    {"title": "Engineer", "url": "/jobs/1"}
                                ],
                            }
                        }
                    ),
                    final_url=request_url,
                ),
            }
        )
        result = discover_js_declared_inventory(
            fetcher, listing(ASSET_URL), "Engineer"
        )
        self.assertEqual(result.trace.status, "candidate_cap_reached")
        self.assertFalse(result.inventory_complete)

    def test_recovers_declared_same_origin_filtered_html_inventory(self):
        html = """
            <input name="freetext" type="text">
            <div class="jobresults"></div>
            <script>
              var limit = 48; var start = 0;
              var dataPayload = {limit_page: limit, page_start: start};
              $.ajax({url:'/ajax.php', method:'POST', data:dataPayload,
                success:function(html, status, xhr) {
                  var jobs = $('<div>').html(html).find('li');
                  $('.jobresults').append(jobs);
                  var total = xhr.getResponseHeader('X-Total-Count');
                }});
            </script>
        """
        landing_url = LISTING_URL + "?freetext=Project+Manager"
        response_html = """
            <li><h2><a href="/job/project_manager/ohio/12345/">Project Manager</a></h2>
              <h3><i class="fa fa-map-marker"></i>Toledo, OH</h3></li>
            <li><h2><a href="/job/senior_project_manager/ohio/12346/">Senior Project Manager</a></h2>
              <h3><i class="fa fa-map-marker"></i>Columbus, OH</h3></li>
        """
        fetcher = RecordingFetcher(
            {
                landing_url: Page(landing_url, html, final_url=landing_url),
                HTML_ENDPOINT_URL: Page(
                    HTML_ENDPOINT_URL, response_html, final_url=HTML_ENDPOINT_URL
                ),
            }
        )
        page = Page(LISTING_URL, html, final_url=LISTING_URL)

        trace = inspect_js_declared_inventory_transport(fetcher, page)
        result = discover_js_declared_inventory(fetcher, page, "Project Manager")

        self.assertEqual(trace.status, "declared")
        self.assertEqual(trace.endpoint_url, HTML_ENDPOINT_URL)
        self.assertEqual(
            [item.title for item in result.candidates],
            ["Project Manager", "Senior Project Manager"],
        )
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates[0].location, "Toledo, OH")
        self.assertEqual(
            parse_qs(fetcher.requests[-1][1].decode("utf-8")),
            {
                "freetext": ["Project Manager"],
                "limit_page": ["48"],
                "page_start": ["0"],
            },
        )

    def test_rejects_unbounded_or_weak_inline_html_transport(self):
        base = """
            <input name="freetext"><div class="jobresults"></div>
            <script>var limit=48; var start=0;
            var dataPayload={limit_page:limit,page_start:start};
            $.ajax({url:'/ajax.php',method:'POST',data:dataPayload,
              success:function(html,status,xhr){
                $('<div>').html(html).find('li'); $('.jobresults').append(html);
                xhr.getResponseHeader('X-Total-Count');
              }});</script>
        """
        variants = (
            base.replace("limit=48", "limit=9999"),
            base.replace("X-Total-Count", "X-Count"),
            base.replace("/ajax.php", "https://evil.example/ajax.php"),
            base.replace("method:'POST'", "method:'GET'"),
            base.replace("name=\"freetext\"", "name=\"query\""),
        )
        for html in variants:
            with self.subTest(html=html[-120:]):
                fetcher = RecordingFetcher({})
                trace = inspect_js_declared_inventory_transport(
                    fetcher, Page(LISTING_URL, html, final_url=LISTING_URL)
                )
                self.assertEqual(trace.status, "transport_not_declared")

    def test_inspects_declared_get_transport_without_executing_inventory(self):
        script = """
            var url = window.settings.homeUrl + '/wp-json/example/jobs';
            url += "&keyword=" + encodeURIComponent(this.search.keyword);
            url += "&limit=" + this.jobsToShow;
            axios.get(url).then(function (response) {
                var tmpjobs = response.data;
                tmpjobs.map(function (job) { if (job.title) render(job); });
            });
        """
        fetcher = RecordingFetcher({ASSET_URL: asset(script)})

        trace = inspect_js_declared_inventory_transport(
            fetcher, listing(ASSET_URL)
        )

        self.assertEqual(trace.status, "declared")
        self.assertEqual(trace.endpoint_url, GET_ENDPOINT_URL)
        self.assertEqual(trace.request_fields, ("keyword", "limit"))
        self.assertEqual(fetcher.requests, [(ASSET_URL, None, None)])

    def test_recovers_same_origin_get_inventory_declared_by_first_party_script(self):
        script = """
            var url = window.settings.homeUrl + '/wp-json/example/jobs';
            url += "&keyword=" + encodeURIComponent(this.search.keyword);
            url += "&limit=" + this.jobsToShow;
            axios.get(url).then(function (response) {
                response.data.map(function (job) {
                    if (job.title) render(job.title, job.url);
                });
            });
        """
        request_url = GET_ENDPOINT_URL + "?keyword=HR+Manager&limit=5000"
        fetcher = RecordingFetcher(
            {
                ASSET_URL: asset(script),
                request_url: Page(
                    request_url,
                    json.dumps(
                        [
                            {
                                "title": "HR Manager",
                                "city": "Indianapolis",
                                "state": "IN",
                                "url": (
                                    "https://career4.successfactors.com/sfcareer/"
                                    "jobreqcareer?jobId=123&company=EXAMPLE"
                                ),
                            }
                        ]
                    ),
                    final_url=request_url,
                ),
            }
        )

        result = discover_js_declared_inventory(
            fetcher, listing(ASSET_URL), "HR Manager"
        )

        self.assertEqual(result.trace.status, "verified")
        self.assertEqual(result.trace.endpoint_url, GET_ENDPOINT_URL)
        self.assertEqual(result.trace.request_fields, ("keyword", "limit"))
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "HR Manager")
        self.assertEqual(
            result.candidates[0].url,
            "https://career4.successfactors.com/sfcareer/"
            "jobreqcareer?jobId=123&company=EXAMPLE",
        )
        self.assertEqual(
            fetcher.requests[-1],
            (request_url, None, {"Accept": "application/json"}),
        )

    def test_rejects_unattested_or_credentialed_get_inventory(self):
        base = """
            var url = window.settings.homeUrl + '/wp-json/example/jobs';
            url += "&keyword=" + encodeURIComponent(this.search.keyword);
            url += "&limit=" + this.jobsToShow;
            axios.get(url).then(function (response) {
                response.data.map(function (job) {
                    if (job.title) render(job.title, job.url);
                });
            });
        """
        variants = {
            "credentials": base + "axios.defaults = {withCredentials: true};",
            "no_keyword": base.replace("keyword", "query"),
            "no_limit": base.replace("&limit=", "&page_size="),
            "no_title_field": base.replace("job.title", "job.department"),
            "dynamic_endpoint": base.replace(
                "'/wp-json/example/jobs'", "window.dynamicJobsEndpoint"
            ),
        }
        for name, script in variants.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({ASSET_URL: asset(script)})
                result = discover_js_declared_inventory(
                    fetcher, listing(ASSET_URL), "Engineer"
                )
                self.assertEqual(result.trace.status, "transport_not_declared")
                self.assertEqual(fetcher.requests, [(ASSET_URL, None, None)])

    def test_inspects_bounded_literal_fetch_get_schema(self):
        fetcher = RecordingFetcher({ASSET_URL: asset(declared_fetch_script())})

        trace = inspect_js_declared_inventory_transport(
            fetcher, fetch_listing("/bin/careersSearch")
        )

        self.assertEqual(trace.status, "declared")
        self.assertEqual(trace.endpoint_url, FETCH_ENDPOINT_URL)
        self.assertEqual(
            trace.request_fields,
            ("pageApp", "count", "display", "careerPosition", "isLink"),
        )
        self.assertEqual(fetcher.requests, [(ASSET_URL, None, None)])

    def test_recovers_bounded_literal_fetch_get_inventory(self):
        request_url = (
            FETCH_ENDPOINT_URL
            + "?pageApp=getCareers&count=20&display=20"
            + "&careerPosition=UX+Designer&isLink=false"
        )
        payload = {
            "Total": 1,
            "OpenPositions": [{
                "Title": "UX Designer",
                "Location": "Philadelphia, PA",
                "Url": "/careers/jobs/ux-designer.html",
            }],
        }
        fetcher = RecordingFetcher({
            ASSET_URL: asset(declared_fetch_script()),
            request_url: Page(
                request_url, json.dumps(payload), final_url=request_url
            ),
        })

        result = discover_js_declared_inventory(
            fetcher,
            fetch_listing("/bin/careersSearch"),
            "UX Designer",
        )

        self.assertEqual(result.trace.status, "verified")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace.endpoint_url, FETCH_ENDPOINT_URL)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "UX Designer")
        self.assertEqual(
            result.candidates[0].url,
            "https://careers.example.com/careers/jobs/ux-designer.html",
        )
        self.assertEqual(
            fetcher.requests[-1],
            (request_url, None, {"Accept": "application/json"}),
        )

    def test_rejects_unsafe_or_ambiguous_literal_fetch_get_schema(self):
        base = declared_fetch_script()
        variants = {
            "auth_header": base.replace(
                "fetch(requestUrl)",
                'fetch(requestUrl, {headers: {Authorization: "Bearer secret"}})',
            ),
            "token_control": base.replace(
                'url += "&isLink=false";',
                'url += "&access_token=secret"; url += "&isLink=false";',
            ),
            "unknown_control": base.replace(
                'url += "&isLink=false";',
                'url += "&sort=recent"; url += "&isLink=false";',
            ),
            "unbounded_count": base.replace("count=20", "count=5001"),
            "conflicting_bound": base.replace("display=20", "display=10"),
            "missing_total_attestation": base.replace("payload.Total", "payload.Count"),
            "missing_records_attestation": base.replace(
                "payload.OpenPositions", "payload.Items"
            ),
        }
        for name, script in variants.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({ASSET_URL: asset(script)})
                result = discover_js_declared_inventory(
                    fetcher,
                    fetch_listing("/bin/careersSearch"),
                    "Designer",
                )
                self.assertEqual(result.trace.status, "transport_not_declared")
                self.assertEqual(fetcher.requests, [(ASSET_URL, None, None)])

        unsafe_paths = {
            "cross_origin": "https://api.example.net/bin/careersSearch",
            "token": "/bin/careersSearch?token=secret",
            "oversized": "/" + ("careersSearch" * 1_000),
        }
        for name, path in unsafe_paths.items():
            with self.subTest(path=name):
                fetcher = RecordingFetcher({ASSET_URL: asset(base)})
                result = discover_js_declared_inventory(
                    fetcher, fetch_listing(path), "Designer"
                )
                self.assertEqual(result.trace.status, "transport_not_declared")
                self.assertEqual(fetcher.requests, [(ASSET_URL, None, None)])

        malformed_page = Page(
            LISTING_URL,
            f'<script src="{ASSET_URL}"></script>'
            '<script type="application/json">'
            '{"careerSearchPath":"/bin/careersSearch"'
            "</script>",
            final_url=LISTING_URL,
        )
        fetcher = RecordingFetcher({ASSET_URL: asset(base)})
        result = discover_js_declared_inventory(fetcher, malformed_page, "Designer")
        self.assertEqual(result.trace.status, "transport_not_declared")
        self.assertEqual(fetcher.requests, [(ASSET_URL, None, None)])

        fetcher = RecordingFetcher({ASSET_URL: asset(base)})
        result = discover_js_declared_inventory(
            fetcher,
            fetch_listing("/bin/careersSearch", "/bin/otherCareersSearch"),
            "Designer",
        )
        self.assertEqual(result.trace.status, "ambiguous_transport")
        self.assertEqual(fetcher.requests, [(ASSET_URL, None, None)])

    def test_rejects_literal_fetch_redirect_and_invalid_response_schema(self):
        request_url = (
            FETCH_ENDPOINT_URL
            + "?pageApp=getCareers&count=20&display=20"
            + "&careerPosition=Designer&isLink=false"
        )
        invalid_payloads = {
            "missing_total": {"OpenPositions": []},
            "string_total": {"Total": "1", "OpenPositions": []},
            "missing_positions": {"Total": 0},
            "total_less_than_records": {
                "Total": 0,
                "OpenPositions": [{"title": "Designer", "url": "/jobs/1"}],
            },
        }
        for name, payload in invalid_payloads.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({
                    ASSET_URL: asset(declared_fetch_script()),
                    request_url: Page(
                        request_url, json.dumps(payload), final_url=request_url
                    ),
                })
                result = discover_js_declared_inventory(
                    fetcher,
                    fetch_listing("/bin/careersSearch"),
                    "Designer",
                )
                self.assertEqual(
                    result.trace.status, "invalid_job_postings_payload"
                )
                self.assertEqual(result.candidates, ())

        oversized = json.dumps({
            "Total": 0,
            "OpenPositions": [],
            "padding": "x" * 5_000_000,
        })
        fetcher = RecordingFetcher({
            ASSET_URL: asset(declared_fetch_script()),
            request_url: Page(request_url, oversized, final_url=request_url),
        })
        result = discover_js_declared_inventory(
            fetcher, fetch_listing("/bin/careersSearch"), "Designer"
        )
        self.assertEqual(result.trace.status, "invalid_job_postings_payload")

        fetcher = RecordingFetcher({
            ASSET_URL: asset(declared_fetch_script()),
            request_url: Page(
                request_url,
                json.dumps({"Total": 0, "OpenPositions": []}),
                final_url=request_url + "&redirected=true",
            ),
        })
        result = discover_js_declared_inventory(
            fetcher, fetch_listing("/bin/careersSearch"), "Designer"
        )
        self.assertEqual(result.trace.status, "transport_redirect_rejected")

    def test_drops_malformed_or_oversized_literal_fetch_detail_urls(self):
        request_url = (
            FETCH_ENDPOINT_URL
            + "?pageApp=getCareers&count=20&display=20"
            + "&careerPosition=Designer&isLink=false"
        )
        payload = {
            "Total": 3,
            "OpenPositions": [
                {"title": "Cross origin", "url": "https://evil.example/jobs/1"},
                {"title": "Malformed", "url": "https://[invalid"},
                {"title": "Oversized", "url": "/jobs/" + ("x" * 8_200)},
            ],
        }
        fetcher = RecordingFetcher({
            ASSET_URL: asset(declared_fetch_script()),
            request_url: Page(
                request_url, json.dumps(payload), final_url=request_url
            ),
        })

        result = discover_js_declared_inventory(
            fetcher, fetch_listing("/bin/careersSearch"), "Designer"
        )

        self.assertEqual(result.trace.status, "verified")
        self.assertEqual(result.candidates, ())

    def test_rejects_oversized_literal_fetch_asset_schema(self):
        script = declared_fetch_script() + (" " * 2_000_001)
        fetcher = RecordingFetcher({ASSET_URL: asset(script)})

        result = discover_js_declared_inventory(
            fetcher, fetch_listing("/bin/careersSearch"), "Designer"
        )

        self.assertEqual(result.trace.status, "transport_not_declared")
        self.assertEqual(fetcher.requests, [(ASSET_URL, None, None)])

    def test_recovers_language_prefixed_drupal_elasticsearch_inventory(self):
        payload = {
            "took": 1,
            "timed_out": False,
            "hits": {
                "total": {"value": 2, "relation": "eq"},
                "hits": [
                    {
                        "_index": "jobs",
                        "_source": {
                            "title": "Senior AI Engineer",
                            "url": "/en/jobs/senior-ai-engineer-r123",
                            "city": "Herzogenaurach",
                            "country": "Germany",
                        },
                    },
                    {
                        "_index": "jobs",
                        "_source": {
                            "title": "AI Engineer",
                            "url": "/en/jobs/ai-engineer-r124",
                            "city": "Carlsbad",
                            "country": "United States of America",
                        },
                    },
                ],
            },
        }
        fetcher = RecordingFetcher(
            {
                DRUPAL_ENDPOINT_URL: Page(
                    DRUPAL_ENDPOINT_URL,
                    json.dumps(payload),
                    final_url=DRUPAL_ENDPOINT_URL,
                ),
            }
        )

        result = discover_js_declared_inventory(
            fetcher, drupal_listing(), "AI Engineer"
        )

        self.assertEqual(result.trace.status, "verified")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace.endpoint_url, DRUPAL_ENDPOINT_URL)
        self.assertEqual(result.trace.request_fields, ("q", "area", "location", "from"))
        self.assertEqual(result.trace.assets_considered, ())
        self.assertEqual(
            [(item.title, item.location, item.url) for item in result.candidates],
            [
                (
                    "Senior AI Engineer",
                    "Herzogenaurach, Germany",
                    "https://careers.example.com/en/jobs/senior-ai-engineer-r123",
                ),
                (
                    "AI Engineer",
                    "Carlsbad, United States of America",
                    "https://careers.example.com/en/jobs/ai-engineer-r124",
                ),
            ],
        )
        self.assertEqual(
            fetcher.requests,
            [(
                DRUPAL_ENDPOINT_URL,
                b'{"q":"AI Engineer","area":"all","location":[],"from":0}',
                {"Accept": "application/json", "Content-Type": "application/json"},
            )],
        )

    def test_queries_and_paginates_drupal_inventory_past_default_ten(self):
        first_hits = [
            {"_source": {"title": f"Engineer {index}", "url": f"/en/jobs/{index}"}}
            for index in range(10)
        ]
        target = {
            "_source": {
                "title": "Account Executive (Remote - Midwest Chicago)",
                "url": "/en/jobs/account-executive-r123",
                "city": "Chicago",
                "country": "United States of America",
            }
        }

        class PaginatedFetcher(RecordingFetcher):
            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, data, headers))
                offset = json.loads(data)["from"]
                hits = first_hits if offset == 0 else [target]
                payload = {"hits": {"total": {"value": 11}, "hits": hits}}
                return Page(url, json.dumps(payload), final_url=url)

        fetcher = PaginatedFetcher({})
        result = discover_js_declared_inventory(
            fetcher, drupal_listing(), "Account Executive"
        )

        self.assertEqual(result.trace.status, "verified")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 11)
        self.assertEqual(result.candidates[-1].title, target["_source"]["title"])
        self.assertEqual(
            [json.loads(request[1]) for request in fetcher.requests],
            [
                {"q": "Account Executive", "area": "all", "location": [], "from": 0},
                {"q": "Account Executive", "area": "all", "location": [], "from": 10},
            ],
        )

    def test_bounds_drupal_pagination_by_candidate_limit(self):
        class LargeInventoryFetcher(RecordingFetcher):
            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, data, headers))
                offset = json.loads(data)["from"]
                hits = [
                    {
                        "_source": {
                            "title": f"Engineer {index}",
                            "url": f"/en/jobs/{index}",
                        }
                    }
                    for index in range(offset, offset + 10)
                ]
                payload = {"hits": {"total": {"value": 100}, "hits": hits}}
                return Page(url, json.dumps(payload), final_url=url)

        fetcher = LargeInventoryFetcher({})
        result = discover_js_declared_inventory(
            fetcher,
            drupal_listing(),
            "Engineer",
            max_candidates=15,
        )

        self.assertEqual(result.trace.status, "candidate_cap_reached")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(len(result.candidates), 15)
        self.assertEqual(len(fetcher.requests), 2)

    def test_stops_repeated_drupal_page_without_unbounded_pagination(self):
        repeated_hits = [
            {"_source": {"title": "Engineer", "url": "/en/jobs/engineer"}}
        ]

        class RepeatingFetcher(RecordingFetcher):
            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, data, headers))
                payload = {"hits": {"total": {"value": 500}, "hits": repeated_hits}}
                return Page(url, json.dumps(payload), final_url=url)

        fetcher = RepeatingFetcher({})
        result = discover_js_declared_inventory(
            fetcher, drupal_listing(), "Engineer"
        )

        self.assertEqual(result.trace.status, "candidate_cap_reached")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(fetcher.requests), 2)

    def test_rejects_cross_origin_drupal_pagination_redirect(self):
        fetcher = RecordingFetcher(
            {
                DRUPAL_ENDPOINT_URL: Page(
                    DRUPAL_ENDPOINT_URL,
                    json.dumps({"hits": {"total": 0, "hits": []}}),
                    final_url="https://evil.example.net/dd_job_search",
                ),
            }
        )

        result = discover_js_declared_inventory(
            fetcher, drupal_listing(), "Engineer"
        )

        self.assertEqual(result.trace.status, "transport_redirect_rejected")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(len(fetcher.requests), 1)

    def test_rejects_unsafe_drupal_endpoint_declarations_without_fetching(self):
        unsafe = {
            "cross_origin": "https://api.example.net/dd_job_search",
            "http": "http://careers.example.com/dd_job_search",
            "sensitive": "/dd_job_search?access_token=secret",
            "non_job": "/api/search",
            "missing": None,
            "oversized": "/" + "job_search" * 1_000,
        }
        for name, path in unsafe.items():
            with self.subTest(name=name):
                settings = {"dd_job_search": {}}
                if path is not None:
                    settings["dd_job_search"]["hero_input_search_path"] = path
                fetcher = RecordingFetcher({})
                result = discover_js_declared_inventory(
                    fetcher,
                    drupal_listing(raw_settings=json.dumps(settings)),
                    "Engineer",
                )
                self.assertEqual(result.trace.status, "transport_not_declared")
                self.assertEqual(result.candidates, ())
                self.assertEqual(fetcher.requests, [])

        fetcher = RecordingFetcher({})
        malformed = discover_js_declared_inventory(
            fetcher,
            drupal_listing(raw_settings='{"dd_job_search":'),
            "Engineer",
        )
        self.assertEqual(malformed.trace.status, "transport_not_declared")
        self.assertEqual(fetcher.requests, [])

    def test_rejects_ambiguous_drupal_declarations(self):
        first = drupal_listing().html
        second = drupal_listing("/jobs_search").html
        page = Page(
            DRUPAL_LISTING_URL,
            first + second,
            final_url=DRUPAL_LISTING_URL,
        )
        fetcher = RecordingFetcher({})

        result = discover_js_declared_inventory(fetcher, page, "Engineer")

        self.assertEqual(result.trace.status, "ambiguous_transport")
        self.assertEqual(fetcher.requests, [])

    def test_rejects_malformed_oversized_and_unsafe_elasticsearch_results(self):
        payloads = {
            "missing_hits": {},
            "malformed_hits": {"hits": {"total": 1, "hits": {}}},
            "malformed_total": {"hits": {"total": "1", "hits": []}},
        }
        for name, payload in payloads.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({
                    DRUPAL_ENDPOINT_URL: Page(
                        DRUPAL_ENDPOINT_URL,
                        json.dumps(payload),
                        final_url=DRUPAL_ENDPOINT_URL,
                    ),
                })
                result = discover_js_declared_inventory(
                    fetcher, drupal_listing(), "Engineer"
                )
                self.assertEqual(
                    result.trace.status, "invalid_job_postings_payload"
                )
                self.assertEqual(result.candidates, ())

        oversized = '{"padding":"' + ("x" * 5_000_001) + '"}'
        fetcher = RecordingFetcher(
            {
                DRUPAL_ENDPOINT_URL: Page(
                    DRUPAL_ENDPOINT_URL,
                    oversized,
                    final_url=DRUPAL_ENDPOINT_URL,
                ),
            }
        )
        result = discover_js_declared_inventory(
            fetcher, drupal_listing(), "Engineer"
        )
        self.assertEqual(result.trace.status, "invalid_job_postings_payload")

        payload = {
            "hits": {
                "total": 2,
                "hits": [
                    {"_source": {
                        "title": "Cross Origin",
                        "url": "https://evil.example.net/jobs/123",
                        "city": "Remote",
                        "country": "Remote",
                    }},
                    {"_source": {
                        "title": "Missing URL",
                        "city": "Berlin",
                    }},
                ],
            }
        }
        fetcher = RecordingFetcher(
            {
                DRUPAL_ENDPOINT_URL: Page(
                    DRUPAL_ENDPOINT_URL,
                    json.dumps(payload),
                    final_url=DRUPAL_ENDPOINT_URL,
                ),
            }
        )
        result = discover_js_declared_inventory(
            fetcher, drupal_listing(), "Engineer"
        )
        self.assertEqual(result.trace.status, "verified")
        self.assertEqual(result.candidates, ())

    def test_marks_partial_elasticsearch_page_incomplete(self):
        payload = {
            "hits": {
                "total": {"value": 20},
                "hits": [
                    {"_source": {
                        "title": "Engineer",
                        "url": "/en/jobs/engineer",
                    }},
                ],
            }
        }
        fetcher = RecordingFetcher(
            {
                DRUPAL_ENDPOINT_URL: Page(
                    DRUPAL_ENDPOINT_URL,
                    json.dumps(payload),
                    final_url=DRUPAL_ENDPOINT_URL,
                ),
            }
        )

        result = discover_js_declared_inventory(
            fetcher, drupal_listing(), "Engineer"
        )

        self.assertEqual(result.trace.status, "candidate_cap_reached")
        self.assertFalse(result.inventory_complete)

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
