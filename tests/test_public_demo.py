"""
Tests for the public demo (REGULARS_PUBLIC_DEMO=1): a hosted copy anyone can try,
where every visitor works in a private in-memory sandbox and nothing can be sent.

Run from the repo root:  python3 -m unittest discover -s tests -t .
"""

import io
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib import parse, request
from urllib.error import HTTPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
_AUDIT_TMP = tempfile.mkdtemp(prefix="regulars-tests-")
os.environ.setdefault("REGULARS_AUDIT_LOG", os.path.join(_AUDIT_TMP, "audit.log"))

import demo as DEMO        # noqa: E402
import security as SEC     # noqa: E402
import server as S         # noqa: E402
import state as ST         # noqa: E402
import store               # noqa: E402


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Browser:
    """One visitor's browser: remembers the sandbox cookie between requests."""

    def __init__(self, base):
        self.base = base
        self.cookie = None
        self.headers = None

    def _do(self, req):
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with request.build_opener(_NoRedirect).open(req, timeout=60) as r:
                status, body, headers = r.status, r.read().decode(), r.headers
        except HTTPError as err:
            status, body, headers = err.code, err.read().decode(), err.headers
        sc = headers.get("Set-Cookie") or ""
        if sc.startswith(DEMO.COOKIE + "="):
            self.cookie = sc.split(";", 1)[0]
        self.headers = headers
        return status, body

    def get(self, path):
        return self._do(request.Request(self.base + path))

    def post(self, path, **form):
        return self._do(request.Request(
            self.base + path, data=parse.urlencode(form, doseq=True).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"}))

    def upload(self, path, content, name="export.csv", field="f_main", **fields):
        boundary = "----publicdemoboundary"
        raw = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{val}\r\n".encode()
                       for k, val in fields.items())
        raw += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                f"filename=\"{name}\"\r\nContent-Type: text/csv\r\n\r\n").encode()
        raw += (content.encode() if isinstance(content, str) else content)
        raw += f"\r\n--{boundary}--\r\n".encode()
        return self._do(request.Request(
            self.base + path, data=raw,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}))


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def count(pattern, text):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


class _Harness(unittest.TestCase):
    PUBLIC = True

    @classmethod
    def setUpClass(cls):
        cls._env = mock.patch.dict(os.environ, {"REGULARS_PUBLIC_DEMO": "1"} if cls.PUBLIC else {})
        cls._env.start()
        if not cls.PUBLIC:
            os.environ.pop("REGULARS_PUBLIC_DEMO", None)
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_path = store.PATH
        store.PATH = os.path.join(cls._tmp.name, "state.json")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        store.PATH = cls._old_path
        cls._tmp.cleanup()
        cls._env.stop()

    def setUp(self):
        V = ST.VISITORS
        self._limits = (V.ttl, V.max_sessions, V.max_uploads, V._clock)
        V.clear()
        self._guard = mock.patch.object(S, "GUARD", SEC.Guard())
        self._guard.start()
        ST.STATE.update(ST.fresh_state())

    def tearDown(self):
        V = ST.VISITORS
        V.ttl, V.max_sessions, V.max_uploads, V._clock = self._limits
        V.clear()
        self._guard.stop()
        ST.STATE.update(ST.fresh_state())

    def visitor(self):
        return Browser(self.base)


class TestSandboxes(_Harness):
    def test_first_visit_sets_a_private_cookie(self):
        a = self.visitor()
        status, _ = a.get("/inbox")
        self.assertEqual(status, 200)
        sc = a.headers["Set-Cookie"]
        self.assertTrue(sc.startswith(DEMO.COOKIE + "="))
        for flag in ("HttpOnly", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, sc)
        self.assertNotIn("Secure", sc)                    # no TLS in this test
        token = sc.split("=", 1)[1].split(";", 1)[0]
        self.assertGreaterEqual(len(token), 24)
        a.get("/inbox")                                   # same sandbox: no new cookie
        self.assertIsNone(a.headers.get("Set-Cookie"))
        with mock.patch.dict(os.environ, {"REGULARS_TLS": "1"}):
            b = self.visitor()
            b.get("/inbox")
            self.assertIn("Secure", b.headers["Set-Cookie"])         # behind HTTPS the cookie is Secure

    def test_visitors_cannot_see_each_others_work(self):
        a, b = self.visitor(), self.visitor()
        a.get("/inbox")
        b.get("/inbox")
        self.assertEqual(a.post("/approve-all")[0], 303)
        self.assertEqual(count(r"<b>(\d+) approved</b>", a.get("/inbox")[1]), 199)
        self.assertEqual(count(r"<b>(\d+) approved</b>", b.get("/inbox")[1]), 0)

        csv = a.get("/sample/simple.csv")[1]
        self.assertEqual(a.upload("/upload", csv)[0], 303)
        mine = count(r"(\d+) messages written", a.get("/inbox")[1])
        theirs = count(r"(\d+) messages written", b.get("/inbox")[1])
        self.assertEqual(theirs, 199)                     # still the example restaurant
        self.assertNotEqual(mine, 199)                    # a's own file, its own messages
        self.assertIn("Import report", a.get("/")[1])
        self.assertNotIn("Import report", b.get("/")[1])

    def test_one_visitors_notes_do_not_reach_another(self):
        a, b = self.visitor(), self.visitor()
        a.get("/"), b.get("/")
        a.post("/region", region="US")                    # US without an address: flashes a refusal
        self.assertNotIn("US market requires", b.get("/")[1])      # b must not see (or eat) it
        self.assertIn("US market requires", a.get("/")[1])

    def test_the_upload_card_explains_consent_and_the_report_counts_visits(self):
        a = self.visitor()
        _, body = a.get("/")
        self.assertIn("Tick the consent box to see messages", body)
        a.upload("/upload", a.get("/sample/simple.csv")[1])
        _, after = a.get("/")
        self.assertRegex(after, r"[1-9]\d* visits read into")     # not the old "0 visits read"
        self.assertIn("No marketing consent found", after)         # and it says why there are 0 messages

    def test_a_visitor_can_approve_their_own_upload_without_inventing_business_details(self):
        a = self.visitor()
        a.get("/")
        a.upload("/upload", a.get("/sample/simple.csv")[1], consent="1")
        _, inbox = a.get("/inbox")
        self.assertNotIn("approving and exporting are switched off", inbox)
        self.assertNotIn("approving and exporting stay off", a.get("/")[1])
        self.assertIn("see your own name in every message", inbox)        # still nudges, doesn't block
        self.assertEqual(a.post("/approve-all")[0], 303)
        _, inbox = a.get("/inbox")
        self.assertGreater(count(r"<b>(\d+) approved</b>", inbox), 0)
        self.assertRegex(inbox, r"<button[^>]*disabled[^>]*>Export these \d+</button>")

    def test_start_over_clears_an_upload(self):
        a = self.visitor()
        a.get("/")
        a.upload("/upload", a.get("/sample/simple.csv")[1])
        self.assertNotEqual(count(r"(\d+) messages written", a.get("/inbox")[1]), 199)
        a.post("/reset")
        self.assertEqual(count(r"(\d+) messages written", a.get("/inbox")[1]), 199)

    def test_idle_sandboxes_are_deleted(self):
        clock = _Clock()
        ST.VISITORS._clock = clock
        ST.VISITORS.ttl = 30 * 60
        a = self.visitor()
        a.get("/inbox")
        a.post("/approve-all")
        old_cookie = a.cookie
        clock.t += 29 * 60
        self.assertEqual(count(r"<b>(\d+) approved</b>", a.get("/inbox")[1]), 199)   # activity keeps it
        clock.t += 31 * 60
        _, body = a.get("/inbox")
        self.assertEqual(count(r"<b>(\d+) approved</b>", body), 0)
        self.assertNotEqual(a.cookie, old_cookie)
        self.assertIn("timed out and was cleared", a.get("/")[1] + body)
        self.assertEqual(len(ST.VISITORS), 1)

    def test_a_forged_cookie_just_gets_a_new_sandbox(self):
        a = self.visitor()
        a.cookie = f"{DEMO.COOKIE}=not-a-token"
        status, body = a.get("/inbox")
        self.assertEqual(status, 200)
        self.assertNotIn("timed out", body)
        self.assertNotEqual(a.cookie, f"{DEMO.COOKIE}=not-a-token")


class TestNothingCanLeave(_Harness):
    REFUSED_GET = ["/export.csv", "/export-copy.txt", "/security", "/security/audit.log", "/login",
                   "/logout", "/welcome", "/oauth/start", "/oauth/callback", "/nope", "/.env"]
    REFUSED_POST = ["/export", "/security/verify", "/security/logout-all", "/login", "/logout",
                    "/sending/test", "/oauth/revoke", "/welcome", "/nope"]

    def test_owner_only_routes_are_refused_without_creating_a_sandbox(self):
        a = self.visitor()
        for path in self.REFUSED_GET:
            with self.subTest(get=path):
                status, body = a.get(path)
                self.assertEqual(status, 403)
                self.assertNotIn("Traceback", body)
        for path in self.REFUSED_POST:
            with self.subTest(post=path):
                self.assertEqual(a.post(path, password="x")[0], 403)
        self.assertEqual(len(ST.VISITORS), 0)
        self.assertIn("switched off in the demo", a.get("/export.csv")[1])

    def test_the_export_button_is_disabled(self):
        a = self.visitor()
        a.post("/approve-all")
        _, body = a.get("/inbox")
        self.assertIn("Exports are off in the demo", body)
        self.assertRegex(body, r"<button[^>]*disabled[^>]*>Export these 199</button>")
        self.assertNotIn('action="/export"', body)

    def test_public_demo_notice_and_no_security_tab(self):
        a = self.visitor()
        for path in ("/", "/goals", "/inbox", "/sending", "/sent", "/data"):
            with self.subTest(page=path):
                _, body = a.get(path)
                self.assertIn("Public demo.", body)
                self.assertIn("Nothing here can be sent", body)
                self.assertNotIn('href="/security"', body)
                self.assertNotIn("No operator password", body)

    def test_sending_screen_stores_and_tests_nothing(self):
        a = self.visitor()
        _, body = a.get("/sending")
        for gone in ("Test the connection", "smtp_pass", "smtp_host", "/oauth/start", "SQUARE_CLIENT_ID"):
            self.assertNotIn(gone, body)
        self.assertIn("switched off in the demo", body)
        self.assertEqual(a.post("/sending", tool="smtp", smtp_host="evil.example",
                                smtp_pass="hunter2", from_addr="x@y.z")[0], 303)
        self.assertIn("checked", a.get("/sending")[1])    # the tool choice is kept
        self.assertEqual(ST.VISITORS._by_token[a.cookie.split("=", 1)[1]].state["sender"]["smtp_host"], "")
        self.assertEqual(ST.VISITORS._by_token[a.cookie.split("=", 1)[1]].state["sender"]["smtp_pass"], "")

    def test_nothing_is_written_to_disk_or_audited(self):
        a = self.visitor()
        with mock.patch.object(ST.AUDIT._inner, "write") as w:
            a.get("/")
            a.post("/approve-all")
            a.upload("/upload", a.get("/sample/simple.csv")[1])
            a.post("/goals", enabled="x")
            a.post("/business", name="Jane's Cafe", suburb="Fitzroy", abn="11111111111")
            a.post("/reset")
        self.assertEqual([c.args[0] for c in w.call_args_list], [])       # not even a config change
        self.assertFalse(os.path.exists(store.PATH))

    def test_security_events_are_still_audited(self):
        a = self.visitor()
        with mock.patch.dict(os.environ, {"REGULARS_MAX_UPLOAD_MB": "1"}), \
                mock.patch.object(ST.AUDIT._inner, "write") as w:
            a.get("/")
            self.assertEqual(a.upload("/upload", "x" * (2 * 1024 * 1024))[0], 413)
        self.assertEqual([c.args[0] for c in w.call_args_list], ["oversize_upload"])

    def test_errors_never_show_or_log_what_a_visitor_uploaded(self):
        def boom(req):
            raise RuntimeError("row 7: jane.citizen@example.com bought 3 flat whites")

        a = self.visitor()
        a.get("/")
        err = io.StringIO()
        with mock.patch("sys.stderr", err), mock.patch.dict(S.GET_ROUTES, {"/data": boom}), \
                mock.patch.dict(S.POST_ROUTES, {"/reset": boom}):
            gs, gbody = a.get("/data")
            ps, pbody = a.post("/reset")
        self.assertEqual((gs, ps), (500, 500))
        for body in (gbody, pbody):
            for leak in ("Traceback", "jane.citizen", ".py", "RuntimeError", "/home/", "/tmp/"):
                self.assertNotIn(leak, body)
        self.assertNotIn("jane.citizen", err.getvalue())                  # not in the server log either
        self.assertIn("RuntimeError", err.getvalue())                     # but the failure is


class TestLogsHoldNoFileDerivedIds(_Harness):
    def test_query_strings_stay_out_of_the_access_log(self):
        a = self.visitor()
        a.get("/")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            a.get("/m?id=cust_00042_drifting")
            a.get("/inbox?cat=money_off")
        log = err.getvalue()
        self.assertIn("GET /m?... HTTP", log)
        self.assertIn("GET /inbox?... HTTP", log)
        self.assertNotIn("cust_00042", log)
        self.assertNotIn("money_off", log)

    def test_a_normal_instance_still_logs_the_full_path(self):
        with mock.patch.dict(os.environ, {"REGULARS_PUBLIC_DEMO": ""}):
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                S.Handler.log_message(mock.Mock(), '"GET /m?id=cust_7 HTTP/1.1" 200 -')
        self.assertIn("cust_7", err.getvalue())


class TestLimits(_Harness):
    def test_default_upload_cap_is_small(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("REGULARS_MAX_UPLOAD_MB", None)
            self.assertEqual(SEC.max_upload_bytes(), 5 * 1024 * 1024)
        with mock.patch.dict(os.environ, {"REGULARS_PUBLIC_DEMO": "", "REGULARS_MAX_UPLOAD_MB": ""}):
            self.assertEqual(SEC.max_upload_bytes(), 50 * 1024 * 1024)    # a normal instance is unchanged

    def test_the_demo_says_when_it_is_full(self):
        ST.VISITORS.max_sessions = 2
        a, b, c = self.visitor(), self.visitor(), self.visitor()
        self.assertEqual(a.get("/inbox")[0], 200)
        self.assertEqual(b.get("/inbox")[0], 200)
        status, body = c.get("/inbox")
        self.assertEqual(status, 503)
        self.assertIn("busy", body)
        self.assertEqual(c.headers["Retry-After"], "60")
        self.assertEqual(a.get("/inbox")[0], 200)                         # people already in are fine
        self.assertEqual(c.post("/approve-all")[0], 503)                  # a POST is refused too

    def test_one_address_cannot_flood_the_store(self):
        with mock.patch.object(DEMO, "NEW_SESSIONS_PER_MINUTE", 2):
            codes = [self.visitor().get("/inbox")[0] for _ in range(4)]
        self.assertEqual(codes, [200, 200, 429, 429])
        self.assertEqual(len(ST.VISITORS), 2)

    def test_health_checks_and_samples_do_not_use_up_sandboxes(self):
        a = self.visitor()
        for _ in range(60):
            self.assertEqual(a.get("/healthz")[0], 200)
        self.assertEqual(a.get("/sample/simple.csv")[0], 200)
        self.assertEqual(len(ST.VISITORS), 0)

    def test_only_a_few_sandboxes_may_hold_an_upload(self):
        ST.VISITORS.max_uploads = 1
        a, b = self.visitor(), self.visitor()
        a.get("/"), b.get("/")
        csv = a.get("/sample/simple.csv")[1]
        self.assertEqual(a.upload("/upload", csv)[0], 303)
        b.upload("/upload", csv)
        self.assertIn("holding as many uploaded files", b.get("/")[1])
        self.assertEqual(count(r"(\d+) messages written", b.get("/inbox")[1]), 199)   # b's was refused
        a.upload("/upload", csv)                                                       # a may replace its own
        self.assertNotIn("holding as many", a.get("/")[1])
        a.post("/reset")                                                               # freeing the slot...
        b.upload("/upload", csv)
        self.assertNotEqual(count(r"(\d+) messages written", b.get("/inbox")[1]), 199)  # ...lets b in

    def test_imports_are_bounded(self):
        a = self.visitor()
        a.get("/")
        sem = threading.BoundedSemaphore(1)
        sem.acquire()
        with mock.patch.object(DEMO, "IMPORT_SLOTS", sem):
            a.upload("/upload", a.get("/sample/simple.csv")[1])
        self.assertIn("reading other people", a.get("/")[1])

    def test_refuses_to_start_with_a_setup_token(self):
        env = {"REGULARS_PUBLIC_DEMO": "1", "REGULARS_SETUP_TOKEN": "t" * 32}
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", ["server.py", "0"]):
            with self.assertRaises(SystemExit) as cm:
                S.main()
        self.assertIn("can't be combined", str(cm.exception))


class TestVisitorsUnit(unittest.TestCase):
    def make(self, **kw):
        self.clock = _Clock()
        return DEMO.Visitors(lambda: {"dataset": None}, clock=self.clock, **{
            "ttl_seconds": 60, "max_sessions": 2, "max_uploads": 1, **kw})

    def test_attach_new_existing_expired(self):
        v = self.make()
        a, status = v.attach("")
        self.assertEqual(status, "new")
        again, status = v.attach(a.token)
        self.assertIs(again, a)
        self.assertEqual(status, "existing")
        self.clock.t += 61
        fresh, status = v.attach(a.token)
        self.assertEqual(status, "expired")
        self.assertIsNot(fresh, a)
        self.assertNotEqual(fresh.token, a.token)

    def test_tokens_are_unguessable_and_a_client_cannot_choose_one(self):
        v = self.make(max_sessions=50)
        tokens = {v.attach("")[0].token for _ in range(40)}
        self.assertEqual(len(tokens), 40)
        chosen = "A" * 30
        got, status = v.attach(chosen)
        self.assertNotEqual(got.token, chosen)
        self.assertEqual(status, "expired")
        for bad in ("short", "has space " * 3, "x" * 100, "../../etc/passwd" * 2):
            self.assertEqual(v.attach(bad)[1], "new")

    def test_full_and_throttled(self):
        v = self.make()
        v.attach(""), v.attach("")
        with self.assertRaises(DEMO.Full):
            v.attach("")
        self.clock.t += 61
        v.attach("")                                       # idle ones make room
        with self.assertRaises(DEMO.Throttled):
            self.make().attach("", may_create=lambda: False)

    def test_upload_slots(self):
        v = self.make()
        a, _ = v.attach("")
        b, _ = v.attach("")
        self.assertTrue(v.may_upload(a))
        a.state["dataset"] = {"customers": []}
        self.assertEqual(v.uploads(), 1)
        self.assertTrue(v.may_upload(a))                   # it may replace its own
        self.assertFalse(v.may_upload(b))
        self.clock.t += 61
        v.attach("")                                       # a is swept: its dataset is freed
        self.assertEqual(v.uploads(), 0)

    def test_cookie_helpers(self):
        self.assertEqual(DEMO.token_from_cookie("a=1; REGULARS_DEMO=abc; b=2"), "abc")
        self.assertEqual(DEMO.token_from_cookie(None), "")
        self.assertIn("Secure", DEMO.cookie_header("t" * 24, tls=True))
        self.assertNotIn("Secure", DEMO.cookie_header("t" * 24, tls=False))


class TestStateProxy(unittest.TestCase):
    def test_follows_the_thread(self):
        a = DEMO.Visitor("a" * 24, ST.fresh_state(), 0)
        b = DEMO.Visitor("b" * 24, ST.fresh_state(), 0)
        seen, barrier = {}, threading.Barrier(2)

        def work(name, visitor):
            ST.set_visitor(visitor)
            try:
                ST.STATE["marker"] = name
                ST.flash(f"note for {name}")
                barrier.wait()
                seen[name] = (ST.STATE["marker"], ST.take_flash())
            finally:
                ST.set_visitor(None)

        threads = [threading.Thread(target=work, args=(n, v)) for n, v in (("a", a), ("b", b))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(seen, {"a": ("a", ["note for a"]), "b": ("b", ["note for b"])})
        self.assertNotIn("marker", ST.STATE)               # no visitor here: the instance's own dict
        self.assertNotIn("marker", ST._BASE)

    def test_behaves_like_the_dict_it_stands_for(self):
        v = DEMO.Visitor("v" * 24, ST.fresh_state(), 0)
        ST.set_visitor(v)
        try:
            ST.STATE.update(ST.fresh_state())
            ST.STATE.setdefault("extra", []).append(1)
            self.assertEqual(ST.STATE["extra"], [1])
            self.assertIn("goals", ST.STATE)
            self.assertEqual(ST.STATE.pop("extra"), [1])
            self.assertEqual(dict(ST.STATE), v.state)
            self.assertEqual(ST.STATE.copy(), v.state)
        finally:
            ST.set_visitor(None)


class TestNormalInstanceIsUnchanged(_Harness):
    PUBLIC = False

    def test_real_data_still_needs_business_details_before_approving(self):
        a = self.visitor()
        a.upload("/upload", a.get("/sample/simple.csv")[1], consent="1")
        _, inbox = a.get("/inbox")
        self.assertIn("approving and exporting are switched off", inbox)
        a.post("/approve-all")
        self.assertEqual(count(r"<b>(\d+) approved</b>", a.get("/inbox")[1]) or 0, 0)

    def test_no_sandboxes_no_cookie_no_notice(self):
        a = self.visitor()
        status, body = a.get("/inbox")
        self.assertEqual(status, 200)
        self.assertIsNone(a.headers.get("Set-Cookie"))
        self.assertNotIn("Public demo.", body)
        self.assertIn('href="/security"', body)
        self.assertEqual(a.get("/security")[0], 200)
        self.assertEqual(len(ST.VISITORS), 0)


if __name__ == "__main__":
    unittest.main()
