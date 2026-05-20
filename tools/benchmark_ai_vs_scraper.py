#!/usr/bin/env python3
import json
import os
import statistics
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper import scrape_einvoicing_gr
from scraper import run_schema_ai_fallback

URL = "https://e-invoicing.gr/edocuments/ViewInvoice/-1/783c7336-2885-4be0-9381-0b82d10c6b1e_34ei7l8"

SCHEMA = {
    "MARK": {"type": "string", "required": False, "default": None, "description": "Document mark"},
    "issuer_vat": {"type": "string", "required": False, "default": None, "description": "Counterpart VAT/AFM"},
    "issue_date": {"type": "string", "required": False, "default": None, "description": "Issue date"},
    "total_amount": {"type": "string", "required": False, "default": None, "description": "Document total"},
    "doc_type": {"type": "string", "required": False, "default": None, "description": "Document type"},
}


def _run_ai_once():
    t0 = time.perf_counter()
    res = run_schema_ai_fallback(URL, SCHEMA, timeout_sec=40, error_hint="benchmark")
    dt = time.perf_counter() - t0
    ok = isinstance(res, dict) and bool(res)
    return {
        "ok": ok,
        "duration_sec": round(dt, 3),
        "result": res if ok else None,
    }


def _run_scraper_once():
    t0 = time.perf_counter()
    res = scrape_einvoicing_gr(URL, return_meta=True)
    dt = time.perf_counter() - t0
    mark = None
    afm = None
    meta = {}
    if isinstance(res, (tuple, list)):
        if len(res) > 0:
            mark = res[0]
        if len(res) > 1:
            afm = res[1]
        if len(res) > 2 and isinstance(res[2], dict):
            meta = res[2]
    ok = bool(mark)
    return {
        "ok": ok,
        "duration_sec": round(dt, 3),
        "result": {
            "MARK": mark,
            "issuer_vat": afm,
            "meta": meta,
        },
    }


def _summarize(runs):
    times = [r["duration_sec"] for r in runs]
    return {
        "runs": len(runs),
        "ok_runs": sum(1 for r in runs if r.get("ok")),
        "min_sec": min(times) if times else None,
        "max_sec": max(times) if times else None,
        "avg_sec": round(statistics.mean(times), 3) if times else None,
        "median_sec": round(statistics.median(times), 3) if times else None,
    }


def main():
    os.environ.setdefault("SCRAPER_AI_FALLBACK_ENABLED", "1")
    os.environ.setdefault("SCRAPER_AI_PROVIDER_CHAIN", "duckduckgo,pollinations")

    ai_runs = []
    scraper_runs = []

    # Keep benchmark short but meaningful.
    for _ in range(2):
        ai_runs.append(_run_ai_once())
    for _ in range(2):
        scraper_runs.append(_run_scraper_once())

    output = {
        "url": URL,
        "ai_provider_chain": os.getenv("SCRAPER_AI_PROVIDER_CHAIN"),
        "ai_runs": ai_runs,
        "ai_summary": _summarize(ai_runs),
        "scraper_runs": scraper_runs,
        "scraper_summary": _summarize(scraper_runs),
        "speed_ratio_ai_over_scraper": (
            round((_summarize(ai_runs)["avg_sec"] or 0) / ((_summarize(scraper_runs)["avg_sec"] or 1)), 3)
            if (_summarize(scraper_runs)["avg_sec"] or 0) > 0 else None
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
