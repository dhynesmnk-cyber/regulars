# Deploying Regulars

Regulars is one Python process with no dependencies beyond the standard
library. One instance serves **one venue** — that is the supported topology
today. Everything a venue configures lives in three files, and everything an
attacker could want is either hashed, encrypted, or not stored at all.

---

## 1. The three files that matter

| File | Env override | Contains | Back it up? |
|---|---|---|---|
| `regulars-state.json` | `REGULARS_STATE` | Goals, thresholds, sender config, send ledger, password hash, sessions, encrypted OAuth tokens | **Yes** |
| `regulars-audit.log` | `REGULARS_AUDIT_LOG` | Hash-chained security/event history | **Yes** (it is your evidence) |
| `regulars-master.key` | `REGULARS_KEYFILE` or replaced entirely by `REGULARS_MASTER_KEY` | Key that encrypts stored OAuth tokens | **Yes — losing it means stored tokens are unrecoverable (venue must reconnect Square)** |

Uploaded CSV data is deliberately **not** persisted: it lives in RAM for the
process lifetime and is gone on restart. That is a privacy feature, not a bug.

With `regularsctl` (section 3) the first two files live in each venue's Docker
volume and the master key lives in `/etc/regulars/venues/<slug>.env`, outside
the volume, so data backups never contain it. Keep a copy of every venue's
key in your password manager.

## 2. Run it locally (evaluation / single machine)

```bash
cd regulars/app
python3 server.py 8010        # or: ./start.sh from the regulars folder
```

Open http://localhost:8010, then **set the operator password immediately** —
the first password can only be created from localhost (or with
`REGULARS_OPEN_FIRST_RUN=1`, see below). Until a password exists the app runs
in open demo mode and says so on every screen.

It listens on `127.0.0.1` only, so nothing else on your network can reach it.
To try it from a phone on the same Wi-Fi, start it with `REGULARS_BIND=0.0.0.0`
(evaluation only: that's plain HTTP).

## 3. Run venues on a server

One Ubuntu VPS (AU region for Australian venues), Docker, and Caddy in front
for HTTPS. Each venue is its own container, volume and port, at
`https://<venue>.<your domain>`. `deploy/regularsctl.py` does every step; the
rest of this section is what it does, for when you need to know.

### Set up the server once

```bash
# Docker, plus Caddy from caddyserver.com/docs/install (Debian/Ubuntu section)
apt install -y docker.io
# Only SSH and the web reach the box. (Venue ports are published on
# 127.0.0.1, so Docker's firewall bypass doesn't expose them.)
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
git clone https://github.com/dhynesmnk-cyber/regulars /opt/regulars
ln -s /opt/regulars/deploy/regularsctl.py /usr/local/bin/regularsctl
regularsctl init --domain regulars.example.com.au
echo 'import /etc/caddy/regulars/*.caddy' >> /etc/caddy/Caddyfile && systemctl reload caddy
```

At your DNS provider, add one wildcard record: `*.regulars.example.com.au` →
the server's IP. Caddy fetches each venue's certificate as soon as
`provision` adds its site. Square credentials, once you have them, go in
`/etc/regulars/shared.env` (`SQUARE_CLIENT_ID=...`, `SQUARE_CLIENT_SECRET=...`).
New venues pick them up straight away; existing ones when their containers are
next recreated (`regularsctl upgrade` does every venue).

Then `/etc/cron.d/regulars`:

```
MAILTO=you@example.com
# nightly backup, then copy it off the box (rclone works with B2, S3, Drive...)
15 3 * * * root regularsctl backup && rclone copy /var/backups/regulars offsite:regulars
# every 5 minutes: silent unless a venue is down, then cron emails you
*/5 * * * * root regularsctl status --quiet
```

### Day to day

| Task | Command |
|---|---|
| New venue | `regularsctl provision juniper-co` - prints the owner's one-time link, the uptime-monitor URL and the master key to save |
| What's running | `regularsctl status` |
| Ship a new version | `git pull && regularsctl upgrade` - rebuilds, recreates each venue, and if the first venue doesn't come back healthy, rolls it back and stops |
| Back up now | `regularsctl backup` (settings and audit log; never keys) |
| Restore a venue | `regularsctl restore juniper-co /var/backups/regulars/<archive> --yes` - takes a safety copy first, then checks the audit chain |
| Owner lost their link | `regularsctl link juniper-co` |
| Owner forgot their password | `regularsctl link juniper-co --reset-owner` - clears it (audited) and prints a new link |

Every command takes `--dry-run`.

### Doing it by hand (one venue)

Docker + a TLS-terminating reverse proxy. The app itself speaks plain HTTP and
expects the proxy to handle certificates.

```bash
docker build -t regulars:0.5.0 .
docker run -d --name regulars \
  -p 127.0.0.1:8010:8010 \
  -v regulars-data:/data \
  -e REGULARS_MASTER_KEY="$(openssl rand -hex 32)" \
  -e REGULARS_SETUP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  -e SQUARE_CLIENT_ID=... -e SQUARE_CLIENT_SECRET=... \
  -e REGULARS_REDIRECT_URI=https://regulars.yourvenue.com/oauth/callback \
  regulars:0.5.0
```

**Handing it to the owner.** With `REGULARS_SETUP_TOKEN` set, the instance
is *unclaimed*: every screen answers "this instance hasn't been set up yet"
except `/healthz` and the owner's one-time link. Send the owner:

```
https://regulars.yourvenue.com/welcome?token=<the REGULARS_SETUP_TOKEN value>
```

Opening it lets them choose their password; they're signed in and the link is
burned (its hash is kept in the state file, so it never works again, even if
the variable stays set). Wrong tokens count towards the normal sign-in
lockout and are audited as `setup_fail`. The token is masked as
`token=[redacted]` in the server log and never written to the audit log. To
issue a new link later (say, after a server-side password reset), start the
container with a new token. Tokens shorter than 24 characters stop start-up.

Reverse proxy (Caddy example — automatic HTTPS):

```
regulars.yourvenue.com {
    reverse_proxy 127.0.0.1:8010 {
        header_up X-Forwarded-For {remote_host}
    }
}
```

`REGULARS_TLS=1` is set in the Dockerfile; it adds the HSTS header. Set it
whenever the app is reachable over HTTPS, and leave it off for plain-HTTP
local testing.

**Client addresses.** Rate limits, the sign-in lockout and the audit log key
on the visitor's IP address. Behind a proxy the app only sees the proxy's, so
the Dockerfile sets `REGULARS_TRUST_PROXY=1`: the app then takes the address
your proxy *appended* to `X-Forwarded-For` (the right-most entry), ignores
anything the visitor wrote to its left, and only believes the header on
connections from a loopback or private address. Without it, the header is
ignored. Get this right in both directions:

- Proxy in front but `REGULARS_TRUST_PROXY` unset: every visitor shares the
  proxy's address, so one attacker's lockout locks everyone out.
- `REGULARS_TRUST_PROXY` set but the port reachable from the internet: a
  visitor can claim any address. Publish the port on `127.0.0.1` only.
- A CDN (e.g. Cloudflare) in front of your proxy: set `REGULARS_TRUST_PROXY=2`.

Caddy's `header_up X-Forwarded-For {remote_host}` (above) works, and so does
nginx's usual `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`.
The start-up log prints a `network:` line when the combination looks wrong.

**Data residency:** the container runs where you run it. For Australian venues
pick an AU region (Sydney/Melbourne) on your host; for US venues a US region.
Nothing phones home — the only outbound calls the app makes are to Square's
OAuth endpoints during connect/refresh/revoke.

## 4. Environment reference

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | Listen port (CLI arg wins: `python3 server.py 8010`) |
| `REGULARS_STATE` | `app/regulars-state.json` | State file path |
| `REGULARS_AUDIT_LOG` | `app/regulars-audit.log` | Audit log path |
| `REGULARS_MASTER_KEY` | *(unset)* | Token-encryption key from the environment. If unset, a 0600 keyfile is auto-generated at `REGULARS_KEYFILE` |
| `REGULARS_KEYFILE` | `app/regulars-master.key` | Keyfile location |
| `REGULARS_TLS` | *(unset)* | Set to `1` behind HTTPS: adds HSTS, marks cookies `Secure` |
| `REGULARS_BIND` | `127.0.0.1` | Interface to listen on. The Docker image sets `0.0.0.0` and expects the port published on the host's `127.0.0.1` only |
| `REGULARS_TRUST_PROXY` | *(unset)* | Number of reverse proxies in front (`1` for Caddy/nginx, `2` with a CDN too). Unset = `X-Forwarded-For` ignored. The Docker image sets `1` |
| `REGULARS_SETUP_TOKEN` | *(unset)* | One-time owner setup link (`/welcome?token=...`). While set and no password exists, the whole app is closed except that link and `/healthz`. At least 24 characters. **Use this for every instance you hand to a venue** |
| `REGULARS_OPEN_FIRST_RUN` | *(unset)* | `1` = allow creating the first password from a non-localhost address. Use once, behind TLS, then unset. Ignored while `REGULARS_SETUP_TOKEN` is set |
| `SQUARE_CLIENT_ID` / `SQUARE_CLIENT_SECRET` | *(unset)* | Square OAuth application credentials. Unset = the connect button explains setup instead of failing |
| `REGULARS_REDIRECT_URI` | `http://localhost:8010/oauth/callback` | Must match the redirect URL registered in the Square Developer Dashboard exactly |
| `SQUARE_OAUTH_BASE` | `https://connect.squareup.com` | **Tests only.** Never set in production |

## 5. Square OAuth application (one-time, free)

1. Sign in at https://developer.squareup.com → **Applications** → new app.
2. Under **OAuth**, add the redirect URL you will serve
   (`https://regulars.yourvenue.com/oauth/callback`).
3. Request only these scopes: `MERCHANT_PROFILE_READ`, `ORDERS_READ`,
   `CUSTOMERS_READ`, `CATALOG_READ`, `LOYALTY_READ`. Read-only; the venue can
   revoke the grant at any time from its Square Dashboard.
4. Copy the Application ID and Application Secret into
   `SQUARE_CLIENT_ID` / `SQUARE_CLIENT_SECRET`.

Sandbox credentials work the same way against Square's sandbox if you set
`SQUARE_OAUTH_BASE=https://connect.squareupsandbox.com` (plus sandbox app
credentials) — useful for staging.

## 6. First-run checklist

- [ ] App reachable over HTTPS; `REGULARS_TLS=1`
- [ ] Venue provisioned with `regularsctl provision`, its master key saved in your password manager, and its `/healthz` added to your uptime monitor
- [ ] `/etc/cron.d/regulars` installed (nightly backup copied off the box; `status --quiet` every 5 minutes)
- [ ] Owner opened their setup link and chose a password (Security screen: "Claimed by its owner through the one-time setup link"), or the operator password was created from localhost
- [ ] `REGULARS_MASTER_KEY` set from the environment (Security screen → Credentials shows "environment")
- [ ] Square OAuth configured, or the venue is on CSV upload
- [ ] Business name, suburb and ABN (or US postal address) saved under **Your business** on Setup; approving and exporting real data stay off until they are
- [ ] `/healthz` returns `{"status":"ok","version":...}` and your uptime monitor watches it
- [ ] `/data` volume (or the three files) included in backups
- [ ] You know where the audit log is and have clicked **Verify chain integrity** once

## 7. Patching and releasing

- **Patch:** `git pull && regularsctl upgrade`. It builds the image from the checkout, keeps the old one as `regulars:previous`, and recreates each venue on the new one. State schema migrations run on boot and are written to the audit log. If the first venue isn't healthy within a minute it goes back to `:previous`, `:current` is pointed back at the old image, and nothing else is touched.
- **Roll back on purpose:** `regularsctl upgrade --tag previous` (or any earlier tag in `docker image ls regulars`).
- **Never** edit `regulars-state.json` or `regulars-audit.log` by hand on a live instance — the audit log is hash-chained and manual edits will (correctly) show up as tampering on the Security screen. `regularsctl restore` and `link --reset-owner` stop the app first and leave an audit trail.
- **Tests:** from the repository root, `python3 -m unittest discover -s tests -t .` (no dependencies). CI runs them on every pull request.

## 8. Monitoring and incident response

- `GET /healthz` — version, schema, region, whether auth is enabled. Put each venue's `https://<venue>.<domain>/healthz` in an uptime monitor; the `status --quiet` cron line is the free fallback. `auth_enabled:false` on a venue that's been live a while means the owner never claimed it.
- **Security screen** (`/security`) — audit chain status, active sessions, rate-limit lockouts, recent flags, deployment checklist. Check it weekly.
- Symptom → response:
  - **`Audit chain BROKEN`** → treat as an incident: preserve the log file, find who/what touched it, rotate the master key, force logout everywhere (button on the Security screen), reset the password.
  - **Unknown address lockouts in the log** → someone is brute-forcing the password. Lockouts contain it; consider firewalling the address. The password gate + 15-minute lockouts make dictionary attacks impractical, but nobody should be finding your login page at all — keep the hostname unlisted.
  - **`oauth_fail` with state mismatch** → a forged or stale Square callback was rejected. Single occurrences are noise; a burst is someone probing.
  - **Lost master key** → stored Square tokens are unrecoverable by design. Have the venue revoke the grant in their Square Dashboard and reconnect.
  - **Forgotten operator password** → there is no self-service reset (by design: a reset channel is an attack vector). On the server: `regularsctl link <venue> --reset-owner` clears the password and sessions, writes `owner_reset` to the audit log, and prints a new one-time link for the owner.
  - **Owner never used their link, or lost it** → `regularsctl link <venue>`. The old link stops working.

## 9. Known limits (honest list)

These are documented in `../04-COMMERCIAL-READINESS-TEARDOWN.md` with the plan:

- Single instance, single venue. Sessions and rate limits are in-process; multi-tenant SaaS needs shared storage and per-venue isolation.
- The stdlib HTTP server is fine behind a proxy for one venue's traffic; it is not a hardened production WSGI server.
- OAuth tokens are obtained and stored, but live Square API data pulls (Orders/Customers sync) are not wired yet — the app runs on demo data or CSV uploads. This is the first production feature.
- No email transmission: exports go to the venue's own sender, as designed.
- Client addresses come from `X-Forwarded-For` only when `REGULARS_TRUST_PROXY` says a proxy is in front, and then only the entry that proxy added. If the app's port is reachable from the internet with that setting on, the header can be forged: keep the port on loopback and let the proxy face the internet.
