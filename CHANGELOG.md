# Changelog

Versioning: the state file carries a `schema` number; the server migrates older
state on boot and writes a `config_change` audit entry when it does. Bump
`VERSION` and `SCHEMA` in `app/server.py` for every release.

## Unreleased - Gate 2.8: a sales demo instance

A provisioned instance always behaved like a venue, so a hosted demo would have
hidden the example restaurant, stripped its goals and demanded Goals first.

- **`REGULARS_DEMO=1`**: an instance with a setup token that is a demo, not a
  venue. It is still shut until you open the one-time link and set a password
  (that password is the only gate), then shows the example restaurant, its goals
  and the connect-or-upload Setup screen, and never asks for Goals first.
- **Uploads are off on a demo**: the form is replaced by a sentence and
  `POST /upload` is refused, because uploads sit in memory shared by everyone
  signed in and a real customer file would show to the next visitor.
- **`regularsctl provision <slug> --demo`**: implies billing off, writes
  `REGULARS_DEMO=1` to the env file (kept by `link`, `upgrade`, `restore`),
  tells you to set your own password rather than hand the link to an owner, and
  `status` shows `demo, no billing`.
- Not done: a public, no-password demo (it would need a read-only mode), and a
  real Docker/Caddy/TLS run, which is the Gate 2 exit drill.

## Unreleased - Gate 2.7: the venue's market, and Terms and Privacy links

Everything the owner reads now speaks their market; one place to link your legal pages.

- **A$ / US$.** Figures on the owner's screens (offer costs, lifetime spend,
  evidence lines) carry the market's currency symbol instead of a bare `$`.
  Customer emails and the owner's own offer wording ("$15 off your bill") are
  untouched.
- **`<html lang>`** is `en-AU` or `en-US` by market (it was always `en-AU`).
  Pages shown before sign-in read as Australian until a market is chosen.
- **Terms and Privacy links** in the foot of every page, including the
  set-a-password screen, from `REGULARS_TERMS_URL` / `REGULARS_PRIVACY_URL` in
  `shared.env`. Nothing shows until you set them; an address that isn't a plain
  `https://` URL is ignored.
- Not changed: the importer still strips only `$` from amounts; an `A$12.50`
  value would not parse. Square's real export format is unverified.

## Unreleased - Gate 2.6: guided onboarding, and real Square exports

A stranger who claimed an instance landed on a Setup screen written for the
demo, and the file they'd bring from Square wouldn't have imported.

- **Square Dashboard exports import.** The Items Detail CSV was refused
  ("Simple format needs columns") and, even when forced through, every order
  landed at midnight and a blank Reference ID collapsed the customer export
  into one person. Now: separate Date and Time columns, Square Customer ID or
  name linking (shared names left unlinked, not guessed), Surname, Email
  Subscription Status as consent with unsubscribes permanent, day/month order
  from the file then the market, and a clear message when the customer export
  is put in the first box. The API-style names still work. Sample downloads
  use the Dashboard layout. The column names come from public posts; check a
  real header row before the first venue.
- **Fixed: customer IDs used as email addresses.** A customer without an email
  got their Square ID as one, so a transactions-only upload with consent
  attested exported rows addressed to "cust_00000". Now they're "no email" and
  the report says to upload the customer export. Two Square profiles with one
  email are still one customer.
- **Setup is a checklist on a venue's instance** (one with a setup token):
  business, upload, goals and offers, read, sender, plan, first export, each
  ticked from the saved state, the next one first. How to export from Square
  comes from the pack (`EXPORT_STEPS`, `WORDS`).
- **The example restaurant no longer acts on a venue's customers.** Venues
  start without its quiet nights and products; **goals and offers are a
  required step** (the starting offers, such as house wine and $15 off, are a
  suggestion), so approving and exporting wait until Goals has been saved
  once. The demo "Connect Square" button and the example's name in the header
  are gone; screens showing example data say so.
- **Uploads stay in memory only.** After a restart the checklist asks for the
  latest export and says why. New persisted `milestones`: goals saved, first
  upload, first export.
- **Footer**: the "working prototype" text is replaced by the version, a
  support address (`REGULARS_SUPPORT_EMAIL`) and a note on example data.
- Import is faster: 20,475 profiles in 21 s, down from 31 s on the same
  machine (dictionary lookups instead of list scans).
- Local runs without a setup token are unchanged: all 20 demo pages, before
  and after an upload, are identical apart from the footer and a CSS fix that
  also straightens the Data screen's "!" icons.
- 24 new tests; two changed (one encoded the customer-ID bug, one checked the
  old welcome wording).

## Unreleased - Gate 2.5: upload limit

The 8 MB cap turned away any venue with more than about 1,500 customers'
full history. Raising it naively would have made things worse: every POST
body, on every route, was read into memory before anyone was checked.

- **CSV uploads: 50 MB by default** (`REGULARS_MAX_UPLOAD_MB`, 1-500). Every
  other form: 1 MB. Both decided from the headers, before reading.
- **Uploads only from a signed-in owner**: on a password-gated or unclaimed
  instance, an upload without a valid session is refused before its body
  is read (its CSRF token, inside the body, is still checked after).
- **A negative Content-Length is refused** (400). It used to make the
  server wait until the client hung up.
- **Refusals are delivered reliably**: the server answers, stops writing,
  and discards the rest of the upload for up to two seconds before closing.
  Measured with curl sending 297 MB: the old server reset 1 of 5
  connections instead of answering; the new one answered 5 of 5. (Chromium
  showed the page either way.)
- The upload form says the limit and what to do about a bigger export; the
  Security screen shows both caps.
- Measured: a 43 MB export (8,367 customers) imports in 17 s and peaks at
  823 MB of RAM - see Performance in the README.
- 5 new tests (each hangs or times out against the old code).

## Unreleased - Gate 2.4: billing v0 (free preview, pay to send)

Nothing collected the A$40/month. Now, with billing on:

- A venue can sign in, upload and read every message free. **Approving or
  exporting real customers' messages needs a paid-up period**; the demo data
  can always be rehearsed. Sign-in, upload, settings and reading never lock.
- Setup gains **Your plan**: status in plain words, the price for the
  venue's market (A$40 a month + GST / US$40 a month), and a **Pay** or
  **Renew** button opening your Stripe Payment Link with
  `client_reference_id=<venue>`.
- Seven days before the paid-up date every screen shows a renewal reminder;
  seven days after it an overdue banner while sending still works; then
  approving and exporting pause until the next payment.
- `regularsctl paid <venue> --months 1 | --until DATE` records a payment
  (extending from the later of today and the current date). It takes effect
  without a restart: the date lives in `/data/regulars-billing.json`, which
  only the operator writes and the app re-reads when it changes. The app
  writes each new date to the audit log itself, once.
- `regularsctl provision` turns billing on (`--no-billing` for demos) and
  names the venue; `status` shows each venue's plan.
- Approve and export now share one rule, `state.send_block()` (business
  details, then billing).
- Opt-in via `REGULARS_BILLING=on`: local runs and existing tests unchanged.
- 16 new tests.

## Unreleased - Gate 2.3: provisioning and ops

Handing a venue its own instance used to take about a dozen manual steps
(keys, token, port, volume, container, Caddy site, reload, health check,
link), and upgrades, backups and restores weren't scripted at all.

- **`deploy/regularsctl.py`** (stdlib Python, runs as root on the server):
  `init`, `provision <slug>` (container, volume, loopback port, Caddy site,
  health wait, then prints the owner's link, the uptime URL and the master
  key; undoes itself if the venue never comes up), `status [--quiet]`
  (cron-friendly alerting), `upgrade` (rebuild, recreate, roll the first
  venue back and stop if it isn't healthy), `backup` (settings and audit log
  only, 0600, pruned), `restore --yes` (safety copy first, audit chain
  verified after), `link [--reset-owner]`. All take `--dry-run`.
- Secrets live in `/etc/regulars/venues/<slug>.env` (0600), outside the data
  volumes, so backups never hold the master key.
- **`app/admin.py`**: `reset-owner` (audited `owner_reset`) and
  `verify-audit`, run inside a venue's container.
- DEPLOYMENT.md: one-time server setup, cron lines, day-to-day commands;
  the hand-edit-the-state-file password reset is replaced by
  `link --reset-owner`.
- 22 new tests against a fake Docker; plus a full drill on real Docker with
  Caddy over HTTPS (25 checks: claim, backup, restore, upgrade, forced
  rollback, owner reset, down alert).

## Unreleased - Gate 2.2: network hardening

**Fixes two security holes.** The app believed the first entry of any
`X-Forwarded-For` header, which the visitor controls:

- **Unlimited password guessing**: a different made-up address on each
  request meant the five-strikes lockout never triggered (demonstrated:
  eight wrong guesses then a correct one signed straight in).
- **Remote takeover of a fresh instance**: with no password set, a visitor
  could claim `X-Forwarded-For: 127.0.0.1`, pass the "first password only from
  the server itself" check and set the operator password - whenever the
  port was exposed directly or the proxy appended to the header (as nginx's
  common config does).

Changes:
- `security.client_ip()`: the header is ignored unless `REGULARS_TRUST_PROXY`
  (number of proxies in front) is set; then only the entry the proxy
  appended counts (N-th from the right), only on connections from a loopback
  or private address, and unparseable values fall back to the connection.
- `REGULARS_BIND` (default `127.0.0.1`): a fresh install no longer listens
  on every interface. The Docker image sets `0.0.0.0` plus
  `REGULARS_TRUST_PROXY=1`, matching the documented Caddy setup.
- Start-up prints a `network:` line when the bind and proxy settings look
  wrong together; the Security checklist shows whether a proxy is trusted.
- 11 new tests, including the lockout-dodge attack (fails on the old code).

## Unreleased - Gate 2.1: owner setup link

A provisioned instance used to start in open demo mode: until someone set a
password, every screen was reachable by whoever found the address first, and
the first password could only be set from the server itself (or by opening
that up to the network with `REGULARS_OPEN_FIRST_RUN=1`).

- **`REGULARS_SETUP_TOKEN`**: provisioning sets it and sends the owner
  `/welcome?token=...`. The owner chooses a password there and is signed in.
- **Closed until claimed**: while the token is set and no password exists,
  every screen and form answers "this instance hasn't been set up yet", except
  the link and `/healthz`. That includes the localhost first-password path.
- **One use**: setting the password burns the token (its sha256 is kept in
  state), so the link never works again, even if the variable stays set.
- Wrong tokens are audited (`setup_fail`) and count towards the sign-in
  lockout. Tokens are compared in constant time, masked in the server log,
  and never written to the audit log. Tokens under 24 characters stop
  start-up with instructions to generate one.
- Pre-sign-in screens use a bare header: no business name, no menu.
- Security checklist shows whether the owner has claimed the instance.
- 7 new tests.

## Unreleased - Gate 2.0: business identity

**Fixes a first-sale blocker.** Every message used to be signed "Marlow &
Finch" with the demo ABN `12 345 678 901` in its footer, and every page
header named the demo venue, even after a real venue uploaded its own CSV.
Nothing in the app let an owner enter their own details.

- **Your business** card on Setup (replaces the market-only card): business
  name, suburb or city, market, ABN (Australia) or physical postal address
  (US). Saved all-or-nothing via `POST /business`, audited, kept by Start over.
- **ABN check digit**: the ABR's published checksum rejects typos, so a wrong
  ABN can't be printed in every footer. Stored as `NN NNN NNN NNN`.
- **Who signs**: the owner's saved details, everywhere (sign-off, tone
  sign-offs, footer, page header, exports, the send checklist). The demo data
  is still signed by the demo venue until details are saved. Real data with
  no details shows `[Your business name]` / `[ABN]` placeholders, and
  approving and exporting are refused with a pointer to Setup.
- Security checklist gains a "business identity set" line.
- State schema 2 -> 3 (`business` key). Older state files load with empty
  details; a 0.6.0 build ignores the new key, so rolling back is safe.
- 10 new tests, including one that loads every screen on real data, before
  and after saving details, and fails if the demo name appears anywhere.

## Unreleased - Gate 1: safe to change

No change to what the app shows or sends: the old and new versions return
identical responses across 46 GET/POST steps, and identical engine output
(messages, evidence, profiles, suppressions) across 40 goal, tone, region
and threshold variants.

- **CI**: the full test suite runs on every push and pull request (Python
  3.11 and 3.12).
- **server.py split** into `state.py` (settings, audit, guard, secrets),
  `pages/` (one module per screen with its form actions), `web.py`
  (request/response values), `views.py`, `exports.py` and `upload.py`.
  `server.py` is now the HTTP layer only: checks, then a route table.
- **Concurrency fix**: a lock now serialises changes to the saved settings.
  Before, two form posts at once could race on `regulars-state.json.tmp`,
  failing one request and risking a half-written state file. The listen
  queue is raised from 5 to 64, which stops connection resets under bursts.
- **Industry packs**: everything restaurant-specific (profile, triggers,
  thresholds, offer categories, email templates, demo venue) moved to
  `packs/restaurant.py` behind a documented interface (`packs/__init__.py`).
  `REGULARS_INDUSTRY` picks the pack; `restaurant` is the default and the
  only one so far.
- **Benchmark**: `tools/bench.py` times the engine, the Setup and Messages
  screens and a full Square-format import at 2k-50k customers, with peak
  memory. `data.build(scale=N)` multiplies the demo venue for it. Results
  are in the README under Performance.

## 0.6.0 - 2026-11-05 - specificity & tone

**Modifier-level personalisation (Square modifiers end to end)**
- Imports read Square's modifier data two ways: a dedicated modifiers column
  ("Item modifier names", ";"-separated) and parentheticals embedded in item
  names ("Flat White (Oat Milk, Extra Shot)") — receipts and exports vary.
  The simple CSV format carries modifiers as parentheticals; both sample
  files demonstrate it.
- The engine builds per-customer modifier *habits* (a choice on 55%+ of their
  orders of that item, at least twice — a one-off trial is not a habit) and
  the copy names the choice, never the count: "since your last flat white
  (almond milk)". Counts stay owner-side in the evidence panel ("Almond Milk
  on 83% of their orders of it").
- Imported menus now carry per-item modifier frequencies (`common_mods`).
- **Offer sense-guard:** if a paired offer contradicts the person's own habit
  (offering "an oat milk upgrade" to someone whose usual already is oat
  milk), the engine swaps to the next sensible offer and tells the merchant
  why in the message's Why panel.

**Tone control**
- New Goals-screen setting: Warm & neighbourly (default), Short & punchy,
  Polished & premium, Playful & cheeky. Tone operates at the seams —
  greeting, sign-off, connective phrases — so evidence rules, vague-timing
  discipline, offer matching and the legal footer are identical in every
  voice. Persisted in goals, audited on change, per-message hand-editing
  still available before approval.

## 0.5.0 - 2026-11-05 - commercial hardening

**Security**
- Operator password gate: PBKDF2-SHA256 (210k iterations) hashing, 12-hour
  HttpOnly/SameSite=Lax sessions, per-session CSRF tokens injected into every
  form, five failed logins -> 15-minute per-IP lockout.
- Per-IP rate limits (120 POST/min, 600 GET/min) and an 8 MB upload cap;
  violations return 429/413 and are audited.
- Hash-chained, append-only audit log (`regulars-audit.log`): logins, config
  changes, approvals, exports, uploads, resets, OAuth events, and every
  attack-shaped request. One-click chain-integrity verification on the new
  **Security** screen; editing or deleting a line breaks verification.
- Security headers on every response: CSP, X-Content-Type-Options,
  X-Frame-Options DENY, Referrer-Policy, Permissions-Policy, no-store;
  HSTS when `REGULARS_TLS=1`.
- Secrets at rest: OAuth tokens encrypted (HMAC-CTR, encrypt-then-MAC) with a
  key from `REGULARS_MASTER_KEY` or an auto-generated 0600 keyfile. SMTP
  password encryption follows when real sending ships.
- Error pages no longer show tracebacks once a password is set.
- "Start over" is now a POST (was a GET link - CSRF-able and CSP-incompatible).

**Square OAuth**
- Real authorization-code flow (`app/oauth.py`): CSRF `state`, 15-minute
  expiry, token exchange, refresh, revoke; read-only scopes (merchant profile,
  orders, customers, catalog, loyalty). Credentials come from the environment
  only. Callback state mismatch is audited as `oauth_fail`.
- Sending screen shows live connection status; revoke deletes stored tokens
  and revokes at Square.

**US market (region support)**
- Market switch AU/US on the Setup screen. US footers carry the CAN-SPAM
  physical postal address (required, validated) and the commercial-email
  opt-out line; AU footers keep the Spam Act identity + ABN form.
- Season references follow the hemisphere; customer copy respells to US
  English (organizing, favorite, ...). Numbers still never reach customers.

**Deployment**
- `Dockerfile` (python:3.12-slim, non-root, /data volume for state+audit+key,
  healthcheck), `DEPLOYMENT.md`, this changelog, `/healthz` now returns JSON
  with version/schema/region/auth status, state schema migrations on boot.

## 0.3.0 - 2026-11-04 - v2 product shape

- BeeSearch-style flow: few questions in -> one message per person out.
- CSV upload (Square-format and simple format, tolerant column matching,
  import report, consent attestation gate), editable trigger thresholds with
  live counts, offer catalogue in categories with per-trigger pairing,
  customer copy that never quotes the customer's own numbers, sender setup
  with per-ESP unsubscribe merge tags, SMTP details stored locally (never
  transmitted), editorial UI.

## 0.1.0 - 2026-11-03

- First prototype: demo venue dataset, trigger engine, suppression rules,
  Spam Act-compliant footers.
