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

## 2. Run it locally (evaluation / single machine)

```bash
cd regulars/app
python3 server.py 8010        # or: ./start.sh from the regulars folder
```

Open http://localhost:8010, then **set the operator password immediately** —
the first password can only be created from localhost (or with
`REGULARS_OPEN_FIRST_RUN=1`, see below). Until a password exists the app runs
in open demo mode and says so on every screen.

## 3. Run it on a server (the recommended shape)

Docker + a TLS-terminating reverse proxy. The app itself speaks plain HTTP and
expects the proxy to handle certificates.

```bash
docker build -t regulars:0.5.0 .
docker run -d --name regulars \
  -p 127.0.0.1:8010:8010 \
  -v regulars-data:/data \
  -e REGULARS_MASTER_KEY="$(openssl rand -hex 32)" \
  -e SQUARE_CLIENT_ID=... -e SQUARE_CLIENT_SECRET=... \
  -e REGULARS_REDIRECT_URI=https://regulars.yourvenue.com/oauth/callback \
  regulars:0.5.0
```

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
| `REGULARS_OPEN_FIRST_RUN` | *(unset)* | `1` = allow creating the first password from a non-localhost address. Use once, behind TLS, then unset |
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
- [ ] Operator password created (Security screen stops showing the demo-mode warning)
- [ ] `REGULARS_MASTER_KEY` set from the environment (Security screen → Credentials shows "environment")
- [ ] Square OAuth configured, or the venue is on CSV upload
- [ ] `/healthz` returns `{"status":"ok","version":...}` and your uptime monitor watches it
- [ ] `/data` volume (or the three files) included in backups
- [ ] You know where the audit log is and have clicked **Verify chain integrity** once

## 7. Patching and releasing

- **Patch:** build a new image tag, `docker stop regulars && docker rm regulars`, run the new tag against the same `/data` volume. State schema migrations run automatically on boot and are written to the audit log.
- **Roll back:** run the previous image tag; state files are forward- and backward-tolerant within the same schema version.
- **Never** edit `regulars-state.json` or `regulars-audit.log` by hand on a live instance — the audit log is hash-chained and manual edits will (correctly) show up as tampering on the Security screen.
- Tests: `cd tests && python3 -m unittest discover -s . -t ..` — 112 tests, no dependencies.

## 8. Monitoring and incident response

- `GET /healthz` — version, schema, region, whether auth is enabled. Alert on non-200 or on `auth_enabled:false` in production.
- **Security screen** (`/security`) — audit chain status, active sessions, rate-limit lockouts, recent flags, deployment checklist. Check it weekly.
- Symptom → response:
  - **`Audit chain BROKEN`** → treat as an incident: preserve the log file, find who/what touched it, rotate the master key, force logout everywhere (button on the Security screen), reset the password.
  - **Unknown address lockouts in the log** → someone is brute-forcing the password. Lockouts contain it; consider firewalling the address. The password gate + 15-minute lockouts make dictionary attacks impractical, but nobody should be finding your login page at all — keep the hostname unlisted.
  - **`oauth_fail` with state mismatch** → a forged or stale Square callback was rejected. Single occurrences are noise; a burst is someone probing.
  - **Lost master key** → stored Square tokens are unrecoverable by design. Have the venue revoke the grant in their Square Dashboard and reconnect.
  - **Forgotten operator password** → there is no reset flow (by design, it would be an attack vector). Stop the app, delete `password_hash` from the state file *on the server* (or delete the state file and reconfigure), restart, set a new password from localhost. The audit log keeps the history.

## 9. Known limits (honest list)

These are documented in `../04-COMMERCIAL-READINESS-TEARDOWN.md` with the plan:

- Single instance, single venue. Sessions and rate limits are in-process; multi-tenant SaaS needs shared storage and per-venue isolation.
- The stdlib HTTP server is fine behind a proxy for one venue's traffic; it is not a hardened production WSGI server.
- OAuth tokens are obtained and stored, but live Square API data pulls (Orders/Customers sync) are not wired yet — the app runs on demo data or CSV uploads. This is the first production feature.
- No email transmission: exports go to the venue's own sender, as designed.
- X-Forwarded-For is trusted for rate limiting — correct behind your proxy, spoofable if the port is exposed directly. Bind to 127.0.0.1 and let the proxy face the internet.
