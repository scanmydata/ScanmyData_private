# ScanmyData Private

## AI fallback behavior

The AI fallback is now enabled by default when scraping receipts and invoices.

- Default: `AI fallback is active`.
- To explicitly disable fallback, set `SCRAPER_AI_FALLBACK_DISABLED=1`.
- The older enable flag still works when present:
  - `SCRAPER_AI_FALLBACK_ENABLED=1` forces fallback on
  - `SCRAPER_AI_FALLBACK_ENABLED=0` forces fallback off

This change means the scraper modules will automatically use the AI fallback when needed, without requiring an environment variable to enable it.
