import unittest
from unittest.mock import MagicMock, patch

from job_source_agent.web import Fetcher


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


if __name__ == "__main__":
    unittest.main()
