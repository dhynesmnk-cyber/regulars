"""
Tests for deploy/regularsctl.py (the operator tool) and app/admin.py.

regularsctl runs against a fake Docker: FakeDocker records every command,
tracks which image each container runs and which tags point where, and
answers /healthz the way a real venue would. Files go to a temp directory
standing in for the server's /.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "app")
_spec = importlib.util.spec_from_file_location(
    "regularsctl", os.path.join(HERE, "..", "deploy", "regularsctl.py"))
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)


def _archive(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo("./" + name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeDocker(rc.Runner):
    def __init__(self):
        super().__init__(dry=False, out=io.StringIO())
        self.cmds = []
        self.tags = {"current": "v1", "v1": "v1"}    # tag -> concrete image
        self.containers = {}                          # name -> concrete image
        self.ports = {}                               # host port -> container name
        self.broken = set()                           # images that never get healthy
        self.claimed = set()
        self.tar = _archive({"regulars-state.json": "{}", "regulars-audit.log": ""})

    def resolve(self, tag):
        return self.tags.get(tag)

    def run(self, args, check=True, stdin=None):
        self.cmds.append((list(args), stdin))
        ok = subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(args, 0 if self.resolve(args[3].split(":")[1])
                                               else 1, b"", b"")
        if args[:2] == ["docker", "build"]:
            tag = args[args.index("-t") + 1].split(":")[1]
            self.tags[tag] = tag
        elif args[:2] == ["docker", "tag"]:
            self.tags[args[3].split(":")[1]] = self.resolve(args[2].split(":")[1])
        elif args[:3] == ["docker", "rm", "-f"]:
            self.containers.pop(args[3], None)
        elif args[:3] == ["docker", "run", "-d"]:
            name = args[args.index("--name") + 1]
            port = int(args[args.index("-p") + 1].split(":")[1])
            self.containers[name] = self.resolve(args[-1].split(":")[1])
            self.ports[port] = name
        elif args[:2] == ["docker", "stop"]:
            self.containers.pop(args[2], None)
        elif args[:3] == ["docker", "run", "--rm"] and "-czf" in args:
            return subprocess.CompletedProcess(args, 0, self.tar, b"")
        elif args[:2] == ["docker", "inspect"]:
            running = args[-1] in self.containers
            return subprocess.CompletedProcess(args, 0 if running else 1,
                                               b"running\n" if running else b"", b"")
        elif args[:2] == ["docker", "exec"]:
            if args[2] not in self.containers:          # like docker: no exec into a stopped one
                return subprocess.CompletedProcess(args, 1, b"", b"not running")
            return subprocess.CompletedProcess(args, 0, b"audit chain intact: 3 entries", b"")
        return ok

    def health(self, port):
        name = self.ports.get(port)
        image = self.containers.get(name)
        if not image or image in self.broken:
            return None
        return {"status": "ok", "version": image, "auth_enabled": name in self.claimed}

    def sleep(self, seconds):
        pass

    # helpers for assertions
    def docker_runs(self, name=None):
        return [a for a, _ in self.cmds if a[:3] == ["docker", "run", "-d"]
                and (name is None or a[a.index("--name") + 1] == name)]

    def output(self):
        return self.out.getvalue()


class CtlCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.d = FakeDocker()
        self.assertEqual(self.ctl("init", "--domain", "example.com.au"), 0)

    def tearDown(self):
        self._tmp.cleanup()

    def ctl(self, *argv):
        return rc.main(list(argv), runner=self.d, root=self.root)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def env(self, slug):
        out = {}
        with open(self.path("etc/regulars/venues", slug + ".env")) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
        return out

    def meta(self, slug):
        with open(self.path("etc/regulars/venues", slug + ".json")) as f:
            return json.load(f)


class TestInitAndProvision(CtlCase):
    def test_init_locks_down_its_directories(self):
        self.assertEqual(os.stat(self.path("etc/regulars")).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.path("etc/regulars/venues")).st_mode & 0o777, 0o700)
        with open(self.path("etc/regulars/config.json")) as f:
            cfg = json.load(f)
        self.assertEqual(cfg["domain"], "example.com.au")
        self.assertIn("import /etc/caddy/regulars/*.caddy", self.d.output())

    def test_provision_writes_secrets_site_and_link(self):
        self.assertEqual(self.ctl("provision", "juniper-co"), 0)
        env_file = self.path("etc/regulars/venues/juniper-co.env")
        self.assertEqual(os.stat(env_file).st_mode & 0o777, 0o600)
        env = self.env("juniper-co")
        self.assertRegex(env["REGULARS_MASTER_KEY"], r"^[0-9a-f]{64}$")
        self.assertGreaterEqual(len(env["REGULARS_SETUP_TOKEN"]), 24)
        self.assertEqual(env["REGULARS_INDUSTRY"], "restaurant")
        self.assertEqual(env["REGULARS_REDIRECT_URI"],
                         "https://juniper-co.example.com.au/oauth/callback")
        self.assertEqual(self.meta("juniper-co")["port"], 8100)

        with open(self.path("etc/caddy/regulars/juniper-co.caddy")) as f:
            site = f.read()
        self.assertIn("juniper-co.example.com.au {", site)
        self.assertIn("reverse_proxy 127.0.0.1:8100", site)
        self.assertIn("header_up X-Forwarded-For {remote_host}", site)

        run = self.d.docker_runs("regulars-juniper-co")[0]
        self.assertIn("127.0.0.1:8100:8010", run)
        self.assertIn("regulars-juniper-co:/data", run)
        self.assertEqual(run[run.index("--restart") + 1], "unless-stopped")
        self.assertEqual(run[run.index("--env-file") + 1], env_file)
        self.assertFalse(any("0.0.0.0" in a for a in run))
        self.assertIn(["systemctl", "reload", "caddy"], [a for a, _ in self.d.cmds])

        out = self.d.output()
        self.assertIn("https://juniper-co.example.com.au/welcome?token="
                      + env["REGULARS_SETUP_TOKEN"], out)
        self.assertIn("https://juniper-co.example.com.au/healthz", out)
        self.assertIn(env["REGULARS_MASTER_KEY"], out)

    def test_shared_square_credentials_are_passed_when_present(self):
        with open(self.path("etc/regulars/shared.env"), "w") as f:
            f.write("SQUARE_CLIENT_ID=x\n")
        self.ctl("provision", "juniper-co")
        run = self.d.docker_runs()[0]
        self.assertIn(self.path("etc/regulars/shared.env"), run)

    def test_ports_go_in_order_and_gaps_are_reused(self):
        for slug in ("aaa", "bbb", "ccc"):
            self.ctl("provision", slug)
        self.assertEqual([self.meta(s)["port"] for s in ("aaa", "bbb", "ccc")],
                         [8100, 8101, 8102])
        os.remove(self.path("etc/regulars/venues/bbb.json"))
        self.ctl("provision", "ddd")
        self.assertEqual(self.meta("ddd")["port"], 8101)

    def test_bad_requests_are_refused_before_anything_runs(self):
        self.ctl("provision", "juniper-co")
        before = len(self.d.cmds)
        for slug in ("ab", "under_score", "café", "x--y", "-abc", "abc-", "www",
                     "has space", "a" * 31):
            self.assertEqual(self.ctl("provision", "--", slug), 1, slug)
        self.assertEqual(self.ctl("provision", "juniper-co"), 1)            # duplicate
        self.assertEqual(self.ctl("provision", "salon-one", "--industry", "salon"), 1)
        self.assertEqual(self.d.cmds[before:], [])
        self.assertEqual(sorted(os.listdir(self.path("etc/regulars/venues"))),
                         ["juniper-co.env", "juniper-co.json"])

    def test_a_venue_that_never_starts_is_undone(self):
        self.d.broken.add("v1")
        self.assertEqual(self.ctl("provision", "juniper-co"), 1)
        self.assertEqual(os.listdir(self.path("etc/regulars/venues")), [])
        self.assertFalse(os.path.exists(self.path("etc/caddy/regulars/juniper-co.caddy")))
        self.assertNotIn("regulars-juniper-co", self.d.containers)
        self.assertIn("failed; undoing", self.d.output())

    def test_names_are_lower_cased(self):
        self.assertEqual(self.ctl("provision", "Juniper-Co"), 0)
        self.assertEqual(self.meta("juniper-co")["host"], "juniper-co.example.com.au")

    def test_dry_run_changes_nothing(self):
        before = sorted(os.listdir(self.path("etc/regulars")))
        self.assertEqual(self.ctl("--dry-run", "provision", "juniper-co"), 0)
        self.assertEqual(sorted(os.listdir(self.path("etc/regulars"))), before)
        self.assertEqual(os.listdir(self.path("etc/regulars/venues")), [])

    def test_real_dry_run_prints_commands_and_writes_nothing(self):
        out = io.StringIO()
        tmp = tempfile.mkdtemp()
        rc.main(["--dry-run", "init", "--domain", "x.example"], runner=rc.Runner(True, out),
                root=tmp)
        self.assertEqual(os.listdir(tmp), [])
        self.assertIn("+ write", out.getvalue())


class TestStatusUpgrade(CtlCase):
    def setUp(self):
        super().setUp()
        for slug in ("aaa", "bbb"):
            self.ctl("provision", slug)

    def test_status_quiet_is_silent_until_something_is_down(self):
        self.d.out = io.StringIO()
        self.assertEqual(self.ctl("status", "--quiet"), 0)
        self.assertEqual(self.d.output(), "")
        self.d.containers.pop("regulars-bbb")
        self.assertEqual(self.ctl("status", "--quiet"), 1)
        self.assertIn("DOWN: bbb", self.d.output())

    def test_status_shows_who_has_claimed(self):
        self.d.claimed.add("regulars-aaa")
        self.d.out = io.StringIO()
        self.ctl("status")
        lines = self.d.output().splitlines()
        self.assertIn("claimed", [ln for ln in lines if ln.startswith("aaa")][0])
        self.assertIn("waiting for owner", [ln for ln in lines if ln.startswith("bbb")][0])

    def test_upgrade_moves_every_venue_and_keeps_the_old_image(self):
        self.d.tags["v2"] = "v2"
        self.assertEqual(self.ctl("upgrade", "--tag", "v2"), 0)
        self.assertEqual(self.d.containers, {"regulars-aaa": "v2", "regulars-bbb": "v2"})
        self.assertEqual((self.d.resolve("current"), self.d.resolve("previous")), ("v2", "v1"))

    def test_a_broken_image_stops_at_the_first_venue_and_rolls_back(self):
        self.d.tags["v2"] = "v2"
        self.d.broken.add("v2")
        self.assertEqual(self.ctl("upgrade", "--tag", "v2", "--wait", "2"), 1)
        self.assertEqual(self.d.containers, {"regulars-aaa": "v1", "regulars-bbb": "v1"})
        self.assertEqual(self.d.resolve("current"), "v1")          # new venues stay safe
        upgraded = [a for a in self.d.docker_runs("regulars-bbb")]
        self.assertEqual(len(upgraded), 1)                         # bbb never touched
        self.assertIn("aaa: back up on the previous image", self.d.output())

    def test_a_rollback_that_also_fails_is_reported(self):
        self.d.tags["v2"] = "v2"
        self.d.broken.update({"v1", "v2"})
        self.assertEqual(self.ctl("upgrade", "--tag", "v2", "--wait", "2"), 1)
        self.assertIn("aaa: STILL DOWN after rolling back", self.d.output())


class TestBackupRestoreLink(CtlCase):
    def setUp(self):
        super().setUp()
        self.ctl("provision", "juniper-co")
        self.backups = self.path("var/backups/regulars")

    def test_backup_is_private_secret_free_and_pruned(self):
        os.makedirs(self.backups, exist_ok=True)
        old = os.path.join(self.backups, "juniper-co-20200101T000000Z.tar.gz")
        recent = os.path.join(self.backups, "juniper-co-recent.tar.gz")
        for p, age_days in ((old, 30), (recent, 2)):
            with open(p, "wb") as f:
                f.write(b"x")
            t = time.time() - age_days * 86400
            os.utime(p, (t, t))
        self.assertEqual(self.ctl("backup", "--keep", "14"), 0)
        files = sorted(os.listdir(self.backups))
        self.assertNotIn(os.path.basename(old), files)
        self.assertIn(os.path.basename(recent), files)
        made = [f for f in files if f not in (os.path.basename(recent),)]
        self.assertEqual(len(made), 1)
        full = os.path.join(self.backups, made[0])
        self.assertEqual(os.stat(full).st_mode & 0o777, 0o600)
        with tarfile.open(full) as t:
            names = sorted(n.lstrip("./") for n in t.getnames() if n.strip("./"))
        self.assertEqual(names, ["regulars-audit.log", "regulars-state.json"])
        cmd = [a for a, _ in self.d.cmds if "-czf" in a][0]
        self.assertIn("regulars-juniper-co:/data:ro", cmd)
        self.assertIn("--exclude=regulars-master.key", cmd)

    def test_restore_needs_yes_then_keeps_a_safety_copy(self):
        archive = os.path.join(self.root, "a.tar.gz")
        with open(archive, "wb") as f:
            f.write(self.d.tar)
        self.assertEqual(self.ctl("restore", "juniper-co", archive), 1)
        self.assertFalse(any(a[:2] == ["docker", "stop"] for a, _ in self.d.cmds))
        self.assertEqual(self.ctl("restore", "juniper-co", archive, "--yes"), 0)
        self.assertTrue(any(f.startswith("juniper-co-pre-restore-")
                            for f in os.listdir(self.backups)))
        unpack = [(a, s) for a, s in self.d.cmds if "-xzf" in " ".join(a)][0]
        self.assertEqual(unpack[1], self.d.tar)
        self.assertIn(["docker", "exec", "regulars-juniper-co", "python3", "admin.py",
                       "verify-audit"], [a for a, _ in self.d.cmds])

    def test_link_rotates_only_the_token(self):
        before = self.env("juniper-co")
        self.assertEqual(self.ctl("link", "juniper-co"), 0)
        after = self.env("juniper-co")
        self.assertNotEqual(after["REGULARS_SETUP_TOKEN"], before["REGULARS_SETUP_TOKEN"])
        self.assertEqual(after["REGULARS_MASTER_KEY"], before["REGULARS_MASTER_KEY"])
        self.assertIn("welcome?token=" + after["REGULARS_SETUP_TOKEN"], self.d.output())
        self.assertEqual(len(self.d.docker_runs("regulars-juniper-co")), 2)

    def test_link_refuses_when_the_venue_is_not_answering(self):
        token = self.env("juniper-co")["REGULARS_SETUP_TOKEN"]
        self.d.containers.pop("regulars-juniper-co")
        self.assertEqual(self.ctl("link", "juniper-co"), 1)
        self.assertIn("isn't answering", self.d.output())
        self.assertEqual(self.env("juniper-co")["REGULARS_SETUP_TOKEN"], token)

    def test_claimed_venue_needs_reset_owner(self):
        self.d.claimed.add("regulars-juniper-co")
        token = self.env("juniper-co")["REGULARS_SETUP_TOKEN"]
        self.assertEqual(self.ctl("link", "juniper-co"), 1)
        self.assertEqual(self.env("juniper-co")["REGULARS_SETUP_TOKEN"], token)
        self.assertEqual(self.ctl("link", "juniper-co", "--reset-owner"), 0)
        cmds = [a for a, _ in self.d.cmds]
        stop = cmds.index(["docker", "stop", "regulars-juniper-co"])
        reset = next(i for i, a in enumerate(cmds) if a[-3:] == ["python3", "admin.py",
                                                                    "reset-owner"])
        self.assertLess(stop, reset)            # the app is stopped before the reset
        self.assertNotEqual(self.env("juniper-co")["REGULARS_SETUP_TOKEN"], token)


class TestBillingCommands(CtlCase):
    def setUp(self):
        super().setUp()
        self.ctl("provision", "juniper-co")
        self.today = datetime.now(timezone.utc).date()
        self.bill = rc.app_module("billing")

    def exec_calls(self):
        return [a for a, _ in self.d.cmds if a[:2] == ["docker", "exec"] and "paid-until" in a]

    def test_provision_turns_billing_on_and_names_the_venue(self):
        env = self.env("juniper-co")
        self.assertEqual((env["REGULARS_BILLING"], env["REGULARS_VENUE_ID"]), ("on", "juniper-co"))
        self.assertEqual(self.meta("juniper-co")["billing"], "on")

    def test_no_billing_venues_are_never_charged(self):
        self.ctl("provision", "demo-venue", "--no-billing")
        self.assertEqual(self.env("demo-venue")["REGULARS_BILLING"], "off")
        self.assertEqual(self.ctl("paid", "demo-venue", "--months", "1"), 1)
        self.assertEqual(self.exec_calls(), [])

    def test_months_extend_from_today_then_from_the_paid_date(self):
        self.assertEqual(self.ctl("paid", "juniper-co", "--months", "1"), 0)
        first = self.bill.add_months(self.today, 1)
        self.assertEqual(self.meta("juniper-co")["paid_until"], first.isoformat())
        self.assertEqual(self.exec_calls()[-1], ["docker", "exec", "regulars-juniper-co",
                                                 "python3", "admin.py", "paid-until",
                                                 first.isoformat()])
        self.ctl("paid", "juniper-co", "--months", "1")       # paying early doesn't lose days
        self.assertEqual(self.meta("juniper-co")["paid_until"],
                         self.bill.add_months(first, 1).isoformat())

    def test_until_sets_an_exact_day_and_rejects_nonsense(self):
        self.assertEqual(self.ctl("paid", "juniper-co", "--until", "2027-01-31"), 0)
        self.assertEqual(self.meta("juniper-co")["paid_until"], "2027-01-31")
        self.assertEqual(self.ctl("paid", "juniper-co", "--until", "31/01/2027"), 1)
        self.assertEqual(self.meta("juniper-co")["paid_until"], "2027-01-31")

    def test_a_stopped_venue_is_not_marked_paid(self):
        self.d.containers.pop("regulars-juniper-co")
        self.assertEqual(self.ctl("paid", "juniper-co", "--months", "1"), 1)
        self.assertNotIn("paid_until", self.meta("juniper-co"))
        self.assertIn("is it running", self.d.output())

    def test_status_shows_each_venue_plan(self):
        self.ctl("provision", "paid-up")
        self.ctl("provision", "gone-quiet")
        self.ctl("paid", "paid-up", "--months", "2")
        self.ctl("paid", "gone-quiet", "--until", "2020-01-01")
        self.d.out = io.StringIO()
        self.ctl("status")
        rows = {ln.split()[0]: ln for ln in self.d.output().splitlines()}
        self.assertIn("preview (not paid yet)", rows["juniper-co"])
        self.assertIn("paid to ", rows["paid-up"])
        self.assertIn("LAPSED 2020-01-01", rows["gone-quiet"])


class TestAdmin(unittest.TestCase):
    """app/admin.py run for real against a temp state file and audit log."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ,
                        REGULARS_STATE=os.path.join(self._tmp.name, "state.json"),
                        REGULARS_AUDIT_LOG=os.path.join(self._tmp.name, "audit.log"))
        self.env.pop("REGULARS_SETUP_TOKEN", None)
        seed = ("import security as SEC, state\n"
                "state.STATE['password_hash'] = SEC.hash_secret('owner password!')\n"
                "state.STATE['sessions'] = {'t': {'created': 1}}\n"
                "state.persist()\n"
                "state.AUDIT.write('login_ok', ip='127.0.0.1')\n")
        self.admin("-c", seed)

    def tearDown(self):
        self._tmp.cleanup()

    def admin(self, *args):
        return subprocess.run([sys.executable, *args], cwd=APP, env=self.env,
                              capture_output=True, text=True)

    def test_reset_owner_clears_password_and_sessions_and_is_audited(self):
        res = self.admin("admin.py", "reset-owner")
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(self.env["REGULARS_STATE"]) as f:
            saved = json.load(f)
        self.assertIsNone(saved["password_hash"])
        self.assertEqual(saved["sessions"], {})
        with open(self.env["REGULARS_AUDIT_LOG"]) as f:
            last = json.loads(f.read().splitlines()[-1])
        self.assertEqual(last["detail"]["what"], "owner_reset")
        self.assertEqual(self.admin("admin.py", "verify-audit").returncode, 0)

    def test_verify_audit_catches_tampering(self):
        self.assertEqual(self.admin("admin.py", "verify-audit").returncode, 0)
        with open(self.env["REGULARS_AUDIT_LOG"]) as f:
            lines = f.read().splitlines()
        lines[0] = lines[0].replace("login_ok", "login_fail")
        with open(self.env["REGULARS_AUDIT_LOG"], "w") as f:
            f.write("\n".join(lines) + "\n")
        res = self.admin("admin.py", "verify-audit")
        self.assertEqual(res.returncode, 1)
        self.assertIn("BROKEN", res.stdout)

    def test_paid_until_writes_the_billing_file_beside_the_state(self):
        res = self.admin("admin.py", "paid-until", "2026-12-31")
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self._tmp.name, "regulars-billing.json")) as f:
            self.assertEqual(json.load(f)["paid_until"], "2026-12-31")
        self.assertEqual(self.admin("admin.py", "paid-until", "next tuesday").returncode, 2)
        self.assertEqual(self.admin("admin.py", "paid-until").returncode, 2)

    def test_unknown_command_shows_usage(self):
        res = self.admin("admin.py", "drop-tables")
        self.assertEqual(res.returncode, 2)
        self.assertIn("reset-owner", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
