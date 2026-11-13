# Review site (Netlify)

`site/` is a **read-only static snapshot** of the Regulars demo app (the "Marlow & Finch" demo venue,
synthetic data), published by `netlify.toml`. Netlify can't run the app itself (it is a Python server),
so this is what you click through to review progress: every screen and every message page, with buttons
and forms switched off and a banner saying so.

Regenerate it whenever the app changes. Run the app from a throwaway copy so its state files
don't land in the repo:

```bash
mkdir /tmp/app-copy && git archive HEAD app | tar -x -C /tmp/app-copy
(cd /tmp/app-copy/app && python3 server.py 8010 &)
python3 scripts/make_snapshot.py http://127.0.0.1:8010 \
    --scrub /tmp/app-copy --source "main @ $(git rev-parse --short HEAD)" \
    --landing /inbox/ \
    --banner "Demo: an example restaurant with made-up customers. Buttons and forms are switched off." \
    --contact-email you@yourdomain.com.au \        # optional: adds "Want this on your own data?"
    --try-url https://try.yourdomain.com.au        # optional: links to the interactive demo
```

- `--landing /inbox/` makes the bare URL open on the Messages screen (the product). The app's own
  home screen moves to `/setup/`, so the "Setup" tab still works.
- The banner says what the page is; where it came from (`--source`, build date) is a tooltip on it.
- Operator-only notices ("No operator password is set") are removed: they mean nothing in a demo.
- The generator replaces local paths, masks demo email domains (`...@icloud.example`, so no made-up
  address can match a real person's), adds `noindex` everywhere and refuses to write output that still
  contains a filesystem path. Your `--contact-email` is the one address it leaves alone. It only
  overwrites a folder it created. Tests: `tests/test_snapshot.py`.
- `--try-url` points visitors at the interactive public demo (`DEPLOYMENT.md`, "A public try-it demo"): it
  adds "Try it with your own Square export" to the banner and to the message shown when someone clicks a
  switched-off button. It must be an `https://` address.
