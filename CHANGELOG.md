# Changelog

Versioning: the state file carries a `schema` number; the server migrates older
state on boot and writes a `config_change` audit entry when it does. Bump
`VERSION` and `SCHEMA` in `app/server.py` for every release.

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
