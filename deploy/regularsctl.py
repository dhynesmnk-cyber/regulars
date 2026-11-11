#!/usr/bin/env python3
"""
regularsctl - run many Regulars venues on one server.

One venue = one container (regulars-<slug>), one Docker volume (same name),
one port on 127.0.0.1, and one Caddy site at https://<slug>.<domain>.

    regularsctl init --domain regulars.example.com.au
    regularsctl provision juniper-co            prints the owner's setup link
    regularsctl status [--quiet]                --quiet: exit 1 if any venue is down
    regularsctl upgrade [slug ...]              rebuild, recreate, roll back on failure
    regularsctl backup [--to DIR] [--keep 14]
    regularsctl restore juniper-co ARCHIVE --yes
    regularsctl link juniper-co [--reset-owner] a fresh setup link
    regularsctl paid juniper-co --months 1      record a payment

Add --dry-run to any command to print what it would do. Runs as root on the
server; Python standard library only. Secrets live in /etc/regulars/venues/
<slug>.env (mode 0600), outside the data volumes, so backups never hold them.
"""

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
IMAGE = "regulars"
APP_PORT = 8010
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$")
RESERVED = {"www", "mail", "api", "admin"}
DEFAULTS = {
    "base_port": 8100,
    "caddy_dir": "/etc/caddy/regulars",
    "backup_dir": "/var/backups/regulars",
    "caddy_reload": ["systemctl", "reload", "caddy"],
}


class Fail(Exception):
    """A refusal or failure the operator should read; exits 1."""


# --------------------------------------------------------------------------- io

class Runner:
    """Runs docker/caddy commands, or prints them under --dry-run. Tests
    swap in a fake that records commands and answers health checks."""

    def __init__(self, dry=False, out=sys.stdout):
        self.dry = dry
        self.out = out

    def say(self, msg=""):
        print(msg, file=self.out)

    def run(self, args, check=True, stdin=None):
        if self.dry:
            self.say("+ " + shlex.join(args))
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if stdin is None:       # never let docker -i wait on the terminal
            res = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True)
        else:
            res = subprocess.run(args, input=stdin, capture_output=True)
        if check and res.returncode:
            raise Fail(f"command failed ({res.returncode}): {shlex.join(args)}\n"
                       + res.stderr.decode(errors="replace").strip())
        return res

    def health(self, port):
        """The venue's /healthz as a dict, or None if it doesn't answer."""
        if self.dry:
            return {"status": "ok", "version": "?", "auth_enabled": False}
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as r:
                return json.loads(r.read().decode())
        except (OSError, ValueError):
            return None

    def sleep(self, seconds):
        if not self.dry:
            time.sleep(seconds)

    def write(self, path, text, mode=0o644):
        if self.dry:
            self.say(f"+ write {path} (mode {mode:o})")
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w" if isinstance(text, str) else "wb") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)

    def remove(self, path):
        if self.dry:
            self.say(f"+ rm {path}")
        elif os.path.exists(path):
            os.remove(path)


class Ctx:
    def __init__(self, runner, root="/"):
        self.r = runner
        self.root = root

    def p(self, absolute):
        """A server path, re-rooted for tests."""
        return os.path.join(self.root, absolute.lstrip("/"))

    @property
    def etc(self):
        return self.p("/etc/regulars")

    @property
    def venues_dir(self):
        return os.path.join(self.etc, "venues")

    def config(self):
        try:
            with open(os.path.join(self.etc, "config.json"), encoding="utf-8") as f:
                return {**DEFAULTS, **json.load(f)}
        except FileNotFoundError:
            raise Fail("not set up yet: run  regularsctl init --domain <your domain>  first")

    def slugs(self):
        if not os.path.isdir(self.venues_dir):
            return []
        return sorted(n[:-5] for n in os.listdir(self.venues_dir) if n.endswith(".json"))

    def meta(self, slug):
        try:
            with open(os.path.join(self.venues_dir, slug + ".json"), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            raise Fail(f"no venue called {slug!r} (see: regularsctl status)")

    def env_path(self, slug):
        return os.path.join(self.venues_dir, slug + ".env")

    def read_env(self, slug):
        env = {}
        with open(self.env_path(slug), encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    env[k] = v
        return env

    def write_env(self, slug, env):
        body = ("# Regulars venue secrets. Root only. Keep a copy of REGULARS_MASTER_KEY in "
                "your password manager:\n# without it, stored Square tokens can't be "
                "decrypted.\n" + "".join(f"{k}={v}\n" for k, v in env.items()))
        self.r.write(self.env_path(slug), body, 0o600)


# ---------------------------------------------------------------------- helpers

def name(slug):
    return f"regulars-{slug}"


def host(cfg, slug):
    return f"{slug}.{cfg['domain']}"


def caddy_site(cfg, slug, port):
    return (f"# Regulars venue {slug} - written by regularsctl\n"
            f"{host(cfg, slug)} {{\n"
            f"    reverse_proxy 127.0.0.1:{port} {{\n"
            f"        header_up X-Forwarded-For {{remote_host}}\n"
            f"    }}\n"
            f"}}\n")


def next_port(ctx, cfg):
    used = {ctx.meta(s)["port"] for s in ctx.slugs()}
    port = cfg["base_port"]
    while port in used:
        port += 1
    return port


def app_module(module):
    """Import one of the app's dependency-free modules (packs, billing), so
    the rules live in one place."""
    sys.path.insert(0, os.path.join(REPO, "app"))
    try:
        return __import__(module)
    finally:
        sys.path.pop(0)


def industries():
    return app_module("packs").AVAILABLE


def billing_line(meta, on_day):
    """What `status` says about a venue's plan."""
    if meta.get("billing", "on") != "on":
        return "demo, no billing" if meta.get("demo") else "no billing"
    bill = app_module("billing")
    paid = date.fromisoformat(meta["paid_until"]) if meta.get("paid_until") else None
    st = bill.status(paid, on_day)
    return {"preview": "preview (not paid yet)", "active": f"paid to {paid}",
            "due_soon": f"due {paid}", "grace": f"GRACE, ended {paid}",
            "lapsed": f"LAPSED {paid}"}[st]


def image_exists(ctx, tag):
    return ctx.r.run(["docker", "image", "inspect", f"{IMAGE}:{tag}"], check=False).returncode == 0


def build_image(ctx):
    """Build from this checkout; tag it with the commit and as :current."""
    sha = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    tag = sha or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ctx.r.say(f"building {IMAGE}:{tag} from {REPO}")
    ctx.r.run(["docker", "build", "-t", f"{IMAGE}:{tag}", REPO])
    return tag


def run_container(ctx, slug, tag="current", meta=None):
    """The one place a venue container is created: provision, upgrade, link
    and restore all come through here, so every venue runs the same way."""
    meta = meta or ctx.meta(slug)
    ctx.r.run(["docker", "rm", "-f", name(slug)], check=False)
    args = ["docker", "run", "-d", "--name", name(slug), "--restart", "unless-stopped",
            "-p", f"127.0.0.1:{meta['port']}:{APP_PORT}",
            "-v", f"{name(slug)}:/data",
            "--env-file", ctx.env_path(slug)]
    shared = os.path.join(ctx.etc, "shared.env")
    if os.path.exists(shared):
        args += ["--env-file", shared]
    args += ["--log-opt", "max-size=10m", "--log-opt", "max-file=3",
             "--label", f"regulars.slug={slug}", f"{IMAGE}:{tag}"]
    ctx.r.run(args)


def one_off(ctx, slug, command, stdin=None, read_only=False, tag="current"):
    """Run a command in a throwaway container on the venue's volume."""
    vol = f"{name(slug)}:/data" + (":ro" if read_only else "")
    args = (["docker", "run", "--rm"] + (["-i"] if stdin is not None else [])
            + ["-v", vol, "--env-file", ctx.env_path(slug), f"{IMAGE}:{tag}"] + command)
    return ctx.r.run(args, stdin=stdin)


def wait_healthy(ctx, port, tries=60):
    for _ in range(tries):
        h = ctx.r.health(port)
        if h and h.get("status") == "ok":
            return h
        ctx.r.sleep(1)
    return None


def backup_one(ctx, slug, out_dir, stamp):
    """Settings and audit log only: the master key lives in the .env file,
    outside the volume, and an auto-generated keyfile is excluded too."""
    res = one_off(ctx, slug, ["tar", "-C", "/data", "--exclude=regulars-master.key",
                              "--exclude=*.tmp", "-czf", "-", "."], read_only=True)
    path = os.path.join(out_dir, f"{slug}-{stamp}.tar.gz")
    ctx.r.write(path, res.stdout, 0o600)
    return path


def reload_caddy(ctx, cfg):
    ctx.r.run(list(cfg["caddy_reload"]))


# --------------------------------------------------------------------- commands

def cmd_init(ctx, a):
    cfg = {"domain": a.domain.strip().lower().rstrip("."), **{k: v for k, v in DEFAULTS.items()}}
    if a.caddy_reload:
        cfg["caddy_reload"] = shlex.split(a.caddy_reload)
    if a.base_port:
        cfg["base_port"] = a.base_port
    if not re.match(r"^[a-z0-9.-]+\.[a-z0-9-]+$", cfg["domain"]):
        raise Fail(f"{a.domain!r} doesn't look like a domain")
    for d in (ctx.etc, ctx.venues_dir):
        if not ctx.r.dry:
            os.makedirs(d, mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)
    if not ctx.r.dry:
        os.makedirs(ctx.p(cfg["caddy_dir"]), exist_ok=True)
    ctx.r.write(os.path.join(ctx.etc, "config.json"), json.dumps(cfg, indent=2) + "\n", 0o600)
    missing = [t for t in ("docker",) if not shutil.which(t)]
    ctx.r.say(f"ready: venues will be https://<slug>.{cfg['domain']}")
    if missing:
        ctx.r.say(f"warning: not installed: {', '.join(missing)}")
    ctx.r.say(f"next: point the DNS record *.{cfg['domain']} at this server, and add this "
              f"line to /etc/caddy/Caddyfile:\n    import {cfg['caddy_dir']}/*.caddy")
    return 0


def cmd_provision(ctx, a):
    cfg = ctx.config()
    slug = a.slug.strip().lower()
    if not SLUG_RE.match(slug) or "--" in slug or slug in RESERVED:
        raise Fail(f"{a.slug!r} can't be a venue name: use 3-30 lower-case letters, digits "
                   f"and single hyphens, e.g. juniper-co")
    if slug in ctx.slugs():
        raise Fail(f"{slug} already exists (see: regularsctl status)")
    if a.industry not in industries():
        raise Fail(f"unknown industry {a.industry!r}; available: {', '.join(industries())}")
    if not image_exists(ctx, "current"):
        tag = build_image(ctx)
        ctx.r.run(["docker", "tag", f"{IMAGE}:{tag}", f"{IMAGE}:current"])

    port = next_port(ctx, cfg)
    token = secrets.token_urlsafe(32)
    master = secrets.token_hex(32)
    meta = {"slug": slug, "port": port, "industry": a.industry, "host": host(cfg, slug),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "billing": "off" if (a.no_billing or a.demo) else "on"}
    if a.demo:
        meta["demo"] = True
    caddy_file = os.path.join(ctx.p(cfg["caddy_dir"]), slug + ".caddy")
    written = []
    try:
        ctx.r.write(os.path.join(ctx.venues_dir, slug + ".json"),
                    json.dumps(meta, indent=2) + "\n", 0o600)
        written.append(os.path.join(ctx.venues_dir, slug + ".json"))
        env = {"REGULARS_MASTER_KEY": master, "REGULARS_SETUP_TOKEN": token,
               "REGULARS_INDUSTRY": a.industry,
               "REGULARS_REDIRECT_URI": f"https://{meta['host']}/oauth/callback",
               "REGULARS_VENUE_ID": slug,
               "REGULARS_BILLING": meta["billing"]}
        if a.demo:
            env["REGULARS_DEMO"] = "1"
        ctx.write_env(slug, env)
        written.append(ctx.env_path(slug))
        run_container(ctx, slug, meta=meta)
        ctx.r.write(caddy_file, caddy_site(cfg, slug, port))
        written.append(caddy_file)
        reload_caddy(ctx, cfg)
        if not wait_healthy(ctx, port):
            raise Fail(f"{slug} started but /healthz never answered on port {port}; "
                       f"see: docker logs {name(slug)}")
    except Exception:
        ctx.r.say(f"provisioning {slug} failed; undoing")
        ctx.r.run(["docker", "rm", "-f", name(slug)], check=False)
        for path in written:
            ctx.r.remove(path)
        if caddy_file in written:
            ctx.r.run(list(cfg["caddy_reload"]), check=False)
        raise

    if a.demo:
        first = ("Open your one-time setup link and set your own password. That password is "
                 "the demo's only gate, so screen-share it or give it only to people you "
                 "trust: everyone signed in shares one state. Uploads are switched off.")
    else:
        first = "Send the owner their one-time setup link:"
    ctx.r.say(f"""
{slug} is up at https://{meta['host']} (port {port}, {a.industry}{', demo' if a.demo else ''}).

1. {first}
     https://{meta['host']}/welcome?token={token}
2. Add this to your uptime monitor:
     https://{meta['host']}/healthz
3. Save this venue's master key in your password manager (it is also in
   {ctx.env_path(slug)}). Without it, stored Square tokens can't be read:
     {master}
""".rstrip())
    return 0


def cmd_status(ctx, a):
    ctx.config()
    down = []
    rows = []
    on_day = datetime.now(timezone.utc).date()
    for slug in ctx.slugs():
        meta = ctx.meta(slug)
        res = ctx.r.run(["docker", "inspect", "-f", "{{.State.Status}}", name(slug)], check=False)
        state = res.stdout.decode().strip() if res.returncode == 0 else "missing"
        h = ctx.r.health(meta["port"])
        if not h:
            down.append(slug)
            rows.append(f"{slug:24} {meta['port']:5}  {state:9}  DOWN")
            continue
        claimed = "claimed" if h.get("auth_enabled") else "waiting for owner"
        rows.append(f"{slug:24} {meta['port']:5}  {state:9}  ok  v{h.get('version', '?')}  "
                    f"{claimed:17}  {billing_line(meta, on_day)}")
    if not a.quiet:
        ctx.r.say("\n".join(rows) if rows else "no venues yet (regularsctl provision <slug>)")
        if down:
            ctx.r.say(f"DOWN: {', '.join(down)}")
    elif down:
        ctx.r.say(f"regulars: DOWN: {', '.join(down)}")
    return 1 if down else 0


def cmd_upgrade(ctx, a):
    ctx.config()
    slugs = a.slugs or ctx.slugs()
    for s in slugs:
        ctx.meta(s)
    if image_exists(ctx, "current"):
        ctx.r.run(["docker", "tag", f"{IMAGE}:current", f"{IMAGE}:previous"])
    tag = a.tag or build_image(ctx)
    ctx.r.run(["docker", "tag", f"{IMAGE}:{tag}", f"{IMAGE}:current"])
    failed = []
    for i, slug in enumerate(slugs):
        port = ctx.meta(slug)["port"]
        run_container(ctx, slug, "current")
        if wait_healthy(ctx, port, tries=a.wait):
            ctx.r.say(f"{slug}: upgraded to {tag}")
            continue
        ctx.r.say(f"{slug}: unhealthy on {tag}; rolling back")
        run_container(ctx, slug, "previous")
        failed.append(slug)
        if wait_healthy(ctx, port, tries=a.wait):
            ctx.r.say(f"{slug}: back up on the previous image")
        else:
            ctx.r.say(f"{slug}: STILL DOWN after rolling back - see: docker logs {name(slug)}")
        if i == 0:
            # The first venue is the canary: stop here and keep new venues on
            # the known-good image.
            ctx.r.run(["docker", "tag", f"{IMAGE}:previous", f"{IMAGE}:current"])
            ctx.r.say("stopped after the first venue; :current points at the previous image "
                      "again. Nothing else was touched.")
            return 1
    if failed:
        ctx.r.say(f"rolled back: {', '.join(failed)}")
        return 1
    return 0


def cmd_backup(ctx, a):
    cfg = ctx.config()
    out_dir = a.to or ctx.p(cfg["backup_dir"])
    if not ctx.r.dry:
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    made = [backup_one(ctx, slug, out_dir, stamp) for slug in ctx.slugs()]
    cutoff = time.time() - a.keep * 86400
    pruned = 0
    if not ctx.r.dry and os.path.isdir(out_dir):
        for fn in os.listdir(out_dir):
            full = os.path.join(out_dir, fn)
            if fn.endswith(".tar.gz") and os.path.getmtime(full) < cutoff:
                os.remove(full)
                pruned += 1
    ctx.r.say(f"backed up {len(made)} venue(s) to {out_dir}; removed {pruned} older than "
              f"{a.keep} days")
    return 0


def cmd_restore(ctx, a):
    meta = ctx.meta(a.slug)
    if not a.yes:
        raise Fail(f"restore replaces {a.slug}'s current settings and audit log with the "
                   f"archive. Run again with --yes to go ahead.")
    with open(a.archive, "rb") as f:
        data = f.read()
    cfg = ctx.config()
    out_dir = ctx.p(cfg["backup_dir"])
    if not ctx.r.dry:
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
    safety = backup_one(ctx, a.slug, out_dir,
                        "pre-restore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    ctx.r.say(f"safety copy of the current state: {safety}")
    ctx.r.run(["docker", "stop", name(a.slug)], check=False)
    one_off(ctx, a.slug, ["sh", "-c", "find /data -mindepth 1 -delete && tar -C /data -xzf -"],
            stdin=data)
    run_container(ctx, a.slug)
    if not wait_healthy(ctx, meta["port"]):
        raise Fail(f"{a.slug} didn't come back healthy after the restore; see: "
                   f"docker logs {name(a.slug)}")
    res = ctx.r.run(["docker", "exec", name(a.slug), "python3", "admin.py", "verify-audit"],
                    check=False)
    ctx.r.say(res.stdout.decode().strip() or "audit check ran")
    if res.returncode:
        raise Fail("restored, but the audit chain does not verify")
    ctx.r.say(f"{a.slug} restored from {os.path.basename(a.archive)}")
    return 0


def cmd_paid(ctx, a):
    """Record a payment: --months N from the later of today and the current
    paid-up date, or an exact --until. Takes effect at once, no restart."""
    ctx.config()
    meta = ctx.meta(a.slug)
    if meta.get("billing", "on") != "on":
        raise Fail(f"{a.slug} was set up with {'--demo' if meta.get('demo') else '--no-billing'}"
                   f", so it is never billed")
    today = datetime.now(timezone.utc).date()
    if a.until:
        try:
            new = date.fromisoformat(a.until)
        except ValueError:
            raise Fail(f"not a date: {a.until!r} (use YYYY-MM-DD)")
    else:
        current = date.fromisoformat(meta["paid_until"]) if meta.get("paid_until") else None
        new = app_module("billing").add_months(max(today, current) if current else today,
                                              a.months)
    res = ctx.r.run(["docker", "exec", name(a.slug), "python3", "admin.py", "paid-until",
                     new.isoformat()], check=False)
    if res.returncode:
        raise Fail(f"couldn't record it inside {a.slug} - is it running? (regularsctl status)")
    meta["paid_until"] = new.isoformat()
    ctx.r.write(os.path.join(ctx.venues_dir, a.slug + ".json"),
                json.dumps(meta, indent=2) + "\n", 0o600)
    ctx.r.say(f"{a.slug} is paid up to {new.isoformat()}; approving and exporting work now.")
    return 0


def cmd_link(ctx, a):
    cfg = ctx.config()
    meta = ctx.meta(a.slug)
    h = wait_healthy(ctx, meta["port"], tries=30)
    if not h:
        raise Fail(f"{a.slug} isn't answering, so it can't tell whether the owner has already "
                   f"set a password. Check: regularsctl status, docker logs {name(a.slug)}")
    if h.get("auth_enabled") and not a.reset_owner:
        raise Fail(f"{a.slug} is already claimed, so a new link would only say 'already set "
                   f"up'. If the owner is locked out, run again with --reset-owner.")
    if a.reset_owner:
        ctx.r.run(["docker", "stop", name(a.slug)], check=False)
        one_off(ctx, a.slug, ["python3", "admin.py", "reset-owner"])
    env = ctx.read_env(a.slug) if not ctx.r.dry else {}
    token = secrets.token_urlsafe(32)
    env["REGULARS_SETUP_TOKEN"] = token
    ctx.write_env(a.slug, env)
    run_container(ctx, a.slug)
    if not wait_healthy(ctx, meta["port"]):
        raise Fail(f"{a.slug} didn't come back healthy; see: docker logs {name(a.slug)}")
    ctx.r.say(f"new one-time setup link for {a.slug} (the old one no longer works):\n"
              f"    https://{host(cfg, a.slug)}/welcome?token={token}")
    return 0


# ------------------------------------------------------------------------ main

def parser():
    ap = argparse.ArgumentParser(prog="regularsctl", description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print what would happen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="first-time setup on this server")
    p.add_argument("--domain", required=True)
    p.add_argument("--caddy-reload", help='command that reloads Caddy '
                                          '(default: "systemctl reload caddy")')
    p.add_argument("--base-port", type=int)
    p = sub.add_parser("provision", help="create a venue and print its setup link")
    p.add_argument("slug")
    p.add_argument("--industry", default="restaurant")
    p.add_argument("--demo", action="store_true",
                   help="a sales demo: the example restaurant, uploads off, never billed. "
                        "You set its password from the setup link")
    p.add_argument("--no-billing", action="store_true",
                   help="never ask this venue to pay (demo or friends-and-family instances)")
    p = sub.add_parser("paid", help="record a payment, e.g. --months 1")
    p.add_argument("slug")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--months", type=int, help="extend by this many months")
    when.add_argument("--until", help="paid up to this day, YYYY-MM-DD")
    p = sub.add_parser("status", help="list venues and their health")
    p.add_argument("--quiet", action="store_true", help="silent unless something is down")
    p = sub.add_parser("upgrade", help="rebuild the image and recreate venues on it")
    p.add_argument("slugs", nargs="*")
    p.add_argument("--tag", help="deploy an existing image tag instead of building")
    p.add_argument("--wait", type=int, default=60, help="seconds to wait for health")
    p = sub.add_parser("backup", help="archive every venue's settings and audit log")
    p.add_argument("--to")
    p.add_argument("--keep", type=int, default=14, help="days of archives to keep")
    p = sub.add_parser("restore", help="put a venue back to a backup")
    p.add_argument("slug")
    p.add_argument("archive")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("link", help="issue a new one-time setup link")
    p.add_argument("slug")
    p.add_argument("--reset-owner", action="store_true",
                   help="also clear the owner's password, for a locked-out owner")
    return ap


COMMANDS = {"init": cmd_init, "provision": cmd_provision, "status": cmd_status,
            "upgrade": cmd_upgrade, "backup": cmd_backup, "restore": cmd_restore,
            "link": cmd_link, "paid": cmd_paid}


def main(argv=None, runner=None, root=None):
    a = parser().parse_args(argv)
    runner = runner or Runner()
    runner.dry = runner.dry or a.dry_run
    ctx = Ctx(runner, root or os.environ.get("REGULARSCTL_ROOT", "/"))
    try:
        return COMMANDS[a.cmd](ctx, a)
    except Fail as ex:
        ctx.r.say(f"regularsctl: {ex}")
        return 1


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)     # `regularsctl status | head` is fine
    sys.exit(main())
