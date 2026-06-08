import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from archive_search.freshdesk import FreshdeskClient


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, payload, headers=None):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = FakeHeaders(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self._body


class FakeBinaryResponse:
    def __init__(self, payload, headers=None):
        self._body = BytesIO(payload)
        self.headers = FakeHeaders(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size=-1):
        return self._body.read(size)


class FreshdeskClientTest(unittest.TestCase):
    def test_iter_tickets_stops_after_short_page(self):
        urls = []

        def fake_urlopen(request, timeout):
            urls.append(request.full_url)
            return FakeResponse([{"id": 1, "updated_at": "2024-01-01T00:00:00Z"}])

        client = FreshdeskClient("example.freshdesk.com", "key", urlopen_impl=fake_urlopen)
        tickets = list(client.iter_tickets("2024-01-01T00:00:00Z", per_page=100))

        self.assertEqual([{"id": 1, "updated_at": "2024-01-01T00:00:00Z"}], tickets)
        self.assertEqual(len(urls), 1)
        self.assertIn("include=description%2Crequester%2Cstats", urls[0])

    def test_iter_conversations_paginates_until_short_page(self):
        pages = [
            [{"id": index, "ticket_id": 42} for index in range(30)],
            [{"id": 31, "ticket_id": 42}],
        ]

        def fake_urlopen(request, timeout):
            return FakeResponse(pages.pop(0))

        client = FreshdeskClient("example.freshdesk.com", "key", urlopen_impl=fake_urlopen)
        conversations = list(client.iter_conversations(42))

        self.assertEqual(len(conversations), 31)

    def test_retries_rate_limit_once(self):
        calls = {"count": 0}

        def fake_urlopen(request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    FakeHeaders({"Retry-After": "0"}),
                    BytesIO(b"rate limited"),
                )
            return FakeResponse([])

        client = FreshdeskClient("example.freshdesk.com", "key", urlopen_impl=fake_urlopen)
        self.assertEqual(client.list_ticket_fields(), [])
        self.assertEqual(calls["count"], 2)

    def test_download_to_path_writes_binary_and_hash(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            return FakeBinaryResponse(b"hello", {"Content-Type": "text/plain"})

        client = FreshdeskClient("example.freshdesk.com", "key", urlopen_impl=fake_urlopen)
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "file.txt"
            result = client.download_to_path("https://example.freshdesk.com/file.txt", target)

            self.assertEqual(target.read_bytes(), b"hello")

        self.assertEqual(result.bytes_written, 5)
        self.assertEqual(result.content_type, "text/plain")
        self.assertNotIn("Authorization", requests[0].headers)

        with tempfile.TemporaryDirectory() as tmpdir:
            client.download_to_path(
                "https://example.freshdesk.com/file.txt",
                Path(tmpdir) / "authed.txt",
                authenticate=True,
            )

        self.assertIn("Authorization", requests[-1].headers)


if __name__ == "__main__":
    unittest.main()
