import importlib.util
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("make_snapshot", ROOT / "scripts" / "make_snapshot.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

BASE = "http://127.0.0.1:9999"


class UrlTests(unittest.TestCase):
    def test_local_url(self):
        self.assertEqual(ms.local_url("/"), "/")
        self.assertEqual(ms.local_url("/goals"), "/goals/")
        self.assertEqual(ms.local_url("/m", "id=cust_7&b=2"), "/m/b_2-id_cust_7/")
        self.assertEqual(ms.local_url("/m", "id=cust 7/../x"), "/m/id_cust_7_.._x/")  # one segment, no traversal

    def test_resolve_same_origin_only(self):
        self.assertEqual(ms.resolve(BASE, "/inbox", "/m?id=3#top"), ("/m", "id=3", "top"))
        self.assertEqual(ms.resolve(BASE, "/inbox", "goals"), ("/goals", "", ""))
        self.assertEqual(ms.resolve(BASE, "/", "/a?x=1&amp;y=2"), ("/a", "x=1&y=2", ""))
        self.assertIsNone(ms.resolve(BASE, "/", "https://example.org/x"))
        self.assertIsNone(ms.resolve(BASE, "/", "javascript:alert(1)"))

    def test_is_page(self):
        for p in ("/logout", "/oauth/start", "/healthz", "/export", "/sent/batch.csv", "/audit.log"):
            self.assertFalse(ms.is_page(p), p)
        for p in ("/", "/goals", "/m", "/inbox"):
            self.assertTrue(ms.is_page(p), p)


class RewriteTests(unittest.TestCase):
    PAGE = ("<html><head><title>t</title></head><body><nav>"
            '<a class="x" href="/goals">Goals</a> <a href="/m?id=3#c">msg</a> '
            '<a href="/export.csv">dl</a> <a href="https://example.org/">ext</a> <a href="#top">up</a> '
            '<a href="mailto:Jo@Gmail.com">mail</a></nav>'
            '<form action="/approve" method="post" enctype="multipart/form-data" class="card"><button>Go</button></form>'
            "<p>jo@icloud.com at /tmp/secret/app/state.json</p></body></html>")

    def out(self, **kw):
        known = {("/", ""), ("/goals", ""), ("/m", "id=3")}
        return ms.rewrite_page(self.PAGE, BASE, "/", known, "PR #1 @ abc1234", "2026-11-12",
                               kw.get("scrub", ["/tmp/secret/app"]))

    def test_links(self):
        o = self.out()
        self.assertIn('href="/goals/"', o)
        self.assertIn('href="/m/id_3/#c"', o)
        self.assertIn('href="https://example.org/"', o)   # external untouched
        self.assertIn('href="#top"', o)
        self.assertRegex(o, r'href="#" data-snap-dead="1"[^>]*>dl')  # unknown page -> inert

    def test_forms_are_switched_off(self):
        o = self.out()
        self.assertIn('<form class="card" action="#" method="get" data-snapshot="1">', o)
        self.assertNotIn("/approve", o)
        self.assertNotIn("multipart", o)

    def test_banner_noindex_and_script(self):
        o = self.out()
        self.assertIn("Read-only snapshot", o)
        self.assertIn('title="Snapshot of PR #1 @ abc1234, built 2026-11-12"', o)   # provenance: tooltip
        self.assertIn('name="robots" content="noindex,nofollow"', o)
        self.assertIn("snap-toast", o)
        self.assertLess(o.index("<body>"), o.index('<div class="snap-banner"'))

    def test_scrub_and_email_masking(self):
        o = self.out()
        self.assertIn("/srv/regulars/state.json", o)
        self.assertNotIn("/tmp/secret", o)
        self.assertIn("jo@icloud.example", o)
        self.assertIn("mailto:Jo@gmail.example", o)
        self.assertNotIn("icloud.com", o)

    def test_operator_notice_is_removed(self):
        page = ('<html><head></head><body><div class="warn"><b>Security.</b> No operator password is set yet. '
                '<a href="/login/">Sign-in &rarr;</a></div><h2>Your business</h2></body></html>')
        o = ms.rewrite_page(page, BASE, "/", {("/", "")}, "src", "2026-11-12", [])
        self.assertNotIn("operator password", o)
        self.assertNotIn("Sign-in", o)
        self.assertIn("Your business", o)

    def test_alias_redirects_links_to_the_moved_page(self):
        page = '<html><head></head><body><a href="/">Setup</a> <a href="/goals">Goals</a></body></html>'
        known = {("/", ""), ("/goals", "")}
        o = ms.rewrite_page(page, BASE, "/goals", known, "src", "d", [], aliases={("/", ""): "/setup/"})
        self.assertIn('<a href="/setup/">Setup</a>', o)
        self.assertIn('<a href="/goals/">Goals</a>', o)

    def test_custom_banner_and_contact_email(self):
        page = "<html><head></head><body><p>jo@icloud.com</p></body></html>"
        o = ms.rewrite_page(page, BASE, "/", {("/", "")}, "src", "d", [],
                            banner_text="Demo <b>only</b> & made up", contact="owner@gmail.com")
        self.assertIn("Demo &lt;b&gt;only&lt;/b&gt; &amp; made up", o)            # banner text is escaped
        self.assertIn('<a href="mailto:owner@gmail.com">owner@gmail.com</a>', o)   # contact NOT masked
        self.assertIn("jo@icloud.example", o)                                      # demo customer still masked
        self.assertNotIn("Want this on your own data?", ms.rewrite_page(page, BASE, "/", {("/", "")}, "s", "d", []))

    def test_contact_email_is_validated(self):
        for bad in ("nope", "a b@c.com", '"><script>@x.y'):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                ms.main(["--contact-email", bad, "--out", "/nonexistent-out"])
        self.assertTrue(ms.CONTACT_RE.match("owner@yourdomain.com.au"))

    def test_placeholder_email_domains_survive(self):
        for e in ("owner@yourvenue.com.au", "a@example.com", "b@x.invalid"):
            self.assertEqual(ms.mask_emails(e), e)


class _App(BaseHTTPRequestHandler):
    pages = {
        "/": '<html><head></head><body><a href="/a">a</a> <a href="/m?id=1">m</a> <a href="/logout">out</a>'
             '<form method="post" action="/approve"><button>x</button></form></body></html>',
        "/a": '<html><head></head><body><a href="/">home</a> amy@gmail.com</body></html>',
        "/m": '<html><head></head><body>message</body></html>',
    }
    leak = False

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in self.pages:
            body = self.pages[path] + ("/tmp/leaky/path" if self.leak and path == "/a" else "")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers(); self.wfile.write(body.encode())
        elif path == "/logout":
            self.send_response(303); self.send_header("Location", "/"); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


class EndToEndTests(unittest.TestCase):
    def serve(self):
        srv = HTTPServer(("127.0.0.1", 0), _App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}"

    def test_build_writes_static_site(self):
        base = self.serve()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "site"
            n, skipped, leftover = ms.build(base, out, "src", "2026-11-12", [], 50)
            self.assertEqual(n, 3)
            self.assertTrue((out / "index.html").exists())
            self.assertTrue((out / "a" / "index.html").exists())
            self.assertTrue((out / "m" / "id_1" / "index.html").exists())
            self.assertTrue((out / "robots.txt").read_text().startswith(ms.ROBOTS_MARKER))
            home = (out / "index.html").read_text()
            self.assertIn('href="/a/"', home)
            self.assertIn('data-snap-dead="1"', home)      # the logout link
            self.assertNotIn(base, home)
            self.assertIn("amy@gmail.example", (out / "a" / "index.html").read_text())
            # rebuilding over a previous snapshot is allowed
            ms.build(base, out, "src", "2026-11-13", [], 50)
            self.assertIn("2026-11-13", (out / "index.html").read_text())

    def test_landing_page_and_setup_alias(self):
        base = self.serve()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "site"
            ms.build(base, out, "src", "2026-11-12", [], 50, landing="/a/")
            root = (out / "index.html").read_text()
            self.assertIn('url=/a/', root)                       # the root just forwards to the landing page
            self.assertIn("noindex", root)
            self.assertNotIn("snap-banner", root)
            home = (out / "setup" / "index.html").read_text()    # the app's own home moved here
            self.assertIn('href="/a/"', home)
            self.assertIn("snap-banner", home)
            self.assertIn('href="/setup/"', (out / "a" / "index.html").read_text())   # a link to "/" follows it

    def test_landing_must_exist(self):
        base = self.serve()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                ms.build(base, Path(d) / "site", "src", "x", [], 50, landing="/nope/")
            self.assertFalse((Path(d) / "site").exists())

    def test_refuses_to_overwrite_a_folder_that_is_not_a_snapshot(self):
        base = self.serve()
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "precious.txt").write_text("keep me")
            with self.assertRaises(SystemExit):
                ms.build(base, Path(d), "src", "x", [], 50)
            self.assertEqual((Path(d) / "precious.txt").read_text(), "keep me")

    def test_leaked_local_path_aborts_before_writing(self):
        base = self.serve()
        _App.leak = True
        self.addCleanup(setattr, _App, "leak", False)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "site"
            with self.assertRaises(SystemExit):
                ms.build(base, out, "src", "x", [], 50)
            self.assertFalse(out.exists())

    def test_unreachable_app(self):
        with self.assertRaises(Exception):
            ms.build("http://127.0.0.1:1", Path(tempfile.mkdtemp()) / "s", "src", "x", [], 5)


if __name__ == "__main__":
    unittest.main()
