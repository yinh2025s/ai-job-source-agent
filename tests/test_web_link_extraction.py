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


if __name__ == "__main__":
    unittest.main()
