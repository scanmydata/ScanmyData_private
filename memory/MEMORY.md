# Memory Index

- [Dev login & preview testing](dev-login-and-preview-testing.md) — how to log in (douradonis / tony / ΛΟΥΓΑΡΗΣ) and the session-lock + sendBeacon-logout gotchas
- [Slow login/save/delete cause](slow-login-cause.md) — Drive-backend sync activity logging made login/save/delete slow (undo was fast b/c it skips logging); fixed with async logging + reload dedup. See docs/SEARCH_PAGE_NOTES.md
- [Drive pull parallel + scraper imports](drive-pull-parallel-and-scraper-imports.md) — warmup timed out at 1/3 groups (serial pull → now ThreadPoolExecutor); scraper bare-import gotcha (works standalone, fails as package)
- [Presence reset on deploy](presence-reset-on-deploy.md) — all users showed "active" after redeploy (stale local SQLite last_active_at, no Firestore presence); auto-reset added in warmup leader
- [Impact scraper WAF/VPN-IP block](impact-scraper-waf-vpn-ip.md) — einvoice.impact.gr fails on server but works locally: its WAF blocks the server's VPN egress IP (IP-reputation). RESOLVED by switching the NordVPN server; recurs → switch again. Not a code bug.
