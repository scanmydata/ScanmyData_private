#!/usr/bin/env python3
"""Shared AI fallback for scraper modules.

Flow:
1) Load target URL with Playwright and capture rendered HTML.
2) Clean HTML into compact text.
3) Ask a free-text model provider for strict JSON matching a schema.
4) Return validated dict payload, or None when fallback is unavailable.

This module is enabled by default and can be explicitly disabled via environment flags.
"""

from __future__ import annotations

import json
import importlib
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

DEFAULT_PROVIDER = "duckduckgo,pollinations,openrouter,deepinfra,nerve"

COOKIE_HINT_RE = re.compile(
    r"cookie|consent|gdpr|privacy|modal|popup|overlay|banner|newsletter|subscribe|notif|chat|intercom|zendesk",
    re.IGNORECASE,
)
DECORATIVE_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "picture",
    "source",
    "video",
    "audio",
}
LAYOUT_TAGS = {"header", "footer", "nav", "aside"}


def _env_flag(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def is_ai_fallback_enabled() -> bool:
    explicit_enabled = _env_flag("SCRAPER_AI_FALLBACK_ENABLED")
    if explicit_enabled is not None:
        return explicit_enabled

    explicit_disabled = _env_flag("SCRAPER_AI_FALLBACK_DISABLED")
    if explicit_disabled is not None:
        return not explicit_disabled

    return True


def _safe_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except Exception:
        return default


def _capture_rendered_html(url: str, timeout_sec: int = 25) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return None, None, f"playwright unavailable: {exc}"

    timeout_ms = max(5000, timeout_sec * 1000)
    browser = None
    try:
        with sync_playwright() as p:
            headless_raw = str(os.getenv("SCRAPER_AI_PLAYWRIGHT_HEADLESS", "1")).strip().lower()
            headless = headless_raw in {"1", "true", "yes", "on"}
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            # SPA pages can keep running after DOM ready. Wait for meaningful content.
            extra_wait_ms = max(3000, _safe_int("SCRAPER_AI_SPA_WAIT_MS", 15000))
            min_text_chars = max(40, _safe_int("SCRAPER_AI_MIN_TEXT_CHARS", 350))
            deadline = time.time() + (extra_wait_ms / 1000.0)
            last_len = 0

            while time.time() < deadline:
                try:
                    state = page.evaluate(
                        """(minChars) => {
                            const text = (document.body && document.body.innerText) ? document.body.innerText : '';
                            const compact = String(text || '').replace(/\\s+/g, ' ').trim();
                            const lower = compact.toLowerCase();
                            const loadingTokens = ['loading', 'φορτ', 'αναμον', 'please wait', 'παρακαλώ περιμένετε', 'spinner', 'ajax', 'fetch', 'upload', 'μεταφόρτ'];
                            const hasLoading = loadingTokens.some(t => lower.includes(t));
                            return {
                                readyState: document.readyState,
                                textLen: compact.length,
                                hasLoading,
                                good: compact.length >= minChars && !hasLoading
                            };
                        }""",
                        min_text_chars,
                    )
                    last_len = int(state.get("textLen") or 0)
                    if bool(state.get("good")):
                        break
                except Exception:
                    pass
                page.wait_for_timeout(700)

            try:
                page.evaluate(
                    """() => {
                        const selectors = [
                            'script', 'style', 'noscript', 'svg', 'canvas',
                            '[aria-hidden="true"]', '[hidden]',
                            '[role="dialog"]', '[role="alertdialog"]',
                            '[id*="cookie" i]', '[class*="cookie" i]',
                            '[id*="consent" i]', '[class*="consent" i]',
                            '[id*="banner" i]', '[class*="banner" i]',
                            '[id*="modal" i]', '[class*="modal" i]',
                            '[id*="popup" i]', '[class*="popup" i]',
                            '[id*="overlay" i]', '[class*="overlay" i]',
                            'header', 'footer', 'nav', 'aside'
                        ];
                        for (const node of document.querySelectorAll(selectors.join(','))) {
                            try { node.remove(); } catch (_) {}
                        }
                    }"""
                )
            except Exception:
                pass

            # One final short settle before extracting HTML.
            page.wait_for_timeout(900)
            html = page.content()
            final_url = page.url
            body_text = page.evaluate("(document.body && document.body.innerText) ? document.body.innerText : ''")

            iframe_texts: List[str] = []
            iframe_html_chunks: List[str] = []
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    frame_url = frame.url
                    frame_body = frame.evaluate("(document.body && document.body.innerText) ? document.body.innerText : ''")
                    frame_html = frame.content()
                    if frame_body:
                        iframe_texts.append(str(frame_body))
                    if frame_html:
                        iframe_html_chunks.append(f"<!-- FRAME {frame_url} -->\n" + str(frame_html))
                except Exception:
                    continue

            if iframe_html_chunks:
                html = html + "\n<!-- BEGIN FRAME CONTENTS -->\n" + "\n".join(iframe_html_chunks)

            combined_body = str(body_text or "")
            if iframe_texts:
                combined_body += "\n" + "\n".join(iframe_texts)

            context.close()
            browser.close()

            if len((html or "").strip()) < 200:
                return None, final_url, f"page content too small after wait (text_len={last_len})"

            compact_body = re.sub(r"\s+", " ", combined_body).strip()
            low = compact_body.lower()
            stuck_loading = any(tok in low for tok in ["loading", "φορτ", "αναμον", "please wait", "παρακαλώ περιμένετε"])
            if len(compact_body) < min_text_chars or stuck_loading:
                return None, final_url, (
                    f"spa content not ready (text_len={len(compact_body)}, "
                    f"min_required={min_text_chars}, loading={stuck_loading})"
                )

            return html, final_url, None
    except Exception as exc:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        return None, None, f"playwright capture failed: {exc}"


def _html_to_clean_text(html: str, max_chars: int) -> str:
    soup = BeautifulSoup(html or "", "html.parser")

    for tag in soup(list(DECORATIVE_TAGS)):
        try:
            tag.decompose()
        except Exception:
            pass

    for tag in soup.find_all(LAYOUT_TAGS):
        try:
            tag.decompose()
        except Exception:
            pass

    for tag in soup.find_all(True):
        try:
            attrs = " ".join(
                str(value)
                for key, value in (tag.attrs or {}).items()
                if key in {"id", "class", "role", "aria-label", "data-testid"}
            )
            text_hint = " ".join(filter(None, [tag.name, attrs]))
            if COOKIE_HINT_RE.search(text_hint):
                tag.decompose()
                continue
            if tag.has_attr("hidden"):
                tag.decompose()
                continue
            aria_hidden = str(tag.attrs.get("aria-hidden", "")).strip().lower()
            if aria_hidden == "true":
                tag.decompose()
                continue
        except Exception:
            continue

    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def _schema_to_example(schema: Dict[str, Any]) -> Dict[str, Any]:
    example: Dict[str, Any] = {}
    for key, rule in (schema or {}).items():
        if isinstance(rule, dict):
            value_type = str(rule.get("type", "string")).lower()
        else:
            value_type = "string"

        if value_type == "boolean":
            example[key] = False
        elif value_type == "object":
            example[key] = {}
        elif value_type == "array":
            example[key] = []
        elif value_type == "number":
            example[key] = None
        else:
            example[key] = None
    return example


def _fetch_html_via_requests(url: str, timeout_sec: int = 15) -> Optional[str]:
    try:
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def _extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    if not raw_text:
        return None
    text = str(raw_text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    block = None
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced:
        block = fenced.group(1)
    if block is None:
        m = re.search(r"(\{[\s\S]*\})", text)
        if m:
            block = m.group(1)
    if block is None:
        return None

    try:
        parsed = json.loads(block)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _call_pollinations(prompt: str, timeout_sec: int = 45) -> Optional[str]:
    # Pollinations supports text generation over a URL-encoded prompt.
    compact = re.sub(r"\s+", " ", prompt).strip()
    max_prompt_chars = _safe_int("SCRAPER_AI_MAX_PROMPT_CHARS", 9000)
    compact = compact[:max_prompt_chars]
    encoded = quote(compact, safe="")
    endpoint = f"https://text.pollinations.ai/{encoded}"
    headers = {
        "User-Agent": "scanmydata-ai-fallback/1.0",
        "Accept": "text/plain, application/json;q=0.9, */*;q=0.8",
    }
    retries = max(1, _safe_int("SCRAPER_AI_PROVIDER_RETRIES", 2))
    for attempt in range(retries):
        try:
            resp = requests.get(endpoint, headers=headers, timeout=timeout_sec)
            if resp.status_code in {429, 500, 502, 503, 504}:
                if attempt < retries - 1:
                    time.sleep(1.0 + attempt)
                    continue
                return None
            resp.raise_for_status()
            return resp.text
        except Exception:
            if attempt < retries - 1:
                time.sleep(1.0 + attempt)
                continue
            return None
    return None


def _openai_payload_response(resp_json: Dict[str, Any]) -> Optional[str]:
    try:
        choices = resp_json.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] or {}
            message = first.get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                joined: List[str] = []
                for part in content:
                    if isinstance(part, dict):
                        txt = part.get("text")
                        if isinstance(txt, str) and txt.strip():
                            joined.append(txt)
                merged = "\n".join(joined).strip()
                if merged:
                    return merged
    except Exception:
        return None
    return None


def _call_openrouter(prompt: str, timeout_sec: int = 45) -> Optional[str]:
    api_key = str(os.getenv("OPENROUTER_API_KEY", "")).strip()
    if not api_key:
        return None

    model = str(
        os.getenv("SCRAPER_AI_OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    ).strip() or "meta-llama/llama-3.3-70b-instruct:free"
    compact = re.sub(r"\s+", " ", prompt).strip()[: _safe_int("SCRAPER_AI_MAX_PROMPT_CHARS", 9000)]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": compact}],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": str(os.getenv("SCRAPER_AI_OPENROUTER_REFERER", "https://scanmydata.local")).strip(),
        "X-Title": "scanmydata-ai-fallback",
    }
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        return _openai_payload_response(resp.json())
    except Exception:
        return None


def _call_deepinfra(prompt: str, timeout_sec: int = 45) -> Optional[str]:
    api_key = str(os.getenv("DEEPINFRA_API_KEY", "")).strip()
    if not api_key:
        return None

    model = str(os.getenv("SCRAPER_AI_DEEPINFRA_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct")).strip()
    if not model:
        model = "meta-llama/Meta-Llama-3.1-8B-Instruct"
    compact = re.sub(r"\s+", " ", prompt).strip()[: _safe_int("SCRAPER_AI_MAX_PROMPT_CHARS", 9000)]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": compact}],
        "temperature": 0,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        resp = requests.post(
            "https://api.deepinfra.com/v1/openai/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        return _openai_payload_response(resp.json())
    except Exception:
        return None


def _call_nerve(prompt: str, timeout_sec: int = 45) -> Optional[str]:
    nerve_url = str(os.getenv("NERVE_URL", "")).strip()
    if not nerve_url:
        return None

    compact = re.sub(r"\s+", " ", prompt).strip()[: _safe_int("SCRAPER_AI_MAX_PROMPT_CHARS", 9000)]
    payload = {
        "prompt": compact,
        "max_new_tokens": _safe_int("SCRAPER_AI_NERVE_MAX_NEW_TOKENS", 220),
    }
    headers = {"Content-Type": "application/json"}

    try:
        resp = requests.post(nerve_url, json=payload, headers=headers, timeout=timeout_sec)
        resp.raise_for_status()
        try:
            obj = resp.json()
        except Exception:
            return resp.text

        for key in ("text", "response", "output", "generated_text"):
            val = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(val, str) and val.strip():
                return val
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None


def _duckduckgo_get_vqd(session: Optional[requests.Session] = None, timeout_sec: int = 20) -> Optional[str]:
    headers = {
        "User-Agent": "scanmydata-ai-fallback/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://duckduckgo.com/",
    }
    try:
        if session is None:
            session = requests.Session()
        resp = session.get("https://duckduckgo.com/duckchat", headers=headers, timeout=timeout_sec)
        resp.raise_for_status()
        match = re.search(r'vqd\s*=\s*"([0-9\-]+)"', resp.text)
        if match:
            return match.group(1)
    except Exception:
        pass

    try:
        if session is None:
            session = requests.Session()
        resp = session.get(
            "https://duckduckgo.com/duckchat/v1/status",
            headers={
                "User-Agent": "scanmydata-ai-fallback/1.0",
                "Accept": "*/*",
                "Referer": "https://duckduckgo.com/",
                "x-vqd-accept": "1",
            },
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        token = str(resp.headers.get("x-vqd-4") or resp.headers.get("X-VQD-4") or "").strip()
        if token:
            return token
    except Exception:
        pass
    return None


def _extract_duckduckgo_stream_content(obj: dict[str, Any]) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in ("message", "text", "answer", "content"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    parts = []
    if "choices" in obj and isinstance(obj["choices"], list):
        for choice in obj["choices"]:
            if isinstance(choice, dict):
                msg = choice.get("message") or choice.get("text")
                if isinstance(msg, str) and msg.strip():
                    parts.append(msg.strip())
    return " ".join(parts).strip()


def _call_duckduckgo(prompt: str, timeout_sec: int = 45) -> Optional[str]:
    compact = re.sub(r"\s+", " ", prompt).strip()
    max_prompt_chars = _safe_int("SCRAPER_AI_MAX_PROMPT_CHARS", 9000)
    compact = compact[:max_prompt_chars]

    session = requests.Session()
    vqd = _duckduckgo_get_vqd(session=session, timeout_sec=min(timeout_sec, 20))
    if not vqd:
        return None

    model = str(os.getenv("SCRAPER_AI_DDG_MODEL", "gpt-4o-mini")).strip() or "gpt-4o-mini"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": compact}],
    }
    headers = {
        "User-Agent": "scanmydata-ai-fallback/1.0",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Referer": "https://duckduckgo.com/duckchat",
        "Origin": "https://duckduckgo.com",
        "X-Requested-With": "XMLHttpRequest",
        "x-vqd-4": vqd,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    try:
        resp = session.post(
            "https://duckduckgo.com/duckchat/v1/chat",
            headers=headers,
            json=payload,
            timeout=timeout_sec,
            stream=True,
        )
        resp.raise_for_status()

        parts: List[str] = []
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            text = str(line).strip()
            if not text.startswith("data:"):
                continue
            chunk = text[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            message = _extract_duckduckgo_stream_content(obj)
            if message:
                parts.append(message)

        merged = " ".join(parts).strip()
        if merged:
            return merged
    except Exception:
        pass

    # Legacy/simple style call (as used by older projects); keep as best-effort fallback.
    for legacy_payload in ({"question": compact}, {"messages": [{"role": "user", "content": compact}]}, {"input": compact}):
        try:
            legacy_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "scanmydata-ai-fallback/1.0",
                "Referer": "https://duckduckgo.com/duckchat",
                "Origin": "https://duckduckgo.com",
                "X-Requested-With": "XMLHttpRequest",
                "x-vqd-4": vqd,
            }
            resp = session.post(
                "https://duckduckgo.com/duckchat/v1/chat",
                json=legacy_payload,
                headers=legacy_headers,
                timeout=timeout_sec,
            )
            if resp.status_code >= 400:
                continue
            try:
                obj = resp.json()
                text = _extract_duckduckgo_stream_content(obj)
                if text:
                    return text
            except Exception:
                pass
            txt = str(resp.text or "").strip()
            if txt:
                return txt
        except Exception:
            continue
    return None


def _parse_provider_chain(provider: str) -> List[str]:
    raw = str(provider or "").strip().lower()
    if not raw:
        raw = DEFAULT_PROVIDER
    parts = [p.strip() for p in re.split(r"[,;|]", raw) if p.strip()]
    out: List[str] = []
    for p in parts:
        if p not in out:
            out.append(p)
    if not out:
        out = ["duckduckgo", "pollinations", "openrouter", "deepinfra", "nerve"]
    return out


def _call_ai_provider(prompt: str, provider: str, timeout_sec: int) -> Tuple[Optional[str], Optional[str]]:
    providers = _parse_provider_chain(provider)
    for current in providers:
        if current == "duckduckgo":
            text = _call_duckduckgo(prompt, timeout_sec=timeout_sec)
        elif current == "pollinations":
            text = _call_pollinations(prompt, timeout_sec=timeout_sec)
        elif current == "openrouter":
            text = _call_openrouter(prompt, timeout_sec=timeout_sec)
        elif current == "deepinfra":
            text = _call_deepinfra(prompt, timeout_sec=timeout_sec)
        elif current == "nerve":
            text = _call_nerve(prompt, timeout_sec=timeout_sec)
        else:
            text = None
        if text and str(text).strip():
            return text, current
    return None, None


def _build_prompt(url: str, final_url: str, schema: Dict[str, Any], page_text: str, error_hint: str = "") -> str:
    schema_obj = {}
    for key, rule in (schema or {}).items():
        if isinstance(rule, dict):
            schema_obj[key] = {
                "type": rule.get("type", "string"),
                "required": bool(rule.get("required", False)),
                "description": str(rule.get("description", "")).strip(),
            }
        else:
            schema_obj[key] = {
                "type": "string",
                "required": False,
                "description": str(rule),
            }

    instructions = {
        "task": "Extract document data from the provided page text.",
        "rules": [
            "Return ONLY a JSON object.",
            "Use exactly the schema keys.",
            "If unknown, use null.",
            "Do not add explanations or markdown.",
            "Keep numeric amounts as strings with comma decimal when visible in source.",
            "Set vat_analysis_inferred to true only when VAT rows are inferred from totals/text and not explicitly present.",
            "Preserve nested objects exactly as objects, not JSON-encoded strings.",
            "Ignore cookie banners, consent prompts, navigation text and decorative UI.",
        ],
        "source_url": url,
        "resolved_url": final_url or url,
        "error_hint": error_hint or "",
        "schema": schema_obj,
        "response_example": _schema_to_example(schema),
        "page_text": page_text,
    }
    return json.dumps(instructions, ensure_ascii=False)


def _coerce_value(value: Any, value_type: str) -> Any:
    t = str(value_type or "string").lower()
    if value is None:
        return None
    if t == "boolean":
        if isinstance(value, bool):
            return value
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "invoice"}:
            return True
        if raw in {"0", "false", "no", "receipt"}:
            return False
        return None
    if t == "number":
        try:
            return float(str(value).replace(",", "."))
        except Exception:
            return None
    if t == "object":
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
        return value if isinstance(value, dict) else None
    if t == "array":
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        return value if isinstance(value, list) else []
    return str(value).strip()


def _normalize_date_string(value: str) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    raw = re.sub(r"[\u2011\u2012\u2013\u2014]", "-", raw)
    raw = raw.replace("T", " ").replace(".00", "").replace("/", "/")
    raw = re.sub(r"\s+", " ", raw).strip()
    if " " in raw:
        raw = raw.split(" ")[0]
    # Normalize separators to slash for parsing
    raw = raw.replace("-", "/").replace(".", "/")
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d", "%Y/%m/%d", "%Y/%m/%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%d/%m/%Y")
        except Exception:
            continue
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", raw)
    if m:
        y, mo, d = m.groups()
        return f"{int(d):02d}/{int(mo):02d}/{int(y):04d}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if m:
        d, mo, y = m.groups()
        return f"{int(d):02d}/{int(mo):02d}/{int(y):04d}"
    return raw


def _normalize_amount_string(value: str) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    s = s.replace("€", "").replace("EUR", "").replace("eur", "").replace(" ", "").replace("\u00A0", "")
    s = re.sub(r"[^\d,\.\-]", "", s)
    if not s:
        return None
    if s.count(",") and s.count("."):
        if s.rfind(",") > s.rfind("."):
            parts = s.split(",")
            dec = parts[-1]
            integer = "".join(parts[:-1]).replace(".", "")
            s = integer + "," + dec
        else:
            parts = s.split(".")
            dec = parts[-1]
            integer = "".join(parts[:-1]).replace(",", "")
            s = integer + "," + dec
    elif s.count(".") and not s.count(","):
        s = s.replace(".", ",")
    if s.count(",") > 1:
        parts = s.split(",")
        dec = parts[-1]
        integer = "".join(parts[:-1])
        s = integer + "," + dec
    if s.startswith(","):
        s = s[1:]
    return s or None


def _normalize_vat_or_mark(value: str, key: str) -> Any:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if "vat" in key.lower():
        digits = re.sub(r"\D", "", raw)
        return digits or None
    if key.lower() == "mark":
        m = re.search(r"\d{15}", raw)
        return m.group(0) if m else None
    return raw


def _extract_first_amount(text: str) -> Optional[str]:
    if not text:
        return None
    matches = re.findall(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+[.,]\d{2}", text)
    return _normalize_amount_string(matches[-1]) if matches else None


def _extract_first_date(text: str) -> Optional[str]:
    if not text:
        return None
    match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b|\b\d{4}[/-]\d{2}[/-]\d{2}\b", text)
    return _normalize_date_string(match.group(0)) if match else None


def _extract_best_effort_fields(page_text: str, raw_html: Optional[str] = None) -> Dict[str, Any]:
    text = str(page_text or "")
    html = str(raw_html or "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    out: Dict[str, Any] = {}

    mark_match = re.search(r"\b\d{15}\b", text)
    if mark_match:
        out["MARK"] = mark_match.group(0)

    vat_match = re.search(r"\b(?:EL)?(\d{9})\b", text)
    if vat_match:
        out["issuer_vat"] = vat_match.group(1)

    date_value = _extract_first_date(text)
    if date_value:
        out["issue_date"] = date_value

    amount_value = _extract_first_amount(text)
    if amount_value:
        out["total_amount"] = amount_value

    aa_match = re.search(r"\bNo\s*([A-Z0-9\-]+)\b|\bΑ/?Α\s*[:#-]?\s*([A-Z0-9\-]+)\b", text, re.IGNORECASE)
    if aa_match:
        out["progressive_aa"] = next((group for group in aa_match.groups() if group), None)
    if not out.get("progressive_aa"):
        for idx, line in enumerate(lines):
            if re.search(r"\bno\b|α/?α|παραστατ", line, re.IGNORECASE):
                joined = " ".join(lines[idx: idx + 2])
                m = re.search(r"\b(\d{1,6})\b", joined)
                if m:
                    out["progressive_aa"] = m.group(1)
                    break

    series_match = re.search(r"\b(ALP|ΑΛΠ|APY|ΑΠΥ|TPI|ΤΠΙ|ΤΔΑ|ΤΠΥ)\b", text, re.IGNORECASE)
    if not series_match and html:
        series_match = re.search(r"\b(ALP|ΑΛΠ|APY|ΑΠΥ|TPI|ΤΠΙ|ΤΔΑ|ΤΠΥ)\b", html, re.IGNORECASE)
    if series_match:
        out["series"] = series_match.group(1).upper()

    issuer_match = re.search(
        r"(?:issuer|seller|company|εκδότη[ςσ]?|πωλητ[ήη]ς|επωνυμία)\s*[:#-]?\s*([^\n]{3,120})",
        text,
        re.IGNORECASE,
    )
    if issuer_match:
        issuer_name = re.sub(r"\s+", " ", issuer_match.group(1)).strip(" :-")
        if issuer_name and not re.search(r"https?://", issuer_name, re.IGNORECASE):
            out["issuer_name"] = issuer_name

    if html:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for td in soup.find_all("td"):
                td_text = td.get_text(" ", strip=True)
                if not re.search(r"\b(?:ΑΦΜ|VAT)\b|\b\d{9}\b", td_text, re.IGNORECASE):
                    continue

                strong_candidates = []
                for strong in td.find_all("strong"):
                    strong_text = str(strong.get_text(" ", strip=True)).strip()
                    if not strong_text:
                        continue
                    if strong_text.endswith(":"):
                        continue
                    if re.search(r"https?://|@", strong_text, re.IGNORECASE):
                        continue
                    if re.search(
                        r"(^|[^A-Za-z0-9Α-Ωα-ω_])(ΑΦΜ|VAT|EMAIL|TEL|PHONE|DOY|ΔΟΥ|ΤΚ|UID|AUTH|CODE|ΕΚΔΟΤ|ΟΝΟΜΑΤ|ΔΙΕΥΘ|ΠΑΡΟΧ|ΔΙΚΤ|WEB|SITE)([^A-Za-z0-9Α-Ωα-ω_]|$)",
                        strong_text,
                        re.IGNORECASE,
                    ):
                        continue
                    if len(strong_text) > 120:
                        continue
                    strong_candidates.append(strong_text)

                company_candidate = None
                if strong_candidates:
                    company_candidate = max(strong_candidates, key=lambda candidate: (candidate.count(" "), len(candidate)))

                if not company_candidate:
                    parts = re.split(r"\b(?:ΑΦΜ|VAT)\b", td_text, maxsplit=1, flags=re.IGNORECASE)
                    candidate = parts[0].strip()
                    candidate = re.sub(r"UID:.*|AUTH CODE:.*|CODE:.*|https?://\S+|Email:.*", "", candidate, flags=re.IGNORECASE).strip()
                    candidate = re.sub(r"\s{2,}", " ", candidate)
                    if candidate and len(candidate) <= 120:
                        company_candidate = candidate

                if company_candidate:
                    out["issuer_name"] = company_candidate
                    break
        except Exception:
            pass

    if not out.get("issuer_name"):
        for idx, line in enumerate(lines):
            if re.search(r"\b(?:ΑΦΜ|VAT)\b", line, re.IGNORECASE):
                candidate = lines[idx - 1] if idx > 0 else ""
                if candidate and not re.search(r"https?://|@|\b\d{9}\b", candidate, re.IGNORECASE):
                    out["issuer_name"] = candidate
                    break
    if not out.get("issuer_name"):
        for line in lines:
            if re.search(r"\b(IKE|AE|EPE|OE|EE|LTD|LLC|INC|PC|ΙΚΕ|ΑΕ|ΕΠΕ|ΟΕ|ΕΕ)\b", line, re.IGNORECASE):
                if not re.search(r"https?://|@", line, re.IGNORECASE):
                    out["issuer_name"] = line
                    break

    vat_rows: Dict[str, Dict[str, Optional[str]]] = {}
    for rate, first_amt, second_amt in re.findall(r"(\d{1,2})%\s+(\d+[.,]\d{2})\s+(\d+[.,]\d{2})", text):
        vat_amount = _normalize_amount_string(first_amt)
        net_amount = _normalize_amount_string(second_amt)
        gross_amount = None
        try:
            gross_value = float((net_amount or "0").replace(",", ".")) + float((vat_amount or "0").replace(",", "."))
            gross_amount = _normalize_amount_string(f"{gross_value:.2f}")
        except Exception:
            gross_amount = None
        vat_rows[str(rate)] = {
            "net_amount": net_amount,
            "vat_amount": vat_amount,
            "gross_amount": gross_amount,
        }
    if vat_rows:
        out["vat_analysis"] = vat_rows

    return out


def _normalize_fallback_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if key.lower() == "vat_analysis" and isinstance(value, dict):
        normalized_map: Dict[str, Any] = {}
        for rate_key, rate_value in value.items():
            rate_text = str(rate_key).strip().replace("%", "")
            rate_text = rate_text.replace(",", ".")
            if isinstance(rate_value, dict):
                normalized_map[rate_text] = {
                    "net_amount": _normalize_amount_string(rate_value.get("net_amount")),
                    "vat_amount": _normalize_amount_string(rate_value.get("vat_amount")),
                    "gross_amount": _normalize_amount_string(rate_value.get("gross_amount")),
                }
                continue
            normalized_map[rate_text] = {
                "net_amount": None,
                "vat_amount": _normalize_amount_string(rate_value),
                "gross_amount": None,
            }
        return normalized_map
    if not isinstance(value, str):
        return value
    key_l = key.lower()
    if key_l == "source":
        stripped = value.strip()
        if not stripped or re.match(r"https?://", stripped, re.IGNORECASE):
            return "ai_fallback"
        return stripped
    if key_l == "series":
        stripped = value.strip()
        if not stripped or not re.fullmatch(r"[A-Za-zΑ-Ωα-ω0-9._/-]{1,10}", stripped):
            return None
        return stripped
    if "date" in key_l:
        return _normalize_date_string(value)
    if "amount" in key_l or key_l in {"total", "gross", "net", "vat_amount", "gross_amount"}:
        return _normalize_amount_string(value)
    if key_l in {"issuer_vat", "vat", "seller_vat", "buyer_vat"} or key_l.endswith("_vat"):
        return _normalize_vat_or_mark(value, key)
    if key_l == "mark":
        return _normalize_vat_or_mark(value, key)
    return value.strip()


def _normalize_fallback_data(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in data.items():
        normalized[key] = _normalize_fallback_value(key, value)
    return normalized


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"none", "null", "n/a", "unknown"}:
            return False
        if "�" in cleaned:
            return False
        meaningful_chars = re.findall(r"[A-Za-zΑ-Ωα-ω0-9]", cleaned)
        return len(meaningful_chars) >= max(1, len(cleaned) // 5)
    if isinstance(value, dict):
        return any(_has_meaningful_value(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_meaningful_value(v) for v in value)
    return True


def _score_fallback_result(data: Dict[str, Any], schema: Dict[str, Any]) -> int:
    score = 0
    for key in (schema or {}).keys():
        value = data.get(key)
        if not _has_meaningful_value(value):
            continue
        score += 3 if key in {"MARK", "issuer_vat", "issuer_name", "issue_date", "total_amount"} else 1
        if key == "vat_analysis" and isinstance(value, dict):
            for vat_row in value.values():
                if isinstance(vat_row, dict):
                    score += sum(1 for nested in vat_row.values() if _has_meaningful_value(nested))
    return score


def _is_low_signal_result(data: Dict[str, Any], schema: Dict[str, Any]) -> bool:
    return _score_fallback_result(data, schema) <= 0


def _validate_and_fill(parsed: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, rule in (schema or {}).items():
        if isinstance(rule, dict):
            value_type = str(rule.get("type", "string"))
            default = rule.get("default", None)
        else:
            value_type = "string"
            default = None
        value = parsed.get(key, default)
        coerced = _coerce_value(value, value_type)
        if coerced is None and default is not None:
            coerced = default
        out[key] = coerced
    return _normalize_fallback_data(out)


def _supplement_from_page_text(data: Dict[str, Any], page_text: str, schema: Dict[str, Any], raw_html: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data
    best_effort = _extract_best_effort_fields(page_text, raw_html)
    for key in (schema or {}).keys():
        current = data.get(key)
        if key == "series":
            inferred_text = str(best_effort.get("series") or "").strip()
            if not _has_meaningful_value(current) and inferred_text:
                data[key] = inferred_text
            elif inferred_text and not _has_meaningful_value(current):
                data[key] = inferred_text
            elif not inferred_text:
                data[key] = None
            continue
        if _has_meaningful_value(current):
            if key == "vat_analysis" and isinstance(current, dict):
                repaired: Dict[str, Any] = {}
                source_map = best_effort.get("vat_analysis") if isinstance(best_effort.get("vat_analysis"), dict) else {}
                for rate_key, vat_row in current.items():
                    if not isinstance(vat_row, dict):
                        repaired[rate_key] = vat_row
                        continue
                    merged_row = dict(vat_row)
                    inferred_row = source_map.get(rate_key) if isinstance(source_map, dict) else None
                    if isinstance(inferred_row, dict):
                        current_complete = all(
                            _has_meaningful_value(merged_row.get(amount_key))
                            for amount_key in ("net_amount", "vat_amount", "gross_amount")
                        )
                        inferred_complete = all(
                            _has_meaningful_value(inferred_row.get(amount_key))
                            for amount_key in ("net_amount", "vat_amount", "gross_amount")
                        )
                        if inferred_complete and not current_complete:
                            repaired[rate_key] = dict(inferred_row)
                            continue
                        for amount_key in ("net_amount", "vat_amount", "gross_amount"):
                            if not _has_meaningful_value(merged_row.get(amount_key)):
                                merged_row[amount_key] = inferred_row.get(amount_key)
                    repaired[rate_key] = merged_row
                data[key] = repaired
            continue
        if key in best_effort:
            data[key] = best_effort[key]
    series_text = str(data.get("series") or best_effort.get("series") or "").strip().upper()
    doc_type_text = str(data.get("doc_type") or "").strip().lower()
    if series_text in {"ALP", "ΑΛΠ", "APY", "ΑΠΥ"}:
        data["is_invoice"] = False
        if not doc_type_text or doc_type_text == "invoice":
            data["doc_type"] = "receipt"
    if "source" in data:
        data["source"] = "ai_fallback"
    if best_effort.get("vat_analysis") and not _has_meaningful_value(data.get("vat_analysis")):
        data["vat_analysis_inferred"] = True
    return _normalize_fallback_data(data)


def run_schema_ai_fallback(
    url: str,
    schema: Dict[str, Any],
    *,
    debug: bool = False,
    timeout_sec: int = 25,
    error_hint: str = "",
    include_metadata: bool = True,
) -> Optional[Dict[str, Any]]:
    """Run AI fallback extraction.

    Returns dict with schema keys and optional metadata when successful, else None.
    """
    if not is_ai_fallback_enabled():
        return None

    html, final_url, capture_error = _capture_rendered_html(url, timeout_sec=timeout_sec)
    if not html:
        # Playwright/Chromium is frequently unavailable on the server (Render free
        # tier, CI containers), which previously aborted the whole fallback. Fall
        # back to a plain requests fetch so the AI providers AND the built-in
        # heuristic extractor still get page text to work with.
        if debug:
            print("ai fallback browser capture failed, trying requests fetch:", capture_error)
        html = _fetch_html_via_requests(url, timeout_sec=min(20, max(10, timeout_sec)))
        final_url = url
        if not html:
            if debug:
                print("ai fallback requests fetch also failed")
            return None

    max_html_chars = _safe_int("SCRAPER_AI_MAX_TEXT_CHARS", 24000)
    page_text = _html_to_clean_text(html, max_chars=max_html_chars)
    if not page_text:
        return None

    raw_html = html
    if page_text.count("�") > 5 or re.search(r"No\s*���", page_text):
        fallback_html = _fetch_html_via_requests(final_url or url, timeout_sec=min(15, timeout_sec))
        if fallback_html:
            raw_html = fallback_html

    provider = str(
        os.getenv("SCRAPER_AI_PROVIDER_CHAIN")
        or os.getenv("SCRAPER_AI_PROVIDER")
        or DEFAULT_PROVIDER
    ).strip().lower() or DEFAULT_PROVIDER
    ai_timeout = _safe_int("SCRAPER_AI_PROVIDER_TIMEOUT", 45)
    content_retries = max(1, _safe_int("SCRAPER_AI_CONTENT_RETRIES", 2))
    best_data: Optional[Dict[str, Any]] = None
    best_provider: Optional[str] = None
    best_score = -1

    for attempt in range(content_retries):
        attempt_hint = error_hint
        if attempt > 0:
            suffix = "previous response was missing schema fields; use only evidence from page text"
            attempt_hint = f"{error_hint}; {suffix}" if error_hint else suffix

        prompt = _build_prompt(url, final_url or url, schema, page_text, error_hint=attempt_hint)
        response_text, used_provider = _call_ai_provider(prompt, provider=provider, timeout_sec=ai_timeout)
        parsed = _extract_json_object(response_text or "")
        if not isinstance(parsed, dict):
            if debug:
                print(f"ai fallback parse failed on attempt {attempt + 1}")
            continue

        data = _validate_and_fill(parsed, schema)
        data = _supplement_from_page_text(data, page_text, schema, raw_html=raw_html)
        score = _score_fallback_result(data, schema)
        if score > best_score:
            best_data = data
            best_provider = used_provider or provider
            best_score = score
        if not _is_low_signal_result(data, schema):
            break

    if not isinstance(best_data, dict) or _is_low_signal_result(best_data, schema):
        heuristic_data = _supplement_from_page_text(_validate_and_fill({}, schema), page_text, schema, raw_html=raw_html)
        heuristic_score = _score_fallback_result(heuristic_data, schema)
        if heuristic_score > best_score:
            best_data = heuristic_data
            best_provider = "heuristic"
            best_score = heuristic_score

    if not isinstance(best_data, dict) or _is_low_signal_result(best_data, schema):
        return None

    data = best_data
    if best_provider == "heuristic":
        data["vat_analysis_inferred"] = True
    if include_metadata:
        data["_ai_fallback_used"] = True
        data["_ai_fallback_provider"] = best_provider or provider
        data["_ai_fallback_source_url"] = final_url or url
    return data


def _load_schema_factory(ref: str):
    raw = str(ref or "").strip()
    if not raw:
        raise ValueError("schema reference is required")

    module_name, sep, attr_name = raw.partition(":")
    if not sep:
        raise ValueError("schema reference must look like module:function")

    module = importlib.import_module(module_name)
    factory = getattr(module, attr_name, None)
    if factory is None or not callable(factory):
        raise ValueError(f"schema factory not found: {raw}")
    return factory


def _main(argv: List[str]) -> int:
    args = list(argv or [])
    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage: python scraper_ai_fallback.py <url> <module:function> [--debug] [--timeout N] [--no-metadata]",
            file=sys.stderr,
        )
        return 2

    url = str(args.pop(0)).strip()
    schema_ref = str(args.pop(0)).strip() if args else ""
    timeout_sec = 25
    debug = False
    include_metadata = True

    idx = 0
    while idx < len(args):
        current = args[idx]
        if current == "--debug":
            debug = True
        elif current == "--no-metadata":
            include_metadata = False
        elif current == "--timeout" and idx + 1 < len(args):
            idx += 1
            try:
                timeout_sec = int(str(args[idx]).strip())
            except Exception:
                pass
        idx += 1

    try:
        schema_factory = _load_schema_factory(schema_ref)
        schema = schema_factory()
    except Exception as exc:
        print(f"schema load failed: {exc}", file=sys.stderr)
        return 1

    os.environ.setdefault("SCRAPER_AI_FALLBACK_ENABLED", "1")
    os.environ.setdefault("SCRAPER_AI_PROVIDER_CHAIN", "duckduckgo,pollinations")

    result = run_schema_ai_fallback(
        url,
        schema,
        debug=debug,
        timeout_sec=timeout_sec,
        error_hint=f"cli simulation via {schema_ref}",
        include_metadata=include_metadata,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if isinstance(result, dict) else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
