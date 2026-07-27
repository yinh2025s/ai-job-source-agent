import unittest
from unittest.mock import MagicMock, patch

from job_source_agent.web import Fetcher, _is_loopback_http_url


class _Headers(dict):
    def get_content_charset(self):
        return "utf-8"


class _Response:
    headers = _Headers()

    def __init__(self, url):
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b"ok"

    def geturl(self):
        return self.url


class FetcherSessionTests(unittest.TestCase):
    @patch("job_source_agent.web.build_opener")
    def test_reuses_cookie_aware_opener_within_worker_thread(self, build_opener):
        opener = MagicMock()
        opener.open.side_effect = lambda request, timeout: _Response(request.full_url)
        build_opener.return_value = opener
        fetcher = Fetcher(timeout=1)

        fetcher.fetch("https://example.com/first")
        fetcher.fetch("https://example.com/second")

        build_opener.assert_called_once()
        self.assertEqual(opener.open.call_count, 2)

    @patch("job_source_agent.web.build_opener")
    def test_authorization_is_not_copied_into_redirect_headers(self, build_opener):
        opener = MagicMock()
        opener.open.side_effect = lambda request, timeout: _Response(request.full_url)
        build_opener.return_value = opener

        Fetcher(timeout=1).fetch(
            "https://api.example.com/jobs",
            headers={"Authorization": "Bearer public-runtime-key"},
        )

        request = opener.open.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(
            request.unredirected_hdrs["Authorization"],
            "Bearer public-runtime-key",
        )

    @patch("job_source_agent.web.build_opener")
    def test_loopback_fetches_reuse_a_proxy_free_opener(self, build_opener):
        opener = MagicMock()
        opener.open.side_effect = lambda request, timeout: _Response(request.full_url)
        build_opener.return_value = opener
        fetcher = Fetcher(timeout=1)

        fetcher.fetch("http://127.0.0.1:8888/search?q=first")
        fetcher.fetch("http://127.0.0.1:8888/search?q=second")

        build_opener.assert_called_once()
        handlers = build_opener.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertEqual(opener.open.call_count, 2)

    @patch("job_source_agent.web.build_opener")
    def test_public_and_loopback_fetches_use_separate_cookie_sessions(self, build_opener):
        public_opener = MagicMock()
        public_opener.open.side_effect = (
            lambda request, timeout: _Response(request.full_url)
        )
        loopback_opener = MagicMock()
        loopback_opener.open.side_effect = (
            lambda request, timeout: _Response(request.full_url)
        )
        build_opener.side_effect = [public_opener, loopback_opener]
        fetcher = Fetcher(timeout=1)

        fetcher.fetch("https://example.com/jobs")
        fetcher.fetch("http://localhost:8888/search?q=jobs")

        self.assertEqual(build_opener.call_count, 2)
        self.assertFalse(
            hasattr(build_opener.call_args_list[0].args[0], "proxies")
        )
        self.assertEqual(build_opener.call_args_list[1].args[0].proxies, {})
        self.assertEqual(public_opener.open.call_count, 1)
        self.assertEqual(loopback_opener.open.call_count, 1)

    def test_loopback_url_detection_is_literal_and_bounded(self):
        self.assertTrue(_is_loopback_http_url("http://localhost:8888/search"))
        self.assertTrue(_is_loopback_http_url("http://127.0.0.1:8888/search"))
        self.assertTrue(_is_loopback_http_url("https://[::1]/search"))
        self.assertFalse(_is_loopback_http_url("https://localhost.example/search"))
        self.assertFalse(_is_loopback_http_url("http://192.168.1.20/search"))
        self.assertFalse(_is_loopback_http_url("ftp://127.0.0.1/search"))
        self.assertFalse(_is_loopback_http_url("http://[invalid/search"))


if __name__ == "__main__":
    unittest.main()
