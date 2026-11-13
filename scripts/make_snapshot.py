#!/usr/bin/env python3
"""Turn a running Regulars demo app into a read-only static site for Netlify.

    ./start.sh 8010 &                            # the app, demo mode, from the repo root
    python3 scripts/make_snapshot.py http://127.0.0.1:8010 --scrub "$PWD" \\
        --source "app @ $(git rev-parse --short HEAD)" --landing /inbox/ \\
        --banner "Demo: an example restaurant with made-up customers. Buttons are switched off." \\
        --contact-email you@yourdomain.com.au

Crawls every same-origin GET page reachable from the app's screens, rewrites links
to static paths, switches every form off, adds a "read-only snapshot" banner and
noindex markers, and refuses to write anything that still contains a local
filesystem path. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import html
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

SEEDS = ["/", "/goals", "/triggers", "/inbox", "/sending", "/sent", "/data", "/security", "/login"]
# Not pages: state-changing, OAuth hand-offs, health checks, file downloads.
SKIP_PREFIXES = ("/logout", "/oauth/", "/healthz", "/welcome", "/export", "/download", "/reset")
SKIP_SUFFIXES = (".csv", ".zip", ".pdf", ".json", ".txt", ".log")
ROBOTS_MARKER = "# regulars-snapshot"
SCRUBBED = "/srv/regulars"
LEAK_RE = re.compile(r"(/tmp/|/home/|/root/|claude-0|scratchpad)")

# Operator-only notices that mean nothing (or look unfinished) in a public demo,
# e.g. "No operator password is set yet". Matched on the app's own markup; if the
# app changes, the notice simply reappears, which is harmless.
STRIP_RES = [
    re.compile(r'<div class="warn"><b>Security\.</b>.*?</div>', re.S),                       # Setup screen
    re.compile(r'<div class="warn"><b>No operator password is set\.</b>.*?</div>', re.S),   # Security screen
]
DEFAULT_BANNER = "Read-only snapshot of the demo venue (synthetic data) \u00b7 buttons and forms are switched off"
TRY_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?(/[^\s\"'<>]*)?$")   # https only: it becomes a link
CONTACT_RE = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")

A_TAG_RE = re.compile(r"<a\b[^>]*>", re.I | re.S)
HREF_RE = re.compile(r"""\bhref=(["'])(.*?)\1""", re.S)
FORM_RE = re.compile(r"<form\b[^>]*>", re.I | re.S)
BODY_RE = re.compile(r"<body\b[^>]*>", re.I | re.S)
EMAIL_RE = re.compile(r"(?<![\w.%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# Domains that are already placeholders and can stay as they are.
SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", ".example", ".invalid", "yourvenue.com.au")


# --- URL mapping ---------------------------------------------------------------

def resolve(base_url: str, current: str, href: str):
    """Absolute (path, query, fragment) if href is a same-origin link, else None."""
    full = urllib.parse.urljoin(base_url + current, html.unescape(href).strip())
    u, b = urllib.parse.urlsplit(full), urllib.parse.urlsplit(base_url)
    if u.scheme not in ("http", "https") or u.netloc != b.netloc:
        return None
    return (u.path or "/", u.query, u.fragment)


def is_page(path: str) -> bool:
    return not (path.startswith(SKIP_PREFIXES) or path.lower().endswith(SKIP_SUFFIXES))


def local_url(path: str, query: str = "") -> str:
    """'/m', 'id=12' -> '/m/id-12/'. Deterministic, directory-style, Netlify-friendly."""
    segs = [s for s in path.split("/") if s]
    if query:
        pairs = sorted(urllib.parse.parse_qsl(query, keep_blank_values=True))
        segs.append("-".join(re.sub(r"[^A-Za-z0-9._]+", "_", f"{k}-{v}") for k, v in pairs))
    return "/" + "/".join(segs) + ("/" if segs else "")


def out_file(out: Path, url: str) -> Path:
    return out / url.strip("/") / "index.html" if url != "/" else out / "index.html"


# --- HTML rewriting --------------------------------------------------------------

def mask_emails(text: str) -> str:
    """name@icloud.com -> name@icloud.example.

    The demo's customers are made up, but they sit on real providers, so an
    address could coincide with a real person's. A public preview shouldn't
    publish those. Applies to visible text and mailto: links alike.
    """
    def one(m):
        local, domain = m.group(1), m.group(2).lower()
        if domain == "yourvenue.com.au" or domain.endswith(SAFE_EMAIL_DOMAINS):
            return m.group(0)
        return f"{local}@{domain.split('.')[0]}.example"

    return EMAIL_RE.sub(one, text)


def extract_links(page: str) -> list[str]:
    return [html.unescape(m.group(2)) for tag in A_TAG_RE.findall(page) for m in [HREF_RE.search(tag)] if m]


def _rewrite_a(tag: str, base_url: str, current: str, known: set, aliases: dict) -> str:
    m = HREF_RE.search(tag)
    if not m:
        return tag
    raw = html.unescape(m.group(2)).strip()
    if raw.startswith(("#", "mailto:", "tel:")):
        return tag
    r = resolve(base_url, current, raw)
    if r is None:  # external site: leave alone
        return tag
    path, query, frag = r
    if (path, query) in known:
        new = (aliases.get((path, query)) or local_url(path, query)) + (f"#{frag}" if frag else "")
        return tag[: m.start()] + f'href="{html.escape(new)}"' + tag[m.end():]
    dead = tag[: m.start()] + 'href="#" data-snap-dead="1" title="Not available in the read-only snapshot"' + tag[m.end():]
    return dead


def _neuter_form(tag: str) -> str:
    tag = re.sub(r"""\s(?:action|enctype|method)=(["']).*?\1""", "", tag, flags=re.I | re.S)
    return tag[:-1].rstrip() + ' action="#" method="get" data-snapshot="1">'


BANNER_CSS = """<style>
.snap-banner{position:sticky;top:0;z-index:99999;background:#1f2937;color:#f9fafb;font:13px/1.4 system-ui,sans-serif;
padding:8px 14px;text-align:center}.snap-banner a{color:#fbbf24}.snap-toast a{color:#fbbf24}
[data-snap-dead]{cursor:not-allowed;opacity:.55}
.snap-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#111827;color:#fff;
padding:10px 16px;border-radius:8px;font:14px system-ui,sans-serif;z-index:99999;display:none;box-shadow:0 4px 16px #0006}
</style>"""

def snap_js(try_url=None):
    """The 'this button is switched off' toast and the script that shows it."""
    if try_url:
        msg = (f'This is the read-only preview. <a href="{html.escape(try_url)}">Open the interactive '
               "demo</a> to use the buttons.")
        ms = 7000
    else:
        msg, ms = "Read-only snapshot: this button doesn't do anything here.", 2600
    return (f'<div class="snap-toast" id="snap-toast">{msg}</div>\n'
            "<script>\n"
            "(function(){var t=document.getElementById('snap-toast'),h;\n"
            "function toast(){t.style.display='block';clearTimeout(h);"
            f"h=setTimeout(function(){{t.style.display='none'}},{ms})}}\n"
            "document.addEventListener('submit',function(e){e.preventDefault();toast()},true);\n"
            "document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('[data-snap-dead]');\n"
            "if(a){e.preventDefault();toast()}},true)})();\n"
            "</script>")


def banner(text: str, source: str, built: str, contact: str | None = None,
           try_url: str | None = None) -> str:
    cta = ""
    if try_url:
        cta += f' &middot; <a href="{html.escape(try_url)}">Try it with your own Square export &rarr;</a>'
    if contact:
        e = html.escape(contact)
        cta += f' &middot; Want this on your own data? <a href="mailto:{e}">{e}</a>'
    tip = html.escape(f"Snapshot of {source}, built {built}")
    return f'<div class="snap-banner" title="{tip}">{html.escape(text)}{cta}</div>'


def rewrite_page(page: str, base_url: str, current: str, known: set, source: str, built: str,
                 scrub: list[str], *, aliases: dict | None = None, banner_text: str = DEFAULT_BANNER,
                 contact: str | None = None, try_url: str | None = None) -> str:
    aliases = aliases or {}
    for rx in STRIP_RES:
        page = rx.sub("", page)
    page = A_TAG_RE.sub(lambda m: _rewrite_a(m.group(0), base_url, current, known, aliases), page)
    page = FORM_RE.sub(lambda m: _neuter_form(m.group(0)), page)
    for prefix in scrub:
        page = page.replace(prefix, SCRUBBED)
    # Mask first, inject after: the contact address in the banner is the owner's own
    # and must not be rewritten like a demo customer's.
    page = mask_emails(page)
    head_add = '<meta name="robots" content="noindex,nofollow">' + BANNER_CSS
    page = re.sub(r"</head>", lambda m: head_add + "</head>", page, count=1, flags=re.I) \
        if re.search(r"</head>", page, re.I) else head_add + page
    page = BODY_RE.sub(lambda m: m.group(0) + banner(banner_text, source, built, contact, try_url), page, count=1)
    js = snap_js(try_url)
    page = re.sub(r"</body>", lambda m: js + "</body>", page, count=1, flags=re.I) \
        if re.search(r"</body>", page, re.I) else page + js
    return page


# --- Crawl + write ------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # surface 3xx instead of following it
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url: str):
    try:
        with _OPENER.open(url, timeout=20) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), b""


def crawl(base_url: str, max_pages: int):
    pages: dict[tuple[str, str], str] = {}
    skipped: dict[str, str] = {}
    queue = deque((p, "") for p in SEEDS)
    seen = set(queue)
    while queue and len(pages) < max_pages:
        path, query = queue.popleft()
        status, ctype, body = fetch(base_url + path + ("?" + query if query else ""))
        if status != 200 or "text/html" not in ctype:
            skipped[path + ("?" + query if query else "")] = f"HTTP {status}" if status != 200 else ctype
            continue
        text = body.decode("utf-8", errors="replace")
        pages[(path, query)] = text
        for href in extract_links(text):
            r = resolve(base_url, path, href)
            if r and is_page(r[0]) and (r[0], r[1]) not in seen:
                seen.add((r[0], r[1]))
                queue.append((r[0], r[1]))
    leftover = len(queue)
    return pages, skipped, leftover


def prepare_out(out: Path) -> None:
    robots = out / "robots.txt"
    if out.exists() and any(out.iterdir()):
        if not (robots.exists() and robots.read_text().startswith(ROBOTS_MARKER)):
            sys.exit(f"refusing to overwrite {out}: not a previous snapshot (no robots.txt marker)")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)


REDIRECT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Regulars demo</title><meta http-equiv="refresh" content="0; url={url}"><link rel="canonical" href="{url}"></head>
<body><p><a href="{url}">Open the demo</a></p></body></html>
"""


def build(base_url: str, out: Path, source: str, built: str, scrub: list[str], max_pages: int, *,
          landing: str | None = None, banner_text: str = DEFAULT_BANNER, contact: str | None = None,
          try_url: str | None = None):
    """landing: local URL (e.g. '/inbox/') the site root should open on. The app's own
    home screen then moves to /setup/ so the "Setup" tab still works."""
    base_url = base_url.rstrip("/")
    pages, skipped, leftover = crawl(base_url, max_pages)
    if ("/", "") not in pages:
        sys.exit(f"{base_url}/ did not return a page; is the app running?")
    known = set(pages)
    aliases = {("/", ""): "/setup/"} if landing else {}

    rendered, taken = {}, {}
    for (path, query), text in pages.items():
        url = aliases.get((path, query)) or local_url(path, query)
        if url in taken and taken[url] != (path, query):
            sys.exit(f"two pages map to {url}: {taken[url]} and {(path, query)}")
        taken[url] = (path, query)
        page = rewrite_page(text, base_url, path, known, source, built, scrub,
                            aliases=aliases, banner_text=banner_text, contact=contact,
                            try_url=try_url)
        if LEAK_RE.search(page):
            bad = LEAK_RE.search(page)
            sys.exit(f"{path}: output still contains a local path near {page[max(0, bad.start()-40):bad.end()+40]!r}; pass --scrub")
        rendered[url] = page
    if landing:
        if landing not in rendered:
            sys.exit(f"--landing {landing} is not a page in the snapshot (have e.g. {sorted(rendered)[:4]})")
        rendered["/"] = REDIRECT_PAGE.format(url=html.escape(landing))

    prepare_out(out)
    for url, page in rendered.items():
        f = out_file(out, url)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(page, encoding="utf-8")
    (out / "robots.txt").write_text(f"{ROBOTS_MARKER}\nUser-agent: *\nDisallow: /\n")
    return len(rendered), skipped, leftover


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url", nargs="?", default="http://127.0.0.1:8010")
    ap.add_argument("--out", default="site")
    ap.add_argument("--source", default="demo app", help="label shown in the banner, e.g. 'PR #1 @ 985e54f'")
    ap.add_argument("--built", default=datetime.date.today().isoformat())
    ap.add_argument("--scrub", action="append", default=[], help="local path prefix to replace (repeatable)")
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--landing", help="open the site on this page, e.g. /inbox/ (app home moves to /setup/)")
    ap.add_argument("--banner", default=DEFAULT_BANNER, help="text of the banner on every page")
    ap.add_argument("--try-url", help="https address of the interactive demo; adds 'Try it with your "
                                      "own Square export' to the banner and the switched-off message")
    ap.add_argument("--contact-email", help="adds 'Want this on your own data? <email>' to the banner")
    a = ap.parse_args(argv)
    if a.contact_email and not CONTACT_RE.match(a.contact_email):
        ap.error(f"--contact-email {a.contact_email!r} doesn't look like an email address")
    if a.try_url and not TRY_URL_RE.match(a.try_url):
        ap.error(f"--try-url {a.try_url!r} must be an https:// address")
    n, skipped, leftover = build(a.base_url, Path(a.out), a.source, a.built, a.scrub, a.max_pages,
                                 landing=a.landing, banner_text=a.banner, contact=a.contact_email,
                                 try_url=a.try_url)
    print(f"wrote {n} pages to {a.out}/")
    for u, why in sorted(skipped.items()):
        print(f"  skipped {u}: {why}")
    if leftover:
        print(f"  WARNING: stopped at --max-pages with {leftover} pages still queued")
    return 0


if __name__ == "__main__":
    sys.exit(main())
