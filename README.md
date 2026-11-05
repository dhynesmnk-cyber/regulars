# Regulars

**Version 0.6.0.** A Square wrapper for Australian and US restaurants. It reads your sales history, works out
what each customer actually does, and writes them **one** message that fits — the same
again, bring a friend, come back, try this, add a side, book a group.

Not a blast. Not a blanket discount. One message per person, derived from their own
orders. And the customer-facing copy never quotes their behaviour back at them: the
numbers are yours, on the message page, not theirs.

```
cd app
python3 server.py 8010          # http://localhost:8010

cd ..
python3 -m unittest discover -s tests -t .    # 124 tests
```

Python 3.11 standard library only. No framework, no CDN, no build step, no internet.
On macOS you can double-click `start.command` instead.

---

## The screens

| Screen | What it does |
|---|---|
| **Setup** | Connect Square (real OAuth when the server has credentials) **or upload CSVs**; market switch (AU/US); where you are in onboarding; who we deliberately did *not* contact |
| **Goals** | Three questions — times you want busier, products you want to sell, what you're optimising for — plus the **message tone** (warm / punchy / polished / playful), the offer catalogue, and which offer each rule uses |
| **Triggers** | All ten rules in priority order with live people-counts, a switch each, and **every threshold editable** with a reset |
| **Messages** | The product. Every message, expandable: the email, the evidence numbers behind it, and why that person got that offer |
| **Sending** | Pick your sender (Mailchimp / Klaviyo / Brevo / MailerLite / Square Marketing / your own SMTP), links if you have none, SMTP stored locally with a connection test |
| **Sent** | Append-only ledger: who approved what, on what consent basis, when. Plus how measurement works |
| **Data** | What Square gives you, what it doesn't, and the email-capture problem worth fixing first |
| **Security** | Password/session state, rate-limit lockouts and flags, credential status, deployment checklist, and the hash-chained audit trail with one-click integrity verification |

The demo venue is synthetic: **Marlow & Finch**, Fitzroy. 525 customers, 8,124 orders,
$704k lifetime, deterministic (`SEED = 40`, "now" = 2026-11-04). Default goals produce
**199 messages, 156 distinct subject lines**, about $1,649 of offer cost if every single
recipient redeemed (in practice roughly a fifth do).

---

## Two ways in

**Connect Square** — read-only Orders, Customers, Catalogue. In production this is
Square's OAuth screen; here it's a button that loads the demo venue.

**Upload CSVs** — for venues that don't want anything connected. Two shapes accepted:

- **Square's own dashboard exports**: a transactions / item-sales CSV (one row per line
  item) plus, optionally, the customer directory. Headers are matched tolerantly
  (`Created at`, `Item variation name`, `Sold count`, `Covers`, `Gross money amount`…),
  because Square has renamed columns over the years.
- **A simple documented format**, one row per visit:
  `date, time, email, name, items, party_size, channel` where items is
  `2x Flat White; 1x Garlic Bread`.

Either way you get an **import report**: rows read, customers folded, menu inferred
(with prices from the file where present), which fields were found and which weren't —
and therefore which triggers are asleep for that dataset. Missing covers means the
group triggers sleep; that's honest rather than guessed.

**Consent is asked about explicitly at upload.** Exports don't carry marketing consent,
and a card payment is not consent under the Spam Act. If the venue ticks "these people
opted in at my POS / website / loyalty", contacts are marked consented with the source
recorded as *merchant attested at import*. If nobody has consent, everything is
quarantined and the report says so.

Sample files for both formats are linked on the setup screen
(`/sample/simple.csv`, `/sample/square-transactions.csv`, `/sample/square-customers.csv`).
The simple sample is a 700-visit slice across ~88 customers — their most recent visits,
spread across the directory, the way a real date-range export looks.

Imports live **in memory only**. Restarting drops them; configuration does not.

---

## The core mechanism

Every customer is profiled from their orders — signature item and how often they buy
it, their own median gap between visits, recency, table size, whether they add sides,
daypart, weekday, menu variety, alcohol share, spend percentile, birthday, and whether
their cadence is decaying.

Then ten triggers are tested **in order, first match wins**:

```
birthday → their usual, overdue → big spender gone quiet → the one who organises
→ big table potential → new, needs a second visit → come at a different time
→ never orders extras → creature of habit → slowing down
```

Everyone gets exactly one message — the most specific thing that's true about them.
If nothing matches they get nothing, and that's counted on screen rather than hidden.

Three design rules worth naming:

1. **Specificity beats the priority setting.** Your goal is worth +8 weight points, not
   an override. Otherwise "their visits are drifting" leapfrogs "you always book the
   table for six" because you picked retention that week.
2. **Upsells only go to people currently coming in.** Pitching a free side to someone
   absent four months is tone-deaf and wasted. They get a win-back.
3. **Nothing sends automatically.** A named human approves each message or batch, and
   the approval is written to the ledger.

### The copy rule

Customer-facing emails may name their usual and use vague time — "a few weeks",
"back in winter". They may never contain a gap, a ratio, a count or a percentile, and
never "we noticed". "You usually come in every 6 days" is not warmth, it's
surveillance, and it reads that way. Every figure stays in the evidence panel on the
message page, which is labelled *yours to see, never theirs*. The tests enforce this
with a regex over every generated body and subject.

The copy *may* name their choices: Square modifiers (oat milk, no onion, extra
shot) are read from your export, and a choice they make on 55%+ of their orders
of an item becomes part of the message — "since your last flat white (almond
milk)". The habit is stated, never counted; the percentages stay in the
evidence panel. And an offer is never sent if it contradicts the person —
nobody who always orders oat milk gets offered "an oat milk upgrade"; the
engine swaps the offer and says why.

### The offer catalogue

Offers live in four categories — **upgrade or perk, something free, money off, group &
booking** — because an oat-milk upgrade is the right nudge for a daily coffee regular
and nonsense for a Friday-night group. The merchant edits the catalogue (add, rename,
re-cost, drop), and each trigger has a dropdown defaulted to a sensible offer. Money-off
offers exist, but the only trigger defaulted to one for a lapsed customer is the
win-back; that's where a credit is earned.

### Tuning

Every threshold the triggers use — 23 of them, from the overdue multiplier to the
birthday window to the drift ratio — is an editable number on the Triggers screen, with
the live people-count for each rule and a reset to defaults. Changes persist.

---

## Sending

The Sending screen records where messages will actually go out from, and adapts the
export to it:

| Sender | Unsubscribe merge tag we write |
|---|---|
| Mailchimp | `*|UNSUB|*` |
| Klaviyo | `{% unsubscribe %}` |
| Brevo / SendGrid | `{{{unsubscribe}}}` |
| MailerLite | `{$unsubscribe}` |
| Square Marketing | Square appends its own; we leave a placeholder |
| Your own SMTP / other | `{{unsubscribe_url}}` |

Venues with nothing yet get the four signup links and a one-line steer on each. If they
send through their own host, SMTP details (host, port, user, password, from, reply-to)
are stored in the local config file and a connection test can be run. **The prototype
never transmits.** The wrong merge tag means a broken unsubscribe, which is a Spam Act
problem, not a cosmetic one — so the tag is chosen for you, not typed.

---

## What persists

Goals, the catalogue and trigger pairings, enabled rules, tuned thresholds, the sending
setup, the approval ledger and the frequency-cap history are written to
`regulars-state.json` beside the app, so a restart doesn't wipe an afternoon of setup.
Uploaded customer data is deliberately excluded. Delete the file to start clean, or use
**Start over** on the setup screen.

---

## Pricing: $40/month

Entry price is **$40/month, per location, no setup fee, monthly, cancel anytime.**

- **Inside the impulse band.** Square Free is $0, Square Plus $129/month. $40 is a third
  of a subscription they've already committed to, and less than one table of four. It
  doesn't need a budget line or a partner conversation. $89 does.
- **Matches month-one work.** Ten rules over one venue is not a $200 product. Charge
  that and you spend your first ten customers arguing about price instead of learning.
- **Defensible against "Square Marketing does this".** Square's bundled tool sends a
  segment a blast. The difference here is that every message derives from that
  individual's orders and no two are alike. At $40 you don't have to win that argument
  hard — you just have to be cheap enough that trying it beats arguing.
- **Unit economics still work.** Hosting is one Python process per venue. The margin
  problem is your time, not infrastructure.

Ladder up as capability is earned, not promised:

| | Price | Who |
|---|---|---|
| **Regulars** | **$40/mo per location** | One venue. Ten triggers, email copy, manual approval, CSV/copy export |
| Regulars + Send | $79/mo per location | Same, plus we transmit via their sender, unsubscribe handling, holdout reporting |
| Multi-site | $40 first site, $25 each additional | The 2–5 venue groups that are the target segment |
| Measurement pack | +$40/mo | 21-day holdout comparison, response rates fed back into the triggers |

Don't discount the entry price. Give away the **email-capture audit** instead: most
Square venues sit on thousands of customer records with no email address, because card
payments never expose one. Turning on the POS email prompt is free, takes ten minutes,
and is worth more than anything this product generates. It's the best proof you
understand their data, and it's useful whether or not they buy.

---

## Compliance, in the product

- **Spam Act 2003 s16** — every message carries venue name, ABN, the reason they're
  receiving it, and a functional unsubscribe tag matched to their sender. Asserted per
  message in the tests.
- **Suppression is checked before triggers.** No email → unreachable. No consent record
  → quarantined. Unsubscribed → permanent, no trigger overrides it. Inside the quiet
  window → left alone. Sent recently → capped.
- **Consent at import is an explicit attestation**, recorded as the consent source.
- **Nothing sends automatically**; the ledger is append-only (customer, exact content,
  trigger, consent basis, approver, timestamp). That's what you produce if ACMA asks.
- **A 10% holdout is applied in the export automatically.** Without a control group you
  cannot prove a campaign worked; comparing the venue total before-and-after counts
  people who were never in the audience.
- **Exports are minimised** — email, first name, subject, body, offer, category, consent
  source. No spend history, no mobile number, no order details.
- **Imports are memory-only** — an uploaded customer list is never written to disk.
- **SMS is off.** Since 1 July 2026, sending under a venue's branded sender ID needs ACMA
  Register approval as an entity associate, a registered sender ID, and recorded consent.
  Email carries Spam Act obligations but no registration regime.
- **Automated decision-making disclosure** from 10 December 2026 — segmenting customers
  for targeting counts. Hand the merchant the privacy-policy clause at onboarding.
- **No third-party enrichment of customers.** Matching your customers against social,
  electoral or broker data is "trading in personal information": it removes the
  small-business exemption from the Privacy Act regardless of turnover, breaches APP 6
  purpose limitation, and is less accurate than the order history you already have.
  Enrich the context instead — postcode demographics, holidays, events, weather, and
  your own measured response rates. None of that is personal information.

None of this is legal advice. Before sending for a paying customer, have an Australian
commercial lawyer review the merchant agreement, privacy policy template and
consent-capture wording. Budget $3,000–$5,000 once.

---

## Security

One instance = one venue, and the app assumes it is the only tenant:

- **Password gate.** PBKDF2-SHA256 (210k iterations), 12-hour HttpOnly
  SameSite=Lax sessions, CSRF tokens on every form, five bad passwords →
  15-minute per-address lockout. The first password can only be set from
  localhost (or `REGULARS_OPEN_FIRST_RUN=1`, once, behind TLS).
- **Outside-attack detection.** Per-IP rate limits (120 POST/min, 600 GET/min),
  an 8 MB upload cap, forged-OAuth-callback rejection — all refused *and*
  written to the audit log, all visible on the Security screen.
- **Tamper-evident audit log.** Every login, config change, approval, export,
  upload, reset and OAuth event is hash-chained to the entry before it. Edit or
  delete one line and **Verify chain integrity** says exactly where the log
  stops being trustworthy.
- **Secrets.** Square OAuth tokens are stored encrypted at rest
  (encrypt-then-MAC) under a key from `REGULARS_MASTER_KEY` or an
  auto-generated 0600 keyfile. Credentials live in the environment, never in
  the state file. Uploaded customer CSVs are never written to disk.
- **Headers.** CSP, nosniff, frame DENY, no-store on every response; HSTS when
  `REGULARS_TLS=1`.

Full operator guide: `DEPLOYMENT.md` (Docker, reverse proxy, env reference,
Square app registration, backups, incident response). Release history:
`CHANGELOG.md`.

## Two markets

The Setup screen switches the market between **Australia** and the **United
States**, and the product changes where the law says it must:

- **AU (Spam Act 2003):** sender identity, working one-click unsubscribe, ABN
  in the footer, consent recorded per contact.
- **US (CAN-SPAM):** a valid physical postal address in every commercial
  email (the app *refuses* to switch to US without one), the commercial-email
  line, and opt-outs honoured within 10 business days.
- Seasons follow the hemisphere (a July visit is "back in winter" in
  Melbourne, "back in summer" in Chicago) and customer copy respells to US
  English. The copy rule is unchanged in both markets: never quote the
  customer's own numbers back at them.

---

## Performance at real-venue sizes

`python3 tools/bench.py --sizes 525,2000,20000,50000` scales the demo venue up
and times the real code paths (measured 2026-11-05, one CPU core of a cloud VM):

| Customers | Orders | Line items | Messages | Engine run | Setup page | Messages page | Square CSV | Import | Peak RAM |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 525 (demo) | 8,124 | 28,022 | 199 | 0.10 s | 0.11 s | 0.11 s | 3 MB | 0.7 s | 78 MB |
| 2,100 | 32,365 | 111,655 | 799 | 0.36 s | 0.33 s | 0.28 s | 11 MB | 3.2 s | 217 MB |
| 20,475 | 318,147 | 1,094,736 | 7,683 | 3.36 s | 5.10 s | 2.99 s | 111 MB | 49.7 s | 1,755 MB |
| 50,400 | 783,897 | 2,695,103 | 19,045 | 11.57 s | 9.80 s | 7.23 s | 276 MB | 163.1 s | 4,291 MB |

What this means today:

- **The 8 MB upload cap blocks venues above roughly 1,500 customers** sending
  their full history (at the demo's order rate). Gate 2 makes the cap
  configurable; Gate 3 makes large imports practical.
- **Every page view re-runs the engine**, so at 20,000 customers each screen
  takes 3-5 seconds. Gate 3 caches the result between changes.
- **The Messages screen shows the first 120 messages only.** The export carries
  all of them; paging the screen is Gate 3.
- **Memory**: a 20,000-customer import peaks near 1.8 GB, too much for the
  smallest VPS plans. Gate 3's target is under 512 MB.

## Files

```
app/data.py       synthetic venue: menu, 11 archetypes, order generator, sample exports
app/engine.py     the pipeline every industry shares: suppress → order triggers → pick
                  offer → copy → tone, regional spelling, legal footer
app/packs/        industry packs (REGULARS_INDUSTRY, default restaurant): profile,
                  triggers, thresholds, offer categories, email templates
app/core.py       industry-neutral wording: vague timing, seasons, US spelling
tools/bench.py    timings and memory at 2k-50k customers (see Performance)
app/importer.py   Square-format and simple-format CSV parsing, menu inference
app/security.py   passwords, sessions, CSRF, rate limits, audit chain, secret box, headers
app/oauth.py      Square authorization-code flow: authorize, exchange, refresh, revoke
app/store.py      the JSON state file (schema-versioned, migrated on boot)
app/ui.py         editorial CSS and layout helpers; <details>-based, zero JavaScript
app/server.py     HTTP layer only: rate limits, sign-in, CSRF, the GET/POST route tables
app/state.py      the venue's settings (STATE), audit log, guard, secret box, LOCK,
                  sessions, persistence, and the engine run every screen shares
app/web.py        Req / Resp values passed between the handler and the screens
app/views.py      HTML fragments shared by several screens
app/pages/        one module per screen (setup, goals, triggers, messages, sending,
                  sent, data, security, login): its page plus its form actions
app/exports.py    audience CSV, copy-paste text, sample input files
app/upload.py     multipart parsing and CSV-to-dataset import
Dockerfile        python:3.12-slim, non-root, /data volume, healthcheck
DEPLOYMENT.md     operator guide: run, proxy, env, Square app, backups, incidents
CHANGELOG.md      release history and state-schema migrations
.github/          CI: the full test suite on every push and pull request
tests/            124 tests: dataset, one-message-per-person, offer/behaviour match,
                  the copy rule, suppression, Spam Act/CAN-SPAM footers, regions,
                  goals/threshold/catalogue sensitivity, import, persistence, HTTP
                  routes, security primitives, auth/CSRF/lockouts, OAuth roundtrip
                  against a mock Square token server
```

The earlier, much larger analytics prototype (`deep-analytics-v1`) is kept separately,
outside this repository — its menu-margin module is still worth reusing later. It is not the product; it was too
complicated to sell to a non-technical operator.
