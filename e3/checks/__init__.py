# e3.checks package
"""Helpers shared by the Playwright-based E3 check scripts.

The scripts in this package can run in three places:

1. A developer's Windows workstation (full GUI).
2. An interactive Linux desktop or CI container (Xvfb / GUI optional).
3. The production Firebed / SVFB server-side browser host — a headless
   Linux container without a display server, with Chromium installed at
   ``PLAYWRIGHT_BROWSERS_PATH=/ms-playwright``.

The third environment is the strictest: Chromium must launch with the
container-safe flags (no sandbox, disable /dev/shm usage, disable GPU) or
it crashes silently. The helpers below centralise that configuration so
every E3 check script stays compatible without each having to remember
the full flag list.
"""

import os

# Standard Chromium flags for running inside an SVFB (server-side Firebed
# browser) container. Anything more relaxed must opt in explicitly.
SVFB_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
]


def chromium_launch_args(extra=None):
    """Return the canonical Chromium ``args`` list for ``launch(...)``.

    ``extra`` is an optional iterable of additional flags to append (e.g.
    ``--start-maximized`` for headed local debugging). Duplicates are
    preserved — Chromium tolerates them.
    """
    args = list(SVFB_CHROMIUM_ARGS)
    if extra:
        args.extend(extra)
    return args


def is_svfb_environment():
    """Heuristic: detect whether the script is running inside an SVFB host.

    True when PLAYWRIGHT_BROWSERS_PATH points to ``/ms-playwright`` (the
    Dockerfile default) or when ``SVFB=1`` is exported. Useful when a
    script wants to force headless or skip headed fallback paths.
    """
    if os.getenv("SVFB", "").strip() in {"1", "true", "yes", "on"}:
        return True
    path = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")
    return path.startswith("/ms-playwright")


def default_headless():
    """Return True unless the user explicitly asked for headed mode.

    Reads ``E3_HEADLESS`` / ``HEADLESS`` env vars (1/0). Defaults to True
    when the environment looks like SVFB, otherwise honours the caller's
    explicit choice via env. Designed so CLI ``--headless`` / ``--headed``
    flags can override it after the fact.
    """
    if is_svfb_environment():
        return True
    val = os.getenv("E3_HEADLESS") or os.getenv("HEADLESS")
    if val is None:
        return True
    return val.strip() in {"1", "true", "yes", "on"}
