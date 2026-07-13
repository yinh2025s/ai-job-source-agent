import unittest

from job_source_agent.web import MAX_EXTRACTED_LINKS, Page, extract_links


class LinkExtractionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
