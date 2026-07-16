import unittest

from job_source_agent.card_listing_extraction import (
    MAX_CANDIDATES,
    MAX_NODES,
    MAX_TEXT_CHARS,
    extract_card_listing_candidates,
)
from job_source_agent.listing_extraction import extract_listing_candidates


SOURCE_URL = "https://careers.example.com/jobs"


class CardListingExtractionTests(unittest.TestCase):
    def test_extracts_calpion_style_detail_card(self):
        html = """
            <div class="job-card">
              <h3>Senior Cloud Engineer</h3>
              <a href="/jobs/482/senior-cloud-engineer">View Details</a>
            </div>
        """

        candidates = extract_card_listing_candidates(html, SOURCE_URL)

        self.assertEqual(
            [(item.title, item.url, item.origin) for item in candidates],
            [("Senior Cloud Engineer", "https://careers.example.com/jobs/482/senior-cloud-engineer", "parent_card")],
        )

    def test_extracts_softleo_style_apply_id_card(self):
        html = """
            <article class="opening-item">
              <h4>Business Systems Analyst</h4>
              <a href="apply?id=SL-2048">Apply Now</a>
            </article>
        """

        candidates = extract_card_listing_candidates(html, SOURCE_URL)

        self.assertEqual(candidates[0].title, "Business Systems Analyst")
        self.assertEqual(candidates[0].url, "https://careers.example.com/apply?id=SL-2048")

    def test_extracts_bootstrap_card_with_card_title(self):
        html = """
            <div class="col-md-6">
              <div class="card h-100 bg-light-beige">
                <div class="card-body">
                  <h5 class="card-title fs-4 fw-bold">AI/ML Engineer</h5>
                  <div class="text-end"><a href="apply?id=123e4567-e89b-12d3-a456-426614174001">Apply</a></div>
                </div>
              </div>
            </div>
        """

        candidates = extract_card_listing_candidates(html, SOURCE_URL)

        self.assertEqual(
            [(item.title, item.url) for item in candidates],
            [("AI/ML Engineer", "https://careers.example.com/apply?id=123e4567-e89b-12d3-a456-426614174001")],
        )

    def test_extracts_career_blog_title_from_career_card(self):
        html = """
            <div class="career-card-featured pulse-button-card">
              <div class="career-title-head"><div class="career-blog-title">AI/ML Developer</div></div>
              <a href="/career/ai-ml-developer" class="featured-link">Know More</a>
              <a href="/career/ai-ml-developer" class="button">Apply Now</a>
            </div>
        """

        candidates = extract_card_listing_candidates(html, SOURCE_URL)

        self.assertEqual(
            [(item.title, item.url) for item in candidates],
            [("AI/ML Developer", "https://careers.example.com/career/ai-ml-developer")],
        )

    def test_extracts_heading_from_guava_style_anchor_card(self):
        html = """
            <a class="careers-card group" href="/careers/senior-cloud-engineer">
              <h2>Senior Cloud Engineer</h2>
              <p>
                Build reliable cloud systems across a large distributed platform.
                You will collaborate closely with the Product Manager and security team
                while owning delivery, observability, and operational improvements.
              </p>
            </a>
            <a href="https://jobs.ashbyhq.com/acme/abc123">
              <div class="job-title">Data Platform Engineer</div>
              <p>Lead the design and operation of our analytics platform.</p>
            </a>
            <a class="careers-card" href="/careers/mid-market-account-executive">
              <h4>Mid-Market Account Executive</h4>
              <p>Own the full sales cycle for mid-market customers.</p>
            </a>
        """

        candidates = extract_card_listing_candidates(html, SOURCE_URL)

        self.assertEqual(
            [(item.title, item.url) for item in candidates],
            [
                ("Senior Cloud Engineer", "https://careers.example.com/careers/senior-cloud-engineer"),
                ("Data Platform Engineer", "https://jobs.ashbyhq.com/acme/abc123"),
                (
                    "Mid-Market Account Executive",
                    "https://careers.example.com/careers/mid-market-account-executive",
                ),
            ],
        )

    def test_anchor_card_rejects_ambiguous_or_description_only_titles(self):
        cases = (
            """
                <a href="/jobs/1/data-engineer">
                  <h2>Data Engineer</h2><h3>Platform Engineer</h3>
                  <p>Choose the role that fits your experience.</p>
                </a>
            """,
            """
                <a href="/jobs/2/data-engineer">
                  <p>Our Data Engineer will build dependable pipelines for customers.</p>
                </a>
            """,
            """
                <a href="/jobs/3/engineering-roles">
                  <h2>Engineering roles</h2><p>Explore the team.</p>
                </a>
            """,
        )

        for html in cases:
            with self.subTest(html=html):
                self.assertEqual(extract_card_listing_candidates(html, SOURCE_URL), [])

    def test_anchor_card_keeps_navigation_hidden_and_url_safety_boundaries(self):
        cases = (
            '<nav><a href="/jobs/1/data-engineer"><h2>Data Engineer</h2></a></nav>',
            '<footer><a href="/jobs/1/data-engineer"><h2>Data Engineer</h2></a></footer>',
            '<a href="/jobs/1/data-engineer" hidden><h2>Data Engineer</h2></a>',
            '<template><a href="/jobs/1/data-engineer"><h2>Data Engineer</h2></a></template>',
            '<script><a href="/jobs/1/data-engineer"><h2>Data Engineer</h2></a></script>',
            '<a href="https://evil.example/jobs/1/data-engineer"><h2>Data Engineer</h2></a>',
            '<a href="https://user:secret@jobs.ashbyhq.com/acme/abc123"><h2>Data Engineer</h2></a>',
            '<a href="https://jobs.ashbyhq.com:443/acme/abc123"><h2>Data Engineer</h2></a>',
        )

        for html in cases:
            with self.subTest(html=html):
                self.assertEqual(extract_card_listing_candidates(html, SOURCE_URL), [])

    def test_rejects_cross_card_pairing(self):
        html = """
            <div class="job-card"><h3>Data Engineer</h3></div>
            <div class="job-card"><a href="/jobs/123/data-engineer">Apply</a></div>
        """

        self.assertEqual(extract_card_listing_candidates(html, SOURCE_URL), [])

    def test_rejects_nested_and_broad_navigation_containers(self):
        html = """
            <div role="navigation">
              <article><h3>Security Engineer</h3><a href="/jobs/4/security-engineer">Jobs</a></article>
            </div>
            <section class="job-listing">
              <h2>Engineering roles</h2>
              <a href="/jobs/5/platform-engineer">Platform role</a>
              <div class="job-card">
                <h3>Platform Engineer</h3>
                <a href="/jobs/5/platform-engineer">Details</a>
              </div>
            </section>
        """

        candidates = extract_card_listing_candidates(html, SOURCE_URL)

        self.assertEqual(
            [(item.title, item.url) for item in candidates],
            [("Platform Engineer", "https://careers.example.com/jobs/5/platform-engineer")],
        )

    def test_rejects_external_non_ats_credentials_and_ports(self):
        hrefs = (
            "https://evil.example/jobs/1/data-engineer",
            "https://user:secret@jobs.ashbyhq.com/acme/abc123",
            "https://jobs.ashbyhq.com:443/acme/abc123",
        )

        for href in hrefs:
            with self.subTest(href=href):
                html = f'<div class="job-card"><h3>Data Engineer</h3><a href="{href}">Apply</a></div>'
                self.assertEqual(extract_card_listing_candidates(html, SOURCE_URL), [])

    def test_accepts_recognized_ats_detail(self):
        html = """
            <li class="position-item">
              <h3>Data Platform Engineer</h3>
              <a href="https://jobs.ashbyhq.com/acme/abc123">Apply</a>
            </li>
        """

        candidates = extract_card_listing_candidates(html, SOURCE_URL)

        self.assertEqual(candidates[0].url, "https://jobs.ashbyhq.com/acme/abc123")

    def test_rejects_ambiguous_or_unrelated_title(self):
        cases = (
            """
                <div class="job-card">
                  <h3>Data Engineer</h3><h4>Platform Engineer</h4>
                  <a href="/jobs/1/data-engineer">Apply</a>
                </div>
            """,
            """
                <div class="job-card">
                  <h3>Software Engineer</h3>
                  <a href="/jobs/2/product-manager">Apply</a>
                </div>
            """,
            """
                <div class="job-card">
                  <h3>Engineering roles</h3>
                  <a href="/jobs/3/engineering-roles">View jobs</a>
                </div>
            """,
        )

        for html in cases:
            with self.subTest(html=html):
                self.assertEqual(extract_card_listing_candidates(html, SOURCE_URL), [])

    def test_rejects_hidden_template_and_script_content(self):
        html = """
            <div class="job-card" hidden><h3>Hidden Engineer</h3><a href="/jobs/1/hidden-engineer">Apply</a></div>
            <template>
              <article><h3>Template Engineer</h3><a href="/jobs/2/template-engineer">Apply</a></article>
            </template>
            <script><article><h3>Script Engineer</h3><a href="/jobs/3/script-engineer">Apply</a></article></script>
            <article aria-hidden="true"><h3>Ghost Engineer</h3><a href="/jobs/4/ghost-engineer">Apply</a></article>
            <article style="display: none"><h3>CSS Engineer</h3><a href="/jobs/5/css-engineer">Apply</a></article>
        """

        self.assertEqual(extract_card_listing_candidates(html, SOURCE_URL), [])
        self.assertEqual(extract_listing_candidates(html, SOURCE_URL), [])

    def test_public_extractor_keeps_bounded_paragraph_title_fallback(self):
        html = """
            <ul class="job-listing">
              <li>
                <p>New York Office</p>
                <p>Software Engineer, Full Stack</p>
                <a href="/jobs/software-engineer-full-stack">See role</a>
              </li>
            </ul>
        """

        candidates = extract_listing_candidates(html, SOURCE_URL)

        self.assertEqual(
            [(item.title, item.url) for item in candidates],
            [("Software Engineer, Full Stack", "https://careers.example.com/jobs/software-engineer-full-stack")],
        )

    def test_enforces_node_and_candidate_bounds(self):
        candidates_html = "".join(
            f'<article><h3>Engineer {index}</h3><a href="/jobs/{index}/engineer-{index}">Details</a></article>'
            for index in range(MAX_CANDIDATES + 5)
        )
        after_node_limit = "<div></div>" * (MAX_NODES + 1)
        after_node_limit += '<article><h3>Late Engineer</h3><a href="/jobs/9/late-engineer">Details</a></article>'
        after_text_limit = "x" * (MAX_TEXT_CHARS + 1)
        after_text_limit += '<article><h3>Late Engineer</h3><a href="/jobs/9/late-engineer">Details</a></article>'

        self.assertEqual(len(extract_card_listing_candidates(candidates_html, SOURCE_URL)), MAX_CANDIDATES)
        self.assertEqual(extract_card_listing_candidates(after_node_limit, SOURCE_URL), [])
        self.assertEqual(extract_card_listing_candidates(after_text_limit, SOURCE_URL), [])


if __name__ == "__main__":
    unittest.main()
