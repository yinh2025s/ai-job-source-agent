import unittest

from job_source_agent.web import MAX_EXTRACTED_LINKS, Page, extract_links


class LinkExtractionTests(unittest.TestCase):
    def test_nested_anchor_text_preserves_clean_node_boundaries(self):
        page = Page(
            url="https://awesomemotive.com/careers/",
            html='''
                <a href="https://apply.workable.com/awesomemotive/j/ABC123/">
                  <h4>AI Developer</h4><span>Remote</span>
                </a>
            ''',
        )

        links = extract_links(page)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].text, "AI Developer Remote")

    def test_extracts_typed_hidden_and_redirect_urls(self):
        page = Page(
            url="https://example.com/careers",
            final_url="https://example.com/jobs",
            html='''
                <iframe src="https://acme.eightfold.ai/careers"></iframe>
                <div data-job-board-url="https://boards.greenhouse.io/acme"></div>
                <script>{"url":"https:\\/\\/acme.fa.us2.oraclecloud.com\\u002FhcmUI\\u002FCandidateExperience\\u002Fen\\u002Fsites\\u002FCX_1"}</script>
            ''',
        )

        links = extract_links(page)
        by_origin = {link.origin: link.url for link in links}

        self.assertEqual(by_origin["iframe_src"], "https://acme.eightfold.ai/careers")
        self.assertEqual(by_origin["data_attribute"], "https://boards.greenhouse.io/acme")
        self.assertIn("oraclecloud.com/hcmUI/CandidateExperience", by_origin["embedded_url"])
        self.assertEqual(by_origin["redirect_final_url"], "https://example.com/jobs")

    def test_ignores_commented_legacy_links_and_provider_configuration(self):
        page = Page(
            url="https://example.com/careers",
            html=r'''
                <!-- <iframe src="https://legacy.example.com/jobs"></iframe> -->
                <!-- https://jobs.legacy.example.com/archive -->
                <!--
                  <script>
                    const company = "retired-greenhouse";
                    fetch(`https://boards-api.greenhouse.io/v1/boards/${company}/jobs`);
                  </script>
                  <div id="lever-jobs-container"></div>
                  <script>
                    window.leverJobsOptions = { accountName: 'retired-lever' };
                  </script>
                -->
                <script>
                  {"url":"https:\/\/active.example.com\/jobs"}
                </script>
            ''',
        )

        links = extract_links(page)
        urls = [link.url for link in links]

        self.assertNotIn("https://legacy.example.com/jobs", urls)
        self.assertNotIn("https://jobs.legacy.example.com/archive", urls)
        self.assertNotIn("https://job-boards.greenhouse.io/retired-greenhouse", urls)
        self.assertNotIn("https://jobs.lever.co/retired-lever", urls)
        self.assertIn("https://active.example.com/jobs", urls)

    def test_embedded_extraction_is_bounded(self):
        html = " ".join(f"https://jobs.example.com/jobs/{index}" for index in range(MAX_EXTRACTED_LINKS + 50))
        self.assertLessEqual(len(extract_links(Page(url="https://example.com", html=html))), MAX_EXTRACTED_LINKS)

    def test_extracts_form_action_and_canonicalizes_greenhouse_embed(self):
        page = Page(
            url="https://careers.example.com",
            html=(
                '<form action="https://acme.eightfold.ai/careers/search"></form>'
                '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
            ),
        )

        links = extract_links(page)

        self.assertIn(
            ("form_action", "https://acme.eightfold.ai/careers/search"),
            [(link.origin, link.url) for link in links],
        )
        self.assertIn(
            "https://job-boards.greenhouse.io/acme",
            [link.url for link in links],
        )

    def test_derives_greenhouse_board_from_bounded_template_configuration(self):
        page = Page(
            url="https://example.com/careers",
            html="""
                <script>
                  const company = "acme-work";
                  fetch(`https://boards-api.greenhouse.io/v1/boards/${company}/jobs?content=true`);
                </script>
            """,
        )

        links = extract_links(page)

        self.assertIn(
            ("derived_provider_config", "https://job-boards.greenhouse.io/acme-work"),
            [(link.origin, link.url) for link in links],
        )

    def test_derives_lever_board_from_embed_configuration(self):
        page = Page(
            url="https://example.com/careers",
            html="""
                <div id="lever-jobs-container"></div>
                <script>window.leverJobsOptions = { accountName: 'influur', includeCss: true };</script>
                <script src="https://cdn.example/lever-jobs-embed/index.js"></script>
            """,
        )

        links = extract_links(page)

        self.assertIn(
            ("derived_provider_config", "https://jobs.lever.co/influur"),
            [(link.origin, link.url) for link in links],
        )

    def test_does_not_derive_lever_board_without_embed_evidence(self):
        page = Page(
            url="https://example.com/account",
            html="<script>window.leverJobsOptions = { accountName: 'untrusted' };</script>",
        )

        self.assertNotIn(
            "https://jobs.lever.co/untrusted",
            [link.url for link in extract_links(page)],
        )

    def test_provider_configuration_has_priority_when_page_link_budget_is_full(self):
        anchors = "".join(
            f'<a href="/docs/{index}">Doc {index}</a>'
            for index in range(MAX_EXTRACTED_LINKS + 10)
        )
        html = anchors + """
            <script>
              const company = "acme";
              fetch(`https://boards-api.greenhouse.io/v1/boards/${company}/jobs`);
            </script>
        """

        links = extract_links(Page(url="https://example.com/careers", html=html))

        self.assertEqual(len(links), MAX_EXTRACTED_LINKS)
        self.assertEqual(links[0].url, "https://job-boards.greenhouse.io/acme")

    def test_retains_late_workday_and_explicit_current_openings_within_cap(self):
        ordinary_anchors = "".join(
            f'<a href="/about/{index}">About {index}</a>'
            for index in range(MAX_EXTRACTED_LINKS + 20)
        )
        workday = "https://bbva.wd3.myworkdayjobs.com/BBVA"
        openings = "https://www.connoisseurmedia.com/careers/current-openings/"
        html = ordinary_anchors + (
            f'<a href="{workday}">Careers</a>'
            f'<a href="{openings}">View all current openings</a>'
        )

        links = extract_links(Page(url="https://www.connoisseurmedia.com/careers/", html=html))
        urls = [link.url for link in links]

        self.assertEqual(len(links), MAX_EXTRACTED_LINKS)
        self.assertEqual(urls[:2], [workday, openings])
        self.assertNotIn(f"https://www.connoisseurmedia.com/about/{MAX_EXTRACTED_LINKS - 1}", urls)

    def test_retains_late_job_offers_command_within_cap(self):
        ordinary_anchors = "".join(
            f'<a href="/about/{index}">About {index}</a>'
            for index in range(MAX_EXTRACTED_LINKS + 20)
        )
        offers = "https://careers.example.com/en/annonces"
        html = ordinary_anchors + f'<a href="{offers}">Our job offers</a>'

        links = extract_links(
            Page(url="https://careers.example.com/en/index.html", html=html)
        )

        self.assertEqual(len(links), MAX_EXTRACTED_LINKS)
        self.assertEqual(links[0].url, offers)

    def test_high_value_merge_is_stable_and_deduplicates_normalized_urls(self):
        provider = "https://jobs.lever.co/acme"
        anchors = (
            f'<a href="{provider}?utm_source=nav">Careers</a>'
            + "".join(
                f'<a href="/docs/{index}">Doc {index}</a>'
                for index in range(MAX_EXTRACTED_LINKS)
            )
            + f'<a href="{provider}">View jobs</a>'
            + '<a href="/jobs">Open positions</a>'
        )

        links = extract_links(Page(url="https://example.com/careers", html=anchors))

        self.assertEqual(len(links), MAX_EXTRACTED_LINKS)
        self.assertEqual(
            [link.url for link in links[:2]],
            [provider, "https://example.com/jobs"],
        )
        self.assertEqual([link.url for link in links].count(provider), 1)

    def test_invalid_and_disguised_provider_urls_are_not_promoted_after_cap(self):
        anchors = "".join(
            f'<a href="/docs/{index}">Doc {index}</a>'
            for index in range(MAX_EXTRACTED_LINKS)
        )
        html = anchors + """
            <a href="https://jobs.lever.co.evil.example/acme">Careers</a>
            <a href="https://evil.example@jobs.lever.co/acme">View jobs</a>
            <a href="http://[invalid">Open positions</a>
            <a href="javascript:https://jobs.lever.co/acme">View jobs</a>
        """

        urls = [
            link.url
            for link in extract_links(Page(url="https://example.com/careers", html=html))
        ]

        self.assertNotIn("https://jobs.lever.co.evil.example/acme", urls)
        self.assertNotIn("https://evil.example@jobs.lever.co/acme", urls)
        self.assertNotIn("https://jobs.lever.co/acme", urls)
        self.assertTrue(all(url.startswith(("http://", "https://")) for url in urls))


if __name__ == "__main__":
    unittest.main()
