"""Run with:  python -m unittest discover -s tests   (uses a throwaway database)."""
import os
import re
import sys
import tempfile
import unittest

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + _tmp.replace("\\", "/") + "/test.db"
os.environ["APP_PASSWORD"] = "testpass123"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A  # noqa: E402
import media  # noqa: E402
import scraper  # noqa: E402
import videos  # noqa: E402
import youtube  # noqa: E402


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = A.app.test_client()
        tok = re.search(r'name="csrf" value="([^"]+)"', cls.c.get("/login").text).group(1)
        r = cls.c.post("/login", data={"csrf": tok, "username": "wodo", "password": "testpass123"})
        assert r.status_code == 302

    def csrf(self):
        self.c.get("/add")  # a page render creates the session token
        with self.c.session_transaction() as s:
            return s["csrf"]

    def test_anonymous_redirected(self):
        self.assertEqual(A.app.test_client().get("/").status_code, 302)
        self.assertEqual(A.app.test_client().get("/healthz").status_code, 200)

    def test_pages_load_with_csp(self):
        for p in ("/", "/add", "/manage", "/runs", "/status"):
            r = self.c.get(p)
            self.assertEqual(r.status_code, 200, p)
        r = self.c.get("/")
        nonce = re.search(r"nonce-([\w-]+)", r.headers["Content-Security-Policy"]).group(1)
        self.assertIn(f'nonce="{nonce}"', r.text)

    def test_csrf_required(self):
        self.assertEqual(self.c.post("/start").status_code, 400)

    def test_add_and_modify(self):
        r = self.c.post("/sources/add", data={"csrf": self.csrf(), "urls": "bbc.com/news"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.post("/foo/1/delete", data={"csrf": self.csrf()}).status_code, 404)

    def test_media_traversal_blocked(self):
        self.assertEqual(self.c.get("/media/..%2Fapp.py").status_code, 404)

    def test_login_next_is_local_only(self):
        with A.app.test_request_context():
            self.assertEqual(A.safe_next("//evil.com"), "/")
            self.assertEqual(A.safe_next("/add"), "/add")


class HelperTests(unittest.TestCase):
    def test_youtube_ids(self):
        self.assertEqual(youtube.parse_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(youtube.parse_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertIsNone(youtube.parse_id("https://evil.com/watch?v=dQw4w9WgXcQ"))

    def test_private_hosts_refused(self):
        for u in ("http://127.0.0.1/x", "http://localhost/x", "http://192.168.1.1/x", "ftp://example.com/x"):
            self.assertFalse(media.is_public(u), u)

    def test_fetch_refuses_private(self):
        self.assertIsNone(scraper.fetch("http://127.0.0.1:1/"))

    def test_section_page_rejected(self):
        html = "<html><body><article>" + "<p>" + "word " * 80 + "</p>" * 1 + "</article></body></html>"
        self.assertIsNone(scraper.extract(html, "https://example.com/news"))

    def test_group_lines(self):
        rows = [{"t": 0, "text": "a"}, {"t": 10, "text": "b"}, {"t": 30, "text": "c"}]
        self.assertEqual([g["text"] for g in videos.group_lines(rows)], ["a b", "c"])

    def test_parse_urls(self):
        self.assertEqual(A.parse_urls("a.com, https://b.com\na.com"), ["https://a.com", "https://b.com"])


if __name__ == "__main__":
    unittest.main()
