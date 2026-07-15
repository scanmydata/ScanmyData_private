# ScanmyData Private

## Frontend assets (Tailwind)

Tailwind is compiled **at build time** into `static/styles/tailwind.css`, which is
committed to the repo so the Docker image needs no Node toolchain.

The app previously loaded `https://cdn.tailwindcss.com` (the Play CDN), which ships
a ~400KB CSS compiler that re-scanned the DOM and regenerated the stylesheet on every
single page load. The compiled file is ~7KB over the wire and costs no CPU.

**If you add or change Tailwind classes in `templates/` or `static/`, rebuild:**

```bash
npm install       # once
npm run build:css # after any class change -- then commit static/styles/tailwind.css
npm run watch:css # rebuild automatically while developing
```

Classes are discovered by scanning `templates/**/*.html` and `static/**/*.js`
(see `tailwind.config.js`). Tailwind only matches **complete** class strings, so
`'bg-' + color + '-500'` will not be picked up — write the full name out.

jQuery, DataTables and Bootstrap are vendored in `static/vendor/` (byte-identical
to the CDN builds they replaced) rather than loaded from jsdelivr /
code.jquery.com / cdn.datatables.net. Each of those was a separate origin costing
a DNS lookup and TLS handshake on first visit; from our own origin they reuse the
warm connection and the app no longer breaks when a CDN is unreachable. Some
per-page libraries (tabulator, flatpickr, chart.js, tesseract.js) are still
CDN-loaded.

`static/styles/tailwind.css` must stay the **last** stylesheet in `base.html`'s
`<head>`, because that is where the Play CDN used to inject its generated styles;
moving it changes which rules win the cascade.

## Sessions: inactivity vs tab close

Two independent timers, backed by two separate columns on `user`. Keeping them
separate matters — one field cannot serve both.

| Signal | Column | Refreshed by | Config | Meaning |
|---|---|---|---|---|
| User interacted | `last_active_at` | real requests (`session_heartbeat`, throttled 30s) | `SESSION_TIMEOUT_SECONDS` (900) | idle user → logout |
| A tab is open | `tab_alive_at` | `POST /api/session/ping` every `TAB_PING_INTERVAL_SECONDS` | `TAB_CLOSE_GRACE_SECONDS` (90) | all tabs closed → logout |

`/api/session/ping` is deliberately excluded from `session_heartbeat`, so an open
but idle tab still hits the inactivity logout.

**Why logout-on-close is done with a ping rather than a `beforeunload` beacon:**
there is no event meaning "this tab is closing". `beforeunload`/`pagehide` also
fire on ordinary navigation and on reload, so a beacon sent from them logs the
user out mid-session and races the next page load. Inverting it removes the
guesswork: an open tab keeps saying "I'm here", and closing the tab, the browser,
or killing the process all stop the pings by simply not happening. Navigation and
reload are safe because the next page pings immediately, and with several tabs
open each one pings, so closing one leaves the others working.

The session is retired lazily — on the first request after the grace expires, not
at the instant of the close. The cookie is unusable either way, which is the
property that matters. Set `TAB_CLOSE_GRACE_SECONDS=0` to disable.

Note both `/api/logout` and `/api/session/ping` live at the **root**: `auth_bp` is
registered with no `url_prefix`. There is no `/auth/...` prefix — code that
assumed one silently 404'd for a long time.

## AI fallback behavior

The AI fallback is now enabled by default when scraping receipts and invoices.

- Default: `AI fallback is active`.
- To explicitly disable fallback, set `SCRAPER_AI_FALLBACK_DISABLED=1`.
- The older enable flag still works when present:
  - `SCRAPER_AI_FALLBACK_ENABLED=1` forces fallback on
  - `SCRAPER_AI_FALLBACK_ENABLED=0` forces fallback off

This change means the scraper modules will automatically use the AI fallback when needed, without requiring an environment variable to enable it.
