import json
import unittest

from job_source_agent.browser_interaction import JobSearchInteraction
from job_source_agent.job_search_actions import (
    MAX_HELPER_RESPONSE_CHARS,
    JobSearchAction,
    TitleSearchQuery,
    discover_job_search_actions,
    resolve_declared_search_route,
    submit_job_search_action,
    title_search_queries,
    verify_job_search_submission,
)
from job_source_agent.web import Page


class _SearchRouteFetcher:
    def __init__(self, payload, *, final_url=None):
        self.payload = payload
        self.final_url = final_url
        self.requests = []

    def fetch(self, url, data=None, headers=None, *, interaction=None):
        self.requests.append((url, headers))
        body = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return Page(url, body, final_url=self.final_url or url, source="fixture")


class JobSearchActionTests(unittest.TestCase):
    def test_title_search_queries_are_deterministic_and_bounded(self):
        queries = title_search_queries("Registered Nurse (RN) - Apollo Platform")

        self.assertEqual(
            queries,
            (
                TitleSearchQuery("Registered Nurse (RN) - Apollo Platform", "full_title"),
                TitleSearchQuery("Registered Nurse", "core_title"),
                TitleSearchQuery("Apollo Platform", "product_or_team"),
            ),
        )
        self.assertLessEqual(len(queries), 3)

    def test_title_search_queries_reject_unsafe_fallback_variants(self):
        cases = {
            "short product": ("Engineer - AI", ("Engineer - AI",)),
            "generic product": ("Engineer - Platform Team", ("Engineer - Platform Team",)),
            "seniority removed": (
                "Engineer (Senior) - Apollo Platform",
                ("Engineer (Senior) - Apollo Platform", "Apollo Platform"),
            ),
            "level suffix": (
                "Engineer (II) - Payments Team",
                ("Engineer (II) - Payments Team", "Payments Team"),
            ),
        }

        for name, (title, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    tuple(query.value for query in title_search_queries(title)),
                    expected,
                )

    def test_declared_anonymous_get_helper_resolves_canonical_search_route(self):
        page = Page(
            "https://jobs.example.com/jobs/",
            """<script>
            const search = (title) => client.get(
              `/api/search/get-search-results?query=${title}&text=${title}`
            );
            </script>""",
        )
        fetcher = _SearchRouteFetcher(
            {"searchUrl": "/jobs/q-data-analyst/#results"}
        )

        result = resolve_declared_search_route(fetcher, page, "Data Analyst")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.route_url, "https://jobs.example.com/jobs/q-data-analyst/")
        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(
            fetcher.requests[0][0],
            "https://jobs.example.com/api/search/get-search-results"
            "?query=Data+Analyst&text=Data+Analyst",
        )
        self.assertEqual(fetcher.requests[0][1], {"Accept": "application/json"})

    def test_declared_same_origin_search_chunk_resolves_minified_get_helper(self):
        page_url = "https://jobs.example.com/jobs/"
        asset_url = "https://jobs.example.com/assets/Pages-Search.chunk.js"
        helper_url = (
            "https://jobs.example.com/api/search/get-search-results"
            "?query=Data+Analyst&text=Data+Analyst"
        )
        html = (
            '<script data-chunk="Pages-Search" '
            f'src="{asset_url}"></script>'
        )
        script = (
            'client.get("/api/search/get-search-results?".concat(params)'
            '.concat(state.query?"&text=".concat(state.query):""))'
        )

        class AssetFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, headers))
                if url == asset_url:
                    return Page(url, script, final_url=url)
                if url == helper_url:
                    return Page(
                        url,
                        json.dumps({"searchUrl": "/jobs/q-data-analyst/"}),
                        final_url=url,
                    )
                raise AssertionError(url)

        fetcher = AssetFetcher()
        result = resolve_declared_search_route(
            fetcher,
            Page(page_url, html),
            "Data Analyst",
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.route_url, "https://jobs.example.com/jobs/q-data-analyst/")
        self.assertEqual([item[0] for item in fetcher.requests], [asset_url, helper_url])

    def test_named_search_route_chunk_precedes_same_chunk_dependency_budget(self):
        page_url = "https://jobs.example.com/jobs/"
        route_asset = "https://jobs.example.com/search-app/assets/Pages-Search.chunk.js"
        helper_url = (
            "https://jobs.example.com/api/search/get-search-results"
            "?query=Data+Analyst&text=Data+Analyst"
        )
        dependencies = [
            f"https://jobs.example.com/search-app/assets/{number}.chunk.js"
            for number in range(4)
        ]
        html = "".join(
            f'<script data-chunk="Pages-Search" src="{url}"></script>'
            for url in (*dependencies, route_asset)
        )
        route_script = (
            'client.get("/api/search/get-search-results?".concat(params)'
            '.concat(state.query?"&text=".concat(state.query):""),'
            '{cancelToken: request.cancelToken})'
        )

        class RankedFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append(url)
                if url == route_asset:
                    return Page(url, route_script, final_url=url)
                if url == helper_url:
                    return Page(
                        url,
                        json.dumps({"searchUrl": "/jobs/q-data-analyst/"}),
                        final_url=url,
                    )
                return Page(url, "const dependency = true;", final_url=url)

        fetcher = RankedFetcher()
        result = resolve_declared_search_route(
            fetcher,
            Page(page_url, html),
            "Data Analyst",
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(fetcher.requests[:2], [route_asset, helper_url])

    def test_concat_helper_does_not_treat_authorization_as_cancel_token(self):
        page_url = "https://jobs.example.com/jobs/"
        asset_url = "https://jobs.example.com/assets/Pages-Search.chunk.js"
        script = (
            'client.get("/api/search/get-search-results?".concat(params)'
            '.concat(state.query?"&text=".concat(state.query):""),'
            '{Authorization: request.token})'
        )

        class AssetFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(url, script, final_url=url)

        result = resolve_declared_search_route(
            AssetFetcher(),
            Page(
                page_url,
                f'<script data-chunk="Pages-Search" src="{asset_url}"></script>',
            ),
            "Data Analyst",
        )

        self.assertEqual(result.status, "helper_undeclared")

    def test_search_chunk_rejects_cross_origin_and_redirected_assets(self):
        page_url = "https://jobs.example.com/jobs/"
        script = (
            'client.get("/api/search/get-search-results?".concat(params)'
            '.concat(state.query?"&text=".concat(state.query):""))'
        )

        class RedirectFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append(url)
                return Page(url, script, final_url="https://cdn.example.net/search.js")

        cross_origin = resolve_declared_search_route(
            RedirectFetcher(),
            Page(
                page_url,
                '<script data-chunk="Search" src="https://evil.example/search.js"></script>',
            ),
            "Data Analyst",
        )
        redirected_fetcher = RedirectFetcher()
        redirected = resolve_declared_search_route(
            redirected_fetcher,
            Page(
                page_url,
                '<script data-chunk="Search" src="/assets/search.js"></script>',
            ),
            "Data Analyst",
        )

        self.assertEqual(cross_origin.status, "helper_undeclared")
        self.assertEqual(redirected.status, "helper_undeclared")
        self.assertEqual(redirected_fetcher.requests, [
            "https://jobs.example.com/assets/search.js"
        ])

    def test_search_route_helper_failures_are_closed_and_bounded(self):
        declared = (
            "<script>api.get(`"
            "/api/search/get-search-results?query=${title}&text=${title}`);"
            "</script>"
        )
        cases = {
            "undeclared": ("<script>const searchUrl = '/jobs/q-data-analyst/';</script>", {}, None, "helper_undeclared"),
            "search_snippet": (declared.replace("<script>", "<pre>").replace("</script>", "</pre>"), {}, None, "helper_undeclared"),
            "static_example": (declared.replace("${title}", "example"), {}, None, "helper_undeclared"),
            "cross_origin_helper": (declared.replace("/api/", "https://evil.example/api/"), {}, None, "helper_undeclared"),
            "http_helper": (declared.replace("/api/", "http://jobs.example.com/api/"), {}, None, "helper_undeclared"),
            "sensitive_helper": (declared.replace("&text=", "&token=secret&text="), {}, None, "helper_undeclared"),
            "redirect": (declared, {"searchUrl": "/jobs/q-data-analyst/"}, "https://jobs.example.com/login", "helper_redirect_rejected"),
            "malformed": (declared, "not json", None, "response_malformed"),
            "oversize": (declared, "x" * (MAX_HELPER_RESPONSE_CHARS + 1), None, "response_oversize"),
            "cross_origin_route": (declared, {"searchUrl": "https://evil.example/jobs/"}, None, "route_unsafe"),
        }
        for name, (html, payload, final_url, status) in cases.items():
            with self.subTest(name=name):
                fetcher = _SearchRouteFetcher(payload, final_url=final_url)
                result = resolve_declared_search_route(
                    fetcher,
                    Page("https://jobs.example.com/jobs/", html),
                    "Data Analyst",
                )
                self.assertIsNone(result.route_url)
                self.assertEqual(result.status, status)
                self.assertLessEqual(len(fetcher.requests), 1)

    def test_ambiguous_declaration_and_response_are_rejected(self):
        helper = "api.get(`/api/search/get-search-results?query=${q}&text=${q}`);"
        ambiguous_declaration = resolve_declared_search_route(
            _SearchRouteFetcher({"searchUrl": "/jobs/q-data-analyst/"}),
            Page(
                "https://jobs.example.com/jobs/",
                f"<script>{helper}{helper.replace('/api/', '/api/v2/')}</script>",
            ),
            "Data Analyst",
        )
        duplicate_response = resolve_declared_search_route(
            _SearchRouteFetcher(
                '{"searchUrl":"/jobs/one/","searchUrl":"/jobs/two/"}'
            ),
            Page("https://jobs.example.com/jobs/", f"<script>{helper}</script>"),
            "Data Analyst",
        )
        multiple_returned_urls = resolve_declared_search_route(
            _SearchRouteFetcher(
                {
                    "searchUrl": "/jobs/one/",
                    "results": [{"jobUrl": "/jobs/two/"}],
                }
            ),
            Page("https://jobs.example.com/jobs/", f"<script>{helper}</script>"),
            "Data Analyst",
        )

        self.assertEqual(ambiguous_declaration.status, "helper_ambiguous")
        self.assertEqual(duplicate_response.status, "response_malformed")
        self.assertEqual(multiple_returned_urls.status, "response_malformed")

    def test_declared_job_search_preserves_safe_hidden_scope(self):
        page = Page(
            url="https://jobs.example.com/",
            html=(
                '<form action="/search-jobs" method="get">'
                '<input type="search" name="k" placeholder="Search by keyword">'
                '<input type="hidden" name="orgIds" value="1127">'
                '<button type="submit">Search Jobs</button>'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page)

        self.assertEqual(len(result.actions), 1)
        self.assertEqual(
            result.actions[0].request_url("Registered Nurse (RN) - Ambulatory"),
            "https://jobs.example.com/search-jobs"
            "?orgIds=1127&k=Registered+Nurse+%28RN%29+-+Ambulatory",
        )
        self.assertEqual(result.trace[0]["disposition"], "eligible")

    def test_declared_get_form_accepts_allowlisted_hidden_keyword_mirror(self):
        page = Page(
            url="https://careers.example.com/jobs?brand=example&locale=en&tracking=drop",
            html=(
                '<form action="/jobs" method="get" id="search-form">'
                '<input type="hidden" name="brand" value="">'
                '<input type="hidden" name="locale" value="">'
                '<input type="hidden" name="keywords" value="">'
                '<input type="hidden" name="offset" value="0">'
                "</form>"
                '<input type="text" id="filterKeyword" placeholder="Keywords">'
            ),
        )

        result = discover_job_search_actions(page)

        self.assertEqual(len(result.actions), 1)
        self.assertEqual(
            result.actions[0].request_url("Financial Analyst"),
            "https://careers.example.com/jobs"
            "?brand=example&locale=en&offset=0&keywords=Financial+Analyst",
        )
        self.assertEqual(result.trace[0]["disposition"], "eligible")

    def test_arbitrary_hidden_field_is_not_promoted_to_search_query(self):
        page = Page(
            url="https://careers.example.com/jobs",
            html=(
                '<form action="/jobs" method="get">'
                '<input type="hidden" name="filterState" value="">'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page)

        self.assertEqual(result.actions, ())
        self.assertEqual(result.trace[0]["disposition"], "no_job_query_field")

    def test_global_site_post_search_is_not_treated_as_job_search(self):
        page = Page(
            url="https://example.com/en/careers/job-openings",
            html=(
                '<form action="/en/site-search" method="post">'
                '<input name="q" placeholder="Search">'
                '<button>Search</button>'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page)

        self.assertEqual(result.actions, ())
        self.assertEqual(result.trace[0]["disposition"], "unsupported_method")

    def test_declared_post_job_form_builds_bounded_urlencoded_submission(self):
        page = Page(
            url="https://careers.example.com/jobs",
            html=(
                '<form action="/jobs/search" method="post" class="job-search">'
                '<input name="keyword" type="search" placeholder="Job title">'
                '<input name="locale" type="hidden" value="en-US">'
                '<button type="submit">Search Jobs</button></form>'
            ),
        )

        result = discover_job_search_actions(page)

        self.assertEqual(len(result.actions), 1)
        action = result.actions[0]
        self.assertEqual(action.method, "POST")
        self.assertEqual(action.source, "declared_post_form")
        self.assertEqual(action.request_url("Data Analyst"), action.url)
        self.assertEqual(
            action.request_data("Data Analyst"),
            b"locale=en-US&keyword=Data+Analyst",
        )
        self.assertEqual(result.trace[0]["disposition"], "eligible")

    def test_post_form_rejects_sensitive_or_cross_origin_submission(self):
        cases = {
            "sensitive": (
                '<form action="/jobs/search" method="post">'
                '<input name="keyword" placeholder="Job title">'
                '<input type="hidden" name="token" value="secret">'
                '<button>Search Jobs</button></form>',
                "unsupported_method",
            ),
            "cross_origin": (
                '<form action="https://evil.example/jobs/search" method="post">'
                '<input name="keyword" placeholder="Job title">'
                '<button>Search Jobs</button></form>',
                "unsupported_method",
            ),
        }
        for name, (html, expected) in cases.items():
            with self.subTest(name=name):
                result = discover_job_search_actions(
                    Page("https://careers.example.com/jobs", html)
                )
                self.assertEqual(result.actions, ())
                self.assertEqual(result.trace[0]["disposition"], expected)

    def test_submission_accepts_changed_listing_payload_or_route(self):
        initial = Page(
            "https://careers.example.com/jobs",
            '<article class="job-card"><h2>Engineer</h2>'
            '<a href="/job/100-engineer">View job</a></article>',
        )
        action = JobSearchAction(
            "POST",
            "https://careers.example.com/api/jobs/search",
            "search",
            (("action", "filter_jobs"), ("paged", "1")),
            "declared_post_form",
        )
        cases = {
            "listing_fingerprint": Page(
                action.url,
                '<article class="job-card"><h2>Data Analyst</h2>'
                '<a href="/job/200-data-analyst">View job</a></article>',
                final_url="https://careers.example.com/jobs",
            ),
            "payload_fingerprint": Page(
                action.url,
                json.dumps({"results": [{"title": "Data Analyst", "id": 200}]}),
                final_url="https://careers.example.com/jobs",
            ),
            "route": Page(
                action.url,
                "<html><body>No results</body></html>",
                final_url="https://careers.example.com/jobs/data-analyst",
            ),
        }
        for expected, response in cases.items():
            with self.subTest(expected=expected):
                fetcher = _SearchRouteFetcher(response.html, final_url=response.final_url)
                result = submit_job_search_action(
                    fetcher,
                    initial,
                    action,
                    "Data Analyst",
                )
                self.assertEqual(result.status, "submitted")
                self.assertEqual(result.change_kind, expected)
                self.assertEqual(
                    fetcher.requests[0][0],
                    "https://careers.example.com/api/jobs/search",
                )

    def test_submission_rejects_decorative_change_and_unsafe_response(self):
        initial = Page(
            "https://careers.example.com/jobs",
            '<article class="job-card"><h2>Engineer</h2>'
            '<a href="/job/100-engineer">View job</a></article>'
            "<script>window.requestId='one'</script>",
        )
        action = JobSearchAction(
            "POST",
            "https://careers.example.com/jobs/search",
            "keyword",
            (),
            "declared_post_form",
        )
        unchanged_listing = (
            '<article class="job-card"><h2>Engineer</h2>'
            '<a href="/job/100-engineer">View job</a></article>'
            "<script>window.requestId='two'</script>"
        )

        unchanged = submit_job_search_action(
            _SearchRouteFetcher(
                unchanged_listing,
                final_url="https://careers.example.com/jobs",
            ),
            initial,
            action,
            "Data Analyst",
        )
        unsafe = submit_job_search_action(
            _SearchRouteFetcher("{}", final_url="https://evil.example/jobs"),
            initial,
            action,
            "Data Analyst",
        )

        self.assertEqual(unchanged.status, "transport_unchanged")
        self.assertIsNone(unchanged.page)
        self.assertEqual(unsafe.status, "transport_unsafe_response")

        browser_result = verify_job_search_submission(
            initial,
            Page(
                initial.url,
                unchanged_listing,
                final_url=initial.url,
            ),
            request_url=initial.url,
        )
        self.assertEqual(browser_result.status, "transport_unchanged")

    def test_post_api_exposes_unique_verified_embedded_listing_html(self):
        initial = Page("https://careers.example.com/jobs", "<p>Loading jobs</p>")
        action = JobSearchAction(
            "POST",
            "https://careers.example.com/wp-admin/admin-ajax.php",
            "search",
            (("action", "filter_jobs"), ("nonce", "public-nonce"), ("paged", "1")),
            "declared_post_api",
        )
        embedded = (
            '<article class="job-card"><h2>Data Analyst</h2>'
            '<a href="/job/200-data-analyst">View job</a></article>'
        )
        fetcher = _SearchRouteFetcher(
            {"success": True, "data": {"html": embedded, "total": 1}}
        )

        result = submit_job_search_action(fetcher, initial, action, "Data Analyst")

        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.change_kind, "listing_fingerprint")
        self.assertIsNotNone(result.page)
        self.assertEqual(result.page.final_url, initial.url)
        self.assertEqual(result.page.html, embedded)
        self.assertIn("declared_post_api_html", result.page.source)

    def test_post_api_error_payload_is_typed_unchanged_transport(self):
        initial = Page("https://careers.example.com/jobs", "<p>Loading jobs</p>")
        action = JobSearchAction(
            "POST",
            "https://careers.example.com/api/jobs/search",
            "search",
            (),
            "declared_post_api",
        )

        result = submit_job_search_action(
            _SearchRouteFetcher(
                {"success": False, "error": "invalid request"},
                final_url=initial.url,
            ),
            initial,
            action,
            "Data Analyst",
        )

        self.assertEqual(result.status, "transport_unchanged")
        self.assertIsNone(result.page)

    def test_js_only_search_is_traced_but_not_faked_as_get(self):
        page = Page(
            url="https://jobs.example.com/jobs/",
            html=(
                "<form>"
                '<input type="search" name="searchField" '
                'placeholder="job title or keyword">'
                '<button type="button">Search jobs</button>'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page)

        self.assertEqual(result.actions, ())
        self.assertEqual(result.trace[0]["disposition"], "interactive_only")

    def test_js_only_job_title_search_emits_exact_fill_descriptor(self):
        page = Page(
            url="https://www.randstadusa.com/jobs/",
            html=(
                '<form id="job-search-form">'
                '<input id="job-title" name="jobTitle" type="text" '
                'placeholder="Search by job title or keyword">'
                '<button type="button"><span>Search</span></button>'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page, "Data Analyst")

        self.assertEqual(result.actions, ())
        self.assertEqual(
            result.interactive_actions,
            (
                JobSearchInteraction(
                    form_ordinal=0,
                    query_name="jobTitle",
                    query_id="job-title",
                    target_title="Data Analyst",
                    submit_text="Search",
                ),
            ),
        )
        self.assertEqual(result.trace[0]["disposition"], "interactive_eligible")

    def test_hcs_unnamed_job_title_input_and_button_like_span_are_discovered(self):
        page = Page(
            url="https://careers.hcs.example/jobs/",
            html=(
                '<form class="job-search">'
                '<input type="text" placeholder="Job Title">'
                '<span class="action">Find Jobs</span>'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page, "Registered Nurse")

        self.assertEqual(result.actions, ())
        self.assertEqual(
            result.interactive_actions,
            (
                JobSearchInteraction(
                    form_ordinal=0,
                    query_name=None,
                    query_id=None,
                    query_placeholder="Job Title",
                    target_title="Registered Nurse",
                    submit_text="Find Jobs",
                    submit_tag="span",
                ),
            ),
        )
        self.assertEqual(result.trace[0]["disposition"], "interactive_eligible")

    def test_valid_get_action_suppresses_interactive_discovery(self):
        page = Page(
            url="https://jobs.example.com/jobs/",
            html=(
                '<form action="/jobs/search" method="get">'
                '<input name="q" type="search" placeholder="Job title">'
                '<button type="submit">Search</button></form>'
                '<form><input name="jobTitle" placeholder="Job title">'
                '<button type="button">Search</button></form>'
            ),
        )

        result = discover_job_search_actions(page, "Data Analyst")

        self.assertEqual(len(result.actions), 1)
        self.assertEqual(result.interactive_actions, ())

    def test_ambiguous_or_unsafe_interactive_forms_fail_closed(self):
        cases = {
            "multiple_unclassified_fields": (
                '<form><input name="jobTitle" placeholder="Job title">'
                '<input name="department" placeholder="Department">'
                '<button type="button">Search</button></form>',
                "interactive_ambiguous_fields",
            ),
            "multiple_search_buttons": (
                '<form method="post"><input name="jobTitle" placeholder="Job title">'
                '<button type="button">Search</button>'
                '<button type="submit">Search Jobs</button></form>',
                "interactive_ambiguous_buttons",
            ),
            "sensitive_field": (
                '<form method="post"><input name="jobTitle" placeholder="Job title">'
                '<input type="hidden" name="token" value="secret">'
                '<button type="submit">Search</button></form>',
                "interactive_sensitive_fields",
            ),
            "cross_origin": (
                '<form method="post" action="https://evil.example/jobs/search">'
                '<input name="jobTitle" placeholder="Job title">'
                '<button type="submit">Search</button></form>',
                "interactive_unsafe_action",
            ),
        }

        for name, (html, disposition) in cases.items():
            with self.subTest(name=name):
                result = discover_job_search_actions(
                    Page("https://jobs.example.com/jobs/", html),
                    "Data Analyst",
                )
                self.assertEqual(result.actions, ())
                self.assertEqual(result.interactive_actions, ())
                self.assertEqual(result.trace[0]["disposition"], disposition)

    def test_title_search_allows_one_unfilled_location_scope(self):
        page = Page(
            url="https://careers.example/jobs",
            html=(
                '<form class="job-search">'
                '<input type="text" placeholder="Job Title">'
                '<input type="text" placeholder="Location, City, State or Zip">'
                '<span class="action">Find Jobs</span>'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page, "Registered Nurse")

        self.assertEqual(len(result.interactive_actions), 1)
        interaction = result.interactive_actions[0]
        self.assertEqual(interaction.query_placeholder, "Job Title")
        self.assertEqual(interaction.submit_text, "Find Jobs")
        self.assertEqual(result.trace[0]["disposition"], "interactive_eligible")

    def test_primary_search_ignores_scope_checkbox_and_non_search_controls(self):
        page = Page(
            url="https://careers.example/jobs",
            html=(
                '<form class="search-form">'
                '<input type="search" name="searchField" '
                'placeholder="job title or keyword">'
                '<input type="text" name="locationSearch" '
                'placeholder="location or zip code">'
                '<input type="checkbox" name="isRemote">'
                '<button type="button">Clear</button>'
                '<button type="button">Search</button>'
                '<button type="button">Search 5997 jobs</button>'
                "</form>"
                '<form class="job-alerts-form">'
                '<input name="query" placeholder="job title or keyword">'
                '<input name="email" placeholder="your email address">'
                '<button type="button">Confirm job alert</button>'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page, "Data Analyst")

        self.assertEqual(len(result.interactive_actions), 1)
        interaction = result.interactive_actions[0]
        self.assertEqual(interaction.form_ordinal, 0)
        self.assertEqual(interaction.query_name, "searchField")
        self.assertEqual(interaction.submit_text, "Search")

    def test_non_job_interactive_search_fails_closed(self):
        page = Page(
            url="https://example.com/support/",
            html=(
                '<form action="/support/search" method="post">'
                '<input name="jobTitle" placeholder="Job title">'
                '<button type="submit">Search</button></form>'
            ),
        )

        result = discover_job_search_actions(page, "Data Analyst")

        self.assertEqual(result.interactive_actions, ())
        self.assertEqual(
            result.trace[0]["disposition"],
            "interactive_non_job_search",
        )

    def test_location_input_and_arbitrary_find_jobs_span_are_rejected(self):
        cases = {
            "location_input": (
                '<form><input placeholder="Location">'
                '<span class="action">Find Jobs</span></form>',
                "interactive_ambiguous_fields",
            ),
            "arbitrary_span": (
                '<form><input placeholder="Job Title">'
                '<span>Find Jobs</span></form>',
                "interactive_ambiguous_buttons",
            ),
        }

        for name, (html, disposition) in cases.items():
            with self.subTest(name=name):
                result = discover_job_search_actions(
                    Page("https://jobs.example.com/jobs/", html),
                    "Data Analyst",
                )
                self.assertEqual(result.interactive_actions, ())
                self.assertEqual(result.trace[0]["disposition"], disposition)

    def test_sensitive_and_cross_origin_actions_are_rejected(self):
        page = Page(
            url="https://jobs.example.com/jobs/",
            html=(
                '<form action="https://evil.example/search-jobs">'
                '<input name="q"></form>'
                '<form action="/search-jobs">'
                '<input name="q"><input type="hidden" name="token" value="secret">'
                "</form>"
            ),
        )

        result = discover_job_search_actions(page)

        self.assertEqual(result.actions, ())
        self.assertEqual(
            [item["disposition"] for item in result.trace],
            ["unsafe_action", "sensitive_fields"],
        )


if __name__ == "__main__":
    unittest.main()
