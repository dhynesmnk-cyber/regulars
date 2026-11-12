# Review site (Netlify)

`site/` is a **read-only static snapshot** of the Regulars demo app (the "Marlow & Finch" demo venue,
synthetic data), published by `netlify.toml`. Netlify can't run the app itself (it is a Python server),
so this is what you click through to review progress: every screen and every message page, with buttons
and forms switched off and a banner saying so.

Regenerate it whenever the app changes:

```bash
./start.sh 8010 &                            # the app, demo mode, from the repo root
python3 scripts/make_snapshot.py http://127.0.0.1:8010 \
    --source "app @ $(git rev-parse --short HEAD)" --scrub "$PWD"
```

The generator replaces local paths, masks demo email domains (`...@icloud.example`, so no made-up address
can match a real person's), adds `noindex` everywhere and refuses to write output that still contains a
filesystem path. It only overwrites a folder it created. Tests: `tests/test_snapshot.py`.
