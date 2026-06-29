---
name: impact-scraper-waf-vpn-ip
description: Why einvoice.impact.gr scraping fails on the production server but works locally — WAF blocks the VPN/datacenter egress IP
metadata:
  type: project
---

**Symptom:** `/api/scrape_receipt` returns 422 "Δεν βρέθηκαν δεδομένα για το URL" for `einvoice.impact.gr` URLs **only on the deployed server** (scanmydata.gr); the same scraper code works locally, and other providers (AADE `www1.aade.gr`, Epsilon) work fine from the server too.

**Root cause (NOT frontend, NOT scraper code):** The production server's outbound egress IP is `186.247.46.70` — geolocates to Athens GR but belongs to **AS136787 PacketHub S.A.**, a commercial **VPN/datacenter** provider (not a residential ISP). `einvoice.impact.gr`'s anti-bot **WAF rejects VPN/datacenter IP ranges**, closing the TCP connection at the TLS/handshake layer → Python `requests` raises `ConnectionError: RemoteDisconnected('Remote end closed connection without response')`. The scraper's `try/except` swallows it and returns an all-null dict with `source:"Impact"` (never reaches the AADE QR-follow step that would set `source:"Impact->MyData"`). AADE & Epsilon use more permissive WAFs, so they work from the same IP — that's the tell.

**How it was diagnosed:** server-side `python -c "requests.get(...)"` reproduced the RemoteDisconnected; a per-host probe showed google/aade/epsilon = 200 but impact = reset; `ipinfo.io/186.247.46.70` revealed the PacketHub VPN ASN. The frontend was verified to send the full URL (the `...` in the `[RC-DEBUG] FETCH` console log is just the logger truncating the body at 100 chars, see `search.html` ~line 10117 `init.body.slice(0,100)+'...'`).

**Fix (what actually resolved it, 2026-06-29):** simply **switching the NordVPN server/endpoint** — no code/config change at all. The old exit IP was on impact's blocklist; a different NordVPN exit gives an IP impact's WAF doesn't block. Confirms the block is purely **IP-reputation based** (not TLS fingerprint, not residential-vs-datacenter per se — just whether that specific IP is flagged).

**If it recurs:** the symptom is impact-only failure (AADE/Epsilon still 200) → just change the NordVPN server again.

**Διαγνωστική εντολή (τρέξε στον server, στο venv του app):**
```bash
python3 -c "import requests; print(requests.get('https://einvoice.impact.gr/', headers={'User-Agent':'Mozilla/5.0'}, timeout=15).status_code)"
```
→ `200` = η τρέχουσα VPN IP είναι ΟΚ · `ConnectionError/RemoteDisconnected` = η IP κόπηκε από το WAF του impact → **άλλαξε NordVPN server**.

More permanent options if churn becomes annoying: a residential GR proxy via `HTTPS_PROXY` on the app process (no scraper change), or NordVPN Meshnet routing through a residential device. The "Δεν βρέθηκαν δεδομένα" string is backend-only: `app.py` `api_scrape_receipt` ~line 12473.
