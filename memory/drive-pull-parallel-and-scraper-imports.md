---
name: drive-pull-parallel-and-scraper-imports
description: Drive warmup timed out at 1/3 groups — pull was serial; now parallel. Plus the scraper bare-import gotcha.
metadata:
  type: project
---

**2026-06-29 fixes.**

1. **Drive warmup timing out (only 1/3 groups, then fail-open).** Root cause: `firebase/drive_storage.py` `drive_pull_group_to_local` downloaded every file **serially** (one Drive round-trip per file). The startup warmup (`firebase/startup_warmup.py`) loops groups serially with a `DRIVE_WARMUP_MAX_SECONDS` (default 300s) budget, so a big group blew the budget and the gate fail-opened. Fix: split into a no-network **plan pass** (smart-sync skip + makedirs) then a parallel **download pass** via `ThreadPoolExecutor(max_workers=_pull_workers())`. Safe because each worker uses a thread-local Drive service (per module docstring) and writes a unique path; counters mutate only in the main `as_completed` loop. Tunable: `DRIVE_PULL_WORKERS` (default 8, cap 32). Related: [[slow-login-cause]].

2. **Scraper "Δεν βρέθηκαν δεδομένα για το URL" while individual scrapers work.** Gotcha: `scraper/scraper_receipt.py` had a **bare** `from scraper_receipt_analysis import scrape_megasoft` (megasoft/invoicelink branch). Works when the file is run standalone (scraper/ dir on sys.path) but raises ImportError when loaded as `scraper.scraper_receipt` (how `/api/scrape_receipt` mixed mode loads it) → `result=None` → 422. Fixed with relative-then-bare import fallback. The package as a whole imports fine (`from scraper.scraper_receipt import detect_and_scrape` works), so non-megasoft "no data" cases are provider-specific and need the actual failing URL to diagnose. Endpoint: `app.py` `api_scrape_receipt` (~line 12373); default mode is `mixed` → `scraper_receipt.py`, `analysis` → `scraper_receipt_analysis.py`.

3. **Impact (einvoice.impact.gr) scraper: works local, "no data" on deployed server.** *(Superseded — see [[impact-scraper-waf-vpn-ip]] for the real cause.)* Initial theory was AADE blocking + an unconditional empty-return in `scrape_impact`, and a DOM-first rewrite was attempted — but the user confirmed the scraper works standalone and asked to leave it untouched, so **that change was reverted** (`git checkout scraper/`). Real root cause: `einvoice.impact.gr`'s WAF rejects the server's VPN/datacenter egress IP (AS136787 PacketHub) at the TCP/TLS layer → `RemoteDisconnected` → scraper returns all-null. Fix is environmental (residential GR proxy/IP), not code. The "Δεν βρέθηκαν δεδομένα" message is backend-only: `app.py` `api_scrape_receipt` ~line 12473; frontend sends the full URL (console `...` is just log truncation).
