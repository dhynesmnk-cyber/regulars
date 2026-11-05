# Changelog

Versioning: the state file carries a `schema` number; the server migrates older
state on boot and writes a `config_change` audit entry when it does. Bump
`VERSION` and `SCHEMA` in `app/server.py` for every release.

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
