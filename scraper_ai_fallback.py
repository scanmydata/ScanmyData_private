#!/usr/bin/env python3
"""Shared AI fallback for scraper modules.

Flow:
1) Load target URL with Playwright and capture rendered HTML.
2) Clean HTML into compact text.
3) Ask a free-text model provider for strict JSON matching a schema.
4) Return validated dict payload, or None when fallback is unavailable.

This module is intentionally opt-in via environment flags.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

DEFAULT_PROVIDER = "duckduckgo,pollinations,openrouter,deepinfra,nerve"


def is_ai_fallback_enabled() -> bool:
    raw = str(os.getenv("SCRAPER_AI_FALLBACK_ENABLED", "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


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
    for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
        try:
            tag.decompose()
        except Exception:
            pass
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


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
        ],
        "source_url": url,
        "resolved_url": final_url or url,
        "error_hint": error_hint or "",
        "schema": schema_obj,
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
        return value if isinstance(value, dict) else None
    if t == "array":
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
        return m.group(0) if m else raw
    return raw


def _normalize_fallback_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value
    key_l = key.lower()
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
        if debug:
            print("ai fallback capture failed:", capture_error)
        return None

    max_html_chars = _safe_int("SCRAPER_AI_MAX_TEXT_CHARS", 24000)
    page_text = _html_to_clean_text(html, max_chars=max_html_chars)
    if not page_text:
        return None

    provider = str(
        os.getenv("SCRAPER_AI_PROVIDER_CHAIN")
        or os.getenv("SCRAPER_AI_PROVIDER")
        or DEFAULT_PROVIDER
    ).strip().lower() or DEFAULT_PROVIDER
    ai_timeout = _safe_int("SCRAPER_AI_PROVIDER_TIMEOUT", 45)
    prompt = _build_prompt(url, final_url or url, schema, page_text, error_hint=error_hint)
    response_text, used_provider = _call_ai_provider(prompt, provider=provider, timeout_sec=ai_timeout)
    parsed = _extract_json_object(response_text or "")
    if not isinstance(parsed, dict):
        if debug:
            print("ai fallback parse failed")
        return None

    data = _validate_and_fill(parsed, schema)
    if include_metadata:
        data["_ai_fallback_used"] = True
        data["_ai_fallback_provider"] = used_provider or provider
        data["_ai_fallback_source_url"] = final_url or url
    return data
