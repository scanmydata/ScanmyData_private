import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET
from posixpath import normpath as posix_normpath

import pandas as pd

from vat_validator import validate_greek_vat

try:
    from .fetch_business_partners import BusinessPortalFetcher
    from .fetch_e3 import fetch_e3_entries
except Exception:
    from fetch_business_partners import BusinessPortalFetcher
    from fetch_e3 import fetch_e3_entries


_GREEK_INDIVIDUAL_LEGAL_TYPE_TOKENS = (
    "ΑΤΟΜ",
    "ΑΤΟΜΙΚ",
    "INDIVIDUAL",
    "SOLE",
)

_STRICT_HEADER_ALIASES = {
    "afm": {"αφμ", "afm", "vat", "vat number", "φορολογικο μητρωο"},
    "name": {"επωνυμια", "επωνυμία", "name", "πελατης", "πελάτης", "εταιρια", "εταιρεία"},
    "taxis_user": {
        "ονομα χρηστη taxis",
        "όνομα χρήστη taxis",
        "username taxis",
        "taxis username",
        "taxisnet username",
        "ονομα χρηστη taxisnet",
        "όνομα χρήστη taxisnet",
        "username",
    },
    "taxis_password": {
        "κωδικος taxis",
        "κωδικός taxis",
        "taxis password",
        "taxisnet password",
        "κωδικος taxisnet",
        "κωδικός taxisnet",
        "password",
    },
    "mydata_user": {
        "ονομα χρηστη mydata",
        "όνομα χρήστη mydata",
        "mydata user",
        "aade user",
        "aade-user-id",
    },
    "mydata_key": {
        "subscription key mydata",
        "subscription key",
        "mydata key",
        "ocp-apim-subscription-key",
    },
    "amka": {"αμκα", "amka"},
    "e3_585_007": {"e3_585_007", "585.007", "585_007"},
    "e3_585_014": {"e3_585_014", "585.014", "585_014"},
    "address": {"διευθυνση", "διεύθυνση", "address"},
    "legal_type": {"νομική μορφή", "νομικη μορφη", "legal type", "legal_type", "νομική", "νομικη"},
}


@dataclass
class PersonTarget:
    afm: str
    full_name: str
    amka: str
    taxisnet_username: str
    taxisnet_password: str


class E3BrainError(Exception):
    pass


def _norm_text(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.replace("\u00a0", " ").split())


def _norm_afm(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _to_float(value: Any) -> float:
    text = _norm_text(value)
    if not text:
        return 0.0
    text = text.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(text)
    except Exception:
        return 0.0


def _parse_date(value: Any) -> Optional[date]:
    text = _norm_text(value)
    if not text:
        return None

    fmts = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%Y.%m.%d",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass

    m = re.search(r"(\d{4})", text)
    if m:
        try:
            return date(int(m.group(1)), 12, 31)
        except Exception:
            return None
    return None


def _xlsx_col_to_index(col_ref: str) -> int:
    out = 0
    for ch in col_ref:
        if not ch.isalpha():
            break
        out = out * 26 + (ord(ch.upper()) - ord("A") + 1)
    return max(out - 1, 0)


def _parse_amount_candidates(text: str) -> List[float]:
    vals = re.findall(r"\d{1,3}(?:[\.\s]\d{3})*(?:,\d{2})", _norm_text(text))
    return [round(_to_float(v), 2) for v in vals if _to_float(v) > 0]


def _is_active_on(ref_date: date, dt_from: Any, dt_to: Any) -> bool:
    d_from = _parse_date(dt_from)
    d_to = _parse_date(dt_to)
    if d_from and ref_date < d_from:
        return False
    if d_to and ref_date > d_to:
        return False
    return True


def _is_active_between(range_start: date, range_end: date, dt_from: Any, dt_to: Any) -> bool:
    """Return True if the member's active interval intersects [range_start, range_end].

    dt_from/dt_to can be various string formats; we parse them with _parse_date.
    If dt_from is missing, treat as -infinity; if dt_to is missing, treat as +infinity.
    """
    from_date = _parse_date(dt_from)
    to_date = _parse_date(dt_to)

    # If no range provided, default to checking against today
    if not range_start and not range_end:
        try:
            from datetime import date as _date
            today = _date.today()
        except Exception:
            today = None
        return _is_active_on(today, dt_from, dt_to) if today else False

    # Normalize missing endpoints
    rs = range_start
    re = range_end

    # Member interval: [from_date or -inf, to_date or +inf]
    m_start = from_date or date.min
    m_end = to_date or date.max

    # Overlap exists if m_start <= re and m_end >= rs
    return (m_start <= re) and (m_end >= rs)


def _normalize_address(value: str) -> str:
    value = _norm_text(value).lower()
    value = re.sub(r"[^\w\d\sάέήίόύώϊϋΐΰα-ω]", " ", value)
    return " ".join(value.split())


def _normalize_role(value: Any) -> str:
    """Normalize various role abbreviations to canonical Greek phrases.

    Examples:
    - Ο.Μ., ΟΜ, ομ -> 'Ομόρρυθμο Μέλος'
    - Ε.Μ., ΕΕ, εε -> 'Ετερόρρυθμο Μέλος'
    - ΔΙΑΧΕΙΡΙΣΤΗΣ -> 'Διαχειριστής'
    - ΝΟΜΙΜΟΣ ΕΚΠΡΟΣΩΠΟΣ -> 'Νόμιμος Εκπρόσωπος'
    """
    raw = _norm_text(value)
    if not raw:
        return ""

    up = raw.upper()
    up_plain = "".join(ch for ch in unicodedata.normalize("NFD", up) if not unicodedata.combining(ch))
    # simple token without punctuation/spaces for easy matching
    token = re.sub(r"[^\wΑ-Ωα-ω0-9]", "", up_plain).lower()

    # Full role phrases coming from business registry
    if (("ΟΜΟΡΡΥΘ" in up_plain) or ("ΟΜΜΟΡΡΥΘ" in up_plain)) and "ΜΕΛ" in up_plain:
        return "ομορυθμο μελος"
    if "ΕΤΕΡΟΡΡΥΘ" in up_plain and "ΜΕΛ" in up_plain:
        return "ετερορυθμο μελος"

    # Ο.Μ. / ΟΜ / ομ -> ομορυθμο μελος
    if re.search(r"\bΟ\.?Μ\.?\b", up) or token == "ομ":
        return "ομορυθμο μελος"

    # Ε.Μ. / ΕΕ / εε / Ε.Ε. -> ετερορυθμο μελος
    if re.search(r"\bΕ\.?Μ\.?\b", up) or re.search(r"\bΕ\.?Ε\.?\b", up) or token in ("εε", "εμ"):
        return "ετερορυθμο μελος"

    if "ΔΙΑΧΕΙΡΙΣΤΗΣ" in up:
        return "διαχειριστης"

    # Match various spellings/abbreviations for 'Νόμιμος Εκπρόσωπος'
    if "ΝΟΜΙΜΟΣ" in up and ("ΕΚΠΡΟΣΩΠΟΣ" in up or "ΕΚΠΡ" in up or "ΕΚΠΡΟΣ" in up):
        return "νομιμος εκπροσωπος"

    # Some sources may use shortened 'ΔΙΑΧΕΙΡ' token
    if "ΔΙΑΧΕΙΡ" in up_plain or "ΔΙΑΧ" in up_plain:
        return "διαχειριστης"

    # No canonical mapping found
    # Final fallback: loose substring matches to catch malformed tokens
    if "ΟΜΟ" in up_plain and "ΜΕΛ" in up_plain:
        return "ομορυθμο μελος"
    if "ΕΤΕΡΟ" in up_plain and "ΜΕΛ" in up_plain:
        return "ετερορυθμο μελος"
    return ""


def _format_role_for_output(value: Any) -> str:
    norm = _normalize_role(value)
    mapping = {
        "ομορυθμο μελος": "Ομόρρυθμο Μέλος",
        "ετερορυθμο μελος": "Ετερόρρυθμο Μέλος",
        "διαχειριστης": "Διαχειριστής",
        "νομιμος εκπροσωπος": "Νόμιμος Εκπρόσωπος",
    }
    if norm:
        return mapping.get(norm, _norm_text(value))
    return _norm_text(value)


def _is_role_token(value: Any) -> bool:
    v = _norm_text(value)
    if not v:
        return False
    norm = _normalize_role(v)
    return norm in ("ομορυθμο μελος", "ετερορυθμο μελος", "διαχειριστης", "νομιμος εκπροσωπος")


def _looks_like_name(value: Any) -> bool:
    v = _norm_text(value)
    if not v:
        return False
    # reject pure numeric, AFM, or short tokens
    if re.fullmatch(r"\d{9}", v) or re.fullmatch(r"\d{1,3}", v) or _parse_date(v):
        return False
    # must contain alphabetic characters and at least one space (first+last)
    if not re.search(r"[Α-Ωα-ωA-Za-z]", v):
        return False
    if " " not in v:
        # single token names possible, but prefer multi-token
        return len(v) > 3
    # ensure it's not a role token
    if _is_role_token(v):
        return False
    return True


def _pretty_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _extract_company_summary(company_payload: Dict[str, Any]) -> Dict[str, Any]:
    company = company_payload.get("company") if isinstance(company_payload.get("company"), dict) else company_payload
    # legalType can sometimes be a dict/object returned from external services
    # (e.g. {"id": 2, "descr": "ΟΕ"}). Prefer human-friendly fields
    # such as 'descr', 'description', 'label' or 'name' when present.
    raw_legal = (
        company.get("legalType")
        or company.get("legalTypeLabel")
        or company.get("coLegalType")
        or company.get("coLegalTypeLabel")
        or company.get("legalForm")
        or ""
    )

    if isinstance(raw_legal, dict):
        # prefer common descriptive keys
        legal_type = ""
        for key in ("descr", "description", "label", "name", "value"):
            if raw_legal.get(key):
                legal_type = _norm_text(raw_legal.get(key))
                break
        if not legal_type:
            # fallback: join non-empty values
            vals = [str(v).strip() for v in raw_legal.values() if v is not None and str(v).strip()]
            legal_type = " ".join(vals).strip()
            legal_type = _norm_text(legal_type)
    else:
        legal_type = _norm_text(raw_legal)

    city = _norm_text(company.get("city") or company.get("coCity") or "")
    street = _norm_text(company.get("street") or company.get("coStreet") or "")
    street_no = _norm_text(company.get("streetNumber") or company.get("coStreetNumber") or "")
    zip_code = _norm_text(company.get("zipCode") or company.get("coZipCode") or "")

    return {
        "legal_type": legal_type,
        "city": city,
        "street": street,
        "street_number": street_no,
        "zip_code": zip_code,
        "headquarter_address": " ".join(x for x in [street, street_no, city, zip_code] if x).strip(),
    }


def _is_individual_business(legal_type: str) -> bool:
    u = legal_type.upper()
    return any(tok in u for tok in _GREEK_INDIVIDUAL_LEGAL_TYPE_TOKENS)


def _extract_active_members(
    partners_result: Dict[str, Any],
    ref_date: Optional[date] = None,
    range_start: Optional[date] = None,
    range_end: Optional[date] = None,
) -> List[Dict[str, Any]]:
    partners = partners_result.get("partners") if isinstance(partners_result, dict) else []
    if not isinstance(partners, list):
        return []

    members: List[Dict[str, Any]] = []
    for p in partners:
        if not isinstance(p, dict):
            continue
        p_dt_from = p.get("dtFrom")
        p_dt_to = p.get("dtTo")
        if range_start and range_end:
            if not _is_active_between(range_start, range_end, p_dt_from, p_dt_to):
                continue
        else:
            check_date = ref_date or date.today()
            if not _is_active_on(check_date, p_dt_from, p_dt_to):
                continue

        name = _norm_text(p.get("personName") or p.get("businessName") or "")
        afm = _norm_afm(p.get("vat") or p.get("afm") or p.get("personAfm") or "")
        members.append(
            {
                "name": name,
                "afm": afm,
                "role": _format_role_for_output(p.get("role") or ""),
                "dt_from": _norm_text(p.get("dtFrom") or ""),
                "dt_to": _norm_text(p.get("dtTo") or ""),
            }
        )
    return members


def _extract_company_info_members(company_info_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    members_raw = company_info_payload.get("members")
    if isinstance(members_raw, list):
        out = []
        for m in members_raw:
            if not isinstance(m, dict):
                continue
            out.append(
                {
                    "name": _norm_text(m.get("name") or m.get("full_name") or m.get("personName") or ""),
                    "afm": _norm_afm(m.get("afm") or m.get("AFM") or ""),
                    "dt_from": _norm_text(m.get("dt_from") or m.get("dtFrom") or m.get("start") or ""),
                    "dt_to": _norm_text(m.get("dt_to") or m.get("dtTo") or m.get("end") or ""),
                    # Use formatted role with proper capitalization and accents
                    "role": _format_role_for_output(m.get("role") or m.get("position") or ""),
                    "percentage": _norm_text(m.get("percentage") or m.get("percent") or ""),
                }
            )
        return out

    # Best-effort parse from extracted registry tables of company_info.py
    result: List[Dict[str, Any]] = []
    tables = company_info_payload.get("registryTables") or company_info_payload.get("tables")

    def _is_member_header_row(row: List[Any]) -> bool:
        vals = [(_normalize_address(str(v)) if v is not None else "") for v in row if _norm_text(v)]
        if not vals:
            return False
        has_afm = any("αφμ" in v or "afm" in v for v in vals)
        has_name = any("επων" in v or "ονομα" in v or "name" in v for v in vals)
        # Prefer tables that include both AFM and a name column; don't require date/role tokens
        return has_afm and has_name

    # Prefer explicit member table named like 'ΑΦΜ μέλους' — use only that table if found
    if isinstance(tables, list):
        member_table = None
        member_header_idx = -1
        for t in tables:
            rows = t.get("rows") if isinstance(t, dict) else None
            if not isinstance(rows, list) or not rows:
                continue

            # scan first few rows for the explicit header containing 'αφμ' and 'μελ' (μέλους)
            found = False
            for i, row in enumerate(rows[:6]):
                if not isinstance(row, list):
                    continue
                for cell in row:
                    if not _norm_text(cell):
                        continue
                    norm = _normalize_address(str(cell))
                    if "αφμ" in norm and ("μελ" in norm or "μέλ" in norm or "μελους" in norm or "μελών" in norm):
                        # prefer a following row that contains separated header columns
                        preferred_idx = -1
                        for j in range(i, min(i + 8, len(rows))):
                            r2 = rows[j]
                            if not isinstance(r2, list):
                                continue
                            # skip rows that include data tokens
                            if any(re.search(r"\d{9}", str(c)) for c in r2) or any(_parse_date(c) for c in r2 if _norm_text(c)):
                                continue
                            # detect separate AFM + name tokens across columns
                            afm_inds = [idx for idx, cell2 in enumerate(r2) if _norm_text(cell2) and ("αφμ" in _normalize_address(str(cell2)) or "afm" in _normalize_address(str(cell2)))]
                            name_inds = [idx for idx, cell2 in enumerate(r2) if _norm_text(cell2) and any(tok in _normalize_address(str(cell2)) for tok in ("επων", "επωνυ", "ονομα", "name"))]
                            if afm_inds and name_inds and any(ai != ni for ai in afm_inds for ni in name_inds):
                                preferred_idx = j
                                break

                        member_table = t
                        member_header_idx = preferred_idx if preferred_idx >= 0 else i
                        found = True
                        break
                if found:
                    break
            if member_table:
                break

        if member_table:
            rows = member_table.get("rows") if isinstance(member_table, dict) else None
            if isinstance(rows, list) and rows:
                # Prefer a header row where header tokens appear across multiple columns
                header_idx = -1
                for i, row in enumerate(rows):
                    if not isinstance(row, list):
                        continue
                    # skip rows that already contain data tokens (AFM or dates) — likely a concatenated cell
                    if any(re.search(r"\d{9}", str(c)) for c in row) or any(_parse_date(c) for c in row if _norm_text(c)):
                        continue
                    # detect AFM and name tokens in distinct columns (avoid single-cell concatenated headers)
                    afm_inds = [idx for idx, cell in enumerate(row) if _norm_text(cell) and ("αφμ" in _normalize_address(str(cell)) or "afm" in _normalize_address(str(cell)))]
                    name_inds = [idx for idx, cell in enumerate(row) if _norm_text(cell) and any(tok in _normalize_address(str(cell)) for tok in ("επων", "επωνυ", "ονομα", "name"))]
                    if afm_inds and name_inds and any(ai != ni for ai in afm_inds for ni in name_inds):
                        # ensure there are data rows with AFM after this header
                        has_data = False
                        for rr in rows[i + 1 : i + 8]:
                            if not isinstance(rr, list):
                                continue
                            if any(re.search(r"\d{9}", str(c)) for c in rr):
                                has_data = True
                                break
                        if has_data:
                            header_idx = i
                            break

                if header_idx < 0:
                    header_idx = member_header_idx if member_header_idx >= 0 else 0

                # helper: detect header-like rows to skip
                def _row_looks_like_header(r: List[Any]) -> bool:
                    if not isinstance(r, list):
                        return False
                    norms = [_normalize_address(str(c)) for c in r if _norm_text(c)]
                    return any("αφμ" in n or "επων" in n or "ημ" in n or "ποσο" in n for n in norms)

                # helper: split a single concatenated cell into logical rows
                def _split_embedded_rows(cell: str) -> List[List[str]]:
                    out: List[List[str]] = []
                    if not cell:
                        return out
                    lines = [ln.strip() for ln in re.split(r"[\r\n]+", cell) if ln.strip()]
                    for ln in lines:
                        # prefer tab-separated parts
                        parts = [p for p in re.split(r"\t+", ln) if p and p.strip()]
                        if len(parts) <= 1:
                            # fallback: split on multiple spaces
                            parts = [p for p in re.split(r"\s{2,}", ln) if p and p.strip()]
                        if parts:
                            out.append(parts)
                    return out

                # infer fields from a list of cell-like tokens
                def _infer_fields_from_cells(cells: List[str]) -> Dict[str, Any]:
                    cells = [str(c).strip() for c in cells if str(c).strip()]
                    afm_val = ""
                    name_val = ""
                    dt_from_val = ""
                    dt_to_val = ""
                    role_val = ""
                    perc_val = ""

                    # try AFM first and remember its index for positional heuristics
                    afm_idx = None
                    for i, c in enumerate(cells):
                        m = re.search(r"(\d{9})", c)
                        if m:
                            afm_val = _norm_afm(m.group(1))
                            afm_idx = i
                            break

                    # collect date-like tokens; prefer positional dates relative to AFM if available
                    date_tokens = [c for c in cells if _parse_date(c)]
                    if afm_idx is not None:
                        # common layout: AFM, NAME, DT_FROM, DT_TO, ROLE, PERC
                        # try to pick dates based on positions after AFM
                        cand_from = None
                        cand_to = None
                        if afm_idx + 2 < len(cells):
                            cand = cells[afm_idx + 2]
                            if _parse_date(cand):
                                cand_from = cand
                        if afm_idx + 3 < len(cells):
                            cand = cells[afm_idx + 3]
                            if _parse_date(cand):
                                cand_to = cand
                        if cand_from:
                            dt_from_val = cand_from
                            if cand_to:
                                dt_to_val = cand_to
                        elif date_tokens:
                            dt_from_val = date_tokens[0]
                            if len(date_tokens) > 1:
                                dt_to_val = date_tokens[1]
                    else:
                        if date_tokens:
                            dt_from_val = date_tokens[0]
                            if len(date_tokens) > 1:
                                dt_to_val = date_tokens[1]

                    # percentage: first small integer token
                    for c in cells[::-1]:
                        if re.fullmatch(r"\d{1,3}", c):
                            perc_val = c
                            break

                    # role: detect only known/expected role tokens (use canonicalizer)
                    for c in cells:
                        norm_role = _normalize_role(c)
                        if norm_role:
                            role_val = norm_role
                            break

                    # name: choose the first token that looks like a personal/full name
                    for c in cells:
                        if c == afm_val:
                            continue
                        if _normalize_role(c) or _parse_date(c) or re.fullmatch(r"\d{1,3}", c):
                            continue
                        if _looks_like_name(c):
                            name_val = c
                            break

                    # fallback: pick any token that looks name-ish or second token
                    if not name_val:
                        for c in cells:
                            if c == afm_val:
                                continue
                            if _parse_date(c) or re.fullmatch(r"\d{1,3}", c):
                                continue
                            if not _normalize_role(c):
                                name_val = c
                                break
                    if not name_val and len(cells) >= 2:
                        name_val = cells[1]

                    return {
                        "afm": afm_val,
                        "name": _norm_text(name_val),
                        "dt_from": dt_from_val,
                        "dt_to": dt_to_val,
                        "role": role_val,
                        "percentage": perc_val,
                    }

                header_row = rows[header_idx]
                col_map: Dict[str, int] = {}
                for j, cell in enumerate(header_row):
                    n = _normalize_address(str(cell))
                    if not n:
                        continue
                    if "αφμ" in n or "afm" in n:
                        col_map["afm"] = j
                    elif any(tok in n for tok in ("επωνυ", "επωνυμια", "επων", "ονομα", "name")):
                        col_map["name"] = j
                    elif any(tok in n for tok in ("έναρ", "έναρξη", "έναρξης", "start")):
                        col_map.setdefault("dt_from", j)
                    elif any(tok in n for tok in ("διακοπ", "διακοπή", "λήξη", "ληξη", "end")):
                        col_map["dt_to"] = j
                    elif any(tok in n for tok in ("ειδο", "συμμετο", "σχέσ", "σχέση")):
                        col_map.setdefault("role", j)
                    elif "ποσο" in n:
                        col_map["percentage"] = j

                members_by_key: Dict[str, Dict[str, Any]] = {}
                for data_row in rows[header_idx + 1 :]:
                    if not isinstance(data_row, list):
                        continue
                    if not any(_norm_text(c) for c in data_row):
                        continue

                    # skip repeated header-like rows
                    if _row_looks_like_header(data_row):
                        continue

                    # handle concatenated single-cell rows
                    if len(data_row) == 1 and isinstance(data_row[0], str) and ("\t" in data_row[0] or "\n" in data_row[0]):
                        embedded = _split_embedded_rows(data_row[0])
                        for parts in embedded:
                            parsed = _infer_fields_from_cells(parts)
                            key = parsed.get("afm") or parsed.get("name", "").upper()
                            if not key:
                                continue
                            exist = members_by_key.get(key)
                            if exist:
                                for k in ("dt_from", "dt_to", "role", "percentage", "name", "afm"):
                                    if not exist.get(k) and parsed.get(k):
                                        exist[k] = parsed.get(k)
                            else:
                                members_by_key[key] = parsed
                        continue

                    # normal multi-cell row: try to extract via col_map, else infer
                    cells = [str(c).strip() for c in data_row if _norm_text(c)]
                    parsed_row: Dict[str, Any] = {"afm": "", "name": "", "dt_from": "", "dt_to": "", "role": "", "percentage": ""}

                    if "afm" in col_map and col_map["afm"] < len(data_row):
                        parsed_row["afm"] = _norm_afm(data_row[col_map["afm"]])
                    if "name" in col_map and col_map["name"] < len(data_row):
                        parsed_row["name"] = _norm_text(data_row[col_map["name"]])
                        # If the name column actually contains a role token (e.g. 'Ο.Μ.', 'Ε.Μ.'), move it to role
                        name_candidate = parsed_row.get("name")
                        if name_candidate:
                            norm_role_from_name = _normalize_role(name_candidate)
                            if norm_role_from_name in ("ομορυθμο μελος", "ετερορυθμο μελος", "διαχειριστης", "νομιμος εκπροσωπος"):
                                parsed_row["role"] = norm_role_from_name
                                parsed_row["name"] = ""
                                # Attempt to infer real name from other cells
                                inferred_name = _infer_fields_from_cells(cells).get("name") if cells else ""
                                if inferred_name:
                                    parsed_row["name"] = inferred_name
                                else:
                                    # fallback: look for first alphabetic token in the row that's not AFM/date/percentage/role
                                    for k_idx, k_cell in enumerate(data_row):
                                        if k_idx == col_map.get("name"):
                                            continue
                                        if not _norm_text(k_cell):
                                            continue
                                        kc = str(k_cell).strip()
                                        if re.fullmatch(r"\d{9}", kc) or _parse_date(kc) or re.fullmatch(r"\d{1,3}", kc):
                                            continue
                                        if _normalize_role(kc) in ("ομορυθμο μελος", "ετερορυθμο μελος", "διαχειριστης", "νομιμος εκπροσωπος"):
                                            continue
                                        parsed_row["name"] = _norm_text(kc)
                                        break
                    if "dt_from" in col_map and col_map["dt_from"] < len(data_row):
                        parsed_row["dt_from"] = _norm_text(data_row[col_map["dt_from"]])
                    if col_map.get("dt_to") is not None and col_map.get("dt_to") < len(data_row):
                        parsed_row["dt_to"] = _norm_text(data_row[col_map.get("dt_to")])
                    if col_map.get("role") is not None and col_map.get("role") < len(data_row):
                        parsed_row["role"] = _normalize_role(data_row[col_map.get("role")])
                    if col_map.get("percentage") is not None and col_map.get("percentage") < len(data_row):
                        parsed_row["percentage"] = _norm_text(data_row[col_map.get("percentage")])

                    # if critical fields look wrong, re-infer from available cells
                    if (not parsed_row.get("afm") and any(re.search(r"\d{9}", c) for c in cells)) or (parsed_row.get("dt_from") and not _parse_date(parsed_row.get("dt_from")) and any(_parse_date(c) for c in cells)):
                        inferred = _infer_fields_from_cells(cells)
                        for k in ("afm", "name", "dt_from", "dt_to", "role", "percentage"):
                            if not parsed_row.get(k) and inferred.get(k):
                                parsed_row[k] = inferred.get(k)

                    # If dt_to was placed into the dt_to column but it's not a date
                    # and it looks like a role token (e.g., 'Ο.Μ.', 'Ε.Μ.'), fix mis-alignment
                    dt_to_val = parsed_row.get("dt_to")
                    if dt_to_val and not _parse_date(dt_to_val):
                        up = dt_to_val.upper()
                        if any(tok in up for tok in ("Ο.Μ", "Ε.Μ", "ΔΙΑΧΕΙΡ", "ΝΟΜΙΜΟΣ", "ΕΚΠΡ")):
                            # role likely found in dt_to column
                            # if 'role' currently holds a numeric percentage, move it to percentage
                            role_candidate = dt_to_val
                            perc_candidate = parsed_row.get("role") if re.fullmatch(r"\d{1,3}", str(parsed_row.get("role") or "")) else parsed_row.get("percentage")
                            parsed_row["role"] = _normalize_role(role_candidate)
                            parsed_row["percentage"] = _norm_text(perc_candidate) if perc_candidate else parsed_row.get("percentage")
                            parsed_row["dt_to"] = ""

                    # As a safety net, if dt_to is still not a date, try full inference from tokenized cells
                    if parsed_row.get("dt_to") and not _parse_date(parsed_row.get("dt_to")):
                        inferred2 = _infer_fields_from_cells(cells)
                        # prefer inferred dt_to if it's parseable
                        if inferred2.get("dt_to") and _parse_date(inferred2.get("dt_to")):
                            parsed_row["dt_to"] = inferred2.get("dt_to")
                        # prefer inferred role/name if current values look like role tokens
                        if parsed_row.get("name"):
                            norm_role_from_name2 = _normalize_role(parsed_row.get("name"))
                            if norm_role_from_name2 in ("ομορυθμο μελος", "ετερορυθμο μελος") and inferred2.get("name"):
                                parsed_row["name"] = inferred2.get("name")
                                # ensure role is set as canonical
                                parsed_row["role"] = norm_role_from_name2
                        if (not parsed_row.get("role") or parsed_row.get("role").isdigit()) and inferred2.get("role"):
                            parsed_row["role"] = inferred2.get("role")
                        if (not parsed_row.get("percentage") or not re.fullmatch(r"\d{1,3}", str(parsed_row.get("percentage") or ""))) and inferred2.get("percentage"):
                            parsed_row["percentage"] = inferred2.get("percentage")

                    # Final heuristic merge: prefer inferred tokens when parsed values are missing or implausible
                    inferred_all = _infer_fields_from_cells(cells) if cells else {}
                    # name: replace if missing or looks like a role or not name-like
                    if (not parsed_row.get("name") or _is_role_token(parsed_row.get("name")) or not _looks_like_name(parsed_row.get("name"))):
                        if inferred_all.get("name") and _looks_like_name(inferred_all.get("name")):
                            parsed_row["name"] = inferred_all.get("name")
                    # dt_from/dt_to: prefer parseable dates
                    if (not parsed_row.get("dt_from") or not _parse_date(parsed_row.get("dt_from"))):
                        if inferred_all.get("dt_from") and _parse_date(inferred_all.get("dt_from")):
                            parsed_row["dt_from"] = inferred_all.get("dt_from")
                    if (not parsed_row.get("dt_to") or not _parse_date(parsed_row.get("dt_to"))):
                        if inferred_all.get("dt_to") and _parse_date(inferred_all.get("dt_to")):
                            parsed_row["dt_to"] = inferred_all.get("dt_to")
                    # role: prefer inferred role when parsed is empty
                    if (not parsed_row.get("role") and inferred_all.get("role")):
                        parsed_row["role"] = inferred_all.get("role")
                    # percentage: prefer numeric inferred percentage
                    if (not re.fullmatch(r"\d{1,3}", str(parsed_row.get("percentage") or ""))) and inferred_all.get("percentage") and re.fullmatch(r"\d{1,3}", str(inferred_all.get("percentage"))):
                        parsed_row["percentage"] = inferred_all.get("percentage")

                    # Normalize date/percentage misplacements found in AADE outputs:
                    # - If dt_to is a small integer (e.g., '10','90'), treat it as percentage and clear dt_to.
                    # - If there are multiple date tokens in the row, prefer earliest as dt_from and latest as dt_to.
                    try:
                        raw_dt_from = parsed_row.get("dt_from") or ""
                        raw_dt_to = parsed_row.get("dt_to") or ""
                        # small integer in dt_to => percentage
                        if raw_dt_to and not _parse_date(raw_dt_to) and re.fullmatch(r"\d{1,3}", raw_dt_to):
                            parsed_row["percentage"] = parsed_row.get("percentage") or raw_dt_to
                            parsed_row["dt_to"] = ""

                        # collect explicit date tokens from the row 'cells' (avoid parsing arbitrary 4-digit numbers)
                        date_tokens: List[date] = []
                        date_regex = re.compile(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}|\d{2}\.\d{2}\.\d{4}|\d{4}\.\d{2}\.\d{2}")
                        for c in cells:
                            s = str(c or "")
                            for m in date_regex.findall(s):
                                d = _parse_date(m)
                                if d:
                                    date_tokens.append(d)
                        if date_tokens:
                            date_tokens = sorted(set(date_tokens))
                            earliest = date_tokens[0]
                            latest = date_tokens[-1]
                            cur_from = _parse_date(parsed_row.get("dt_from"))
                            cur_to = _parse_date(parsed_row.get("dt_to"))
                            # prefer earliest available as dt_from if it's earlier than current dt_from or dt_from missing
                            if not cur_from or earliest < cur_from:
                                parsed_row["dt_from"] = earliest.strftime("%d/%m/%Y")
                            # prefer latest available as dt_to if it's later than current dt_to or dt_to missing
                            if not cur_to or latest > cur_to:
                                # if earliest == latest then there may be only one date (use as dt_from, leave dt_to empty)
                                if latest != earliest:
                                    parsed_row["dt_to"] = latest.strftime("%d/%m/%Y")
                                else:
                                    # single date token likely implies start date
                                    parsed_row["dt_to"] = parsed_row.get("dt_to") or ""
                    except Exception:
                        pass

                    key = parsed_row.get("afm") or parsed_row.get("name", "").upper()
                    if not key:
                        continue

                    existing = members_by_key.get(key)
                    if existing:
                        if not existing.get("dt_from") and parsed_row.get("dt_from"):
                            existing["dt_from"] = parsed_row.get("dt_from")
                        if not existing.get("dt_to") and parsed_row.get("dt_to"):
                            existing["dt_to"] = parsed_row.get("dt_to")
                        if not existing.get("role") and parsed_row.get("role"):
                            existing["role"] = parsed_row.get("role")
                        if not existing.get("percentage") and parsed_row.get("percentage"):
                            existing["percentage"] = parsed_row.get("percentage")
                        if not existing.get("name") and parsed_row.get("name"):
                            existing["name"] = parsed_row.get("name")
                        if not existing.get("afm") and parsed_row.get("afm"):
                            existing["afm"] = parsed_row.get("afm")
                    else:
                        members_by_key[key] = parsed_row

                # Additionally scan other registry tables for related-person / legal-representative tables
                # (headers like 'Σχετιζόμενος ΑΦΜ', 'Είδος σχέσης', 'Σχετιζόμενος') and merge results.
                try:
                    for t_other in (tables or []):
                        if not isinstance(t_other, dict):
                            continue
                        rows_o = t_other.get('rows')
                        if not isinstance(rows_o, list) or not rows_o:
                            continue
                        # find header row that mentions related/relationship tokens
                        header_o = -1
                        for i_o, r_o in enumerate(rows_o[:8]):
                            if not isinstance(r_o, list):
                                continue
                            norms = [_normalize_address(str(c)) for c in r_o if _norm_text(c)]
                            if not norms:
                                continue
                            if any('σχετ' in n or 'σχετι' in n or 'σχετιζ' in n or 'ειδο' in n or 'σχεσ' in n or 'σχεση' in n for n in norms):
                                header_o = i_o
                                break
                        if header_o < 0:
                            continue

                        # map columns
                        col_map_o: Dict[str, int] = {}
                        header_row_o = rows_o[header_o]
                        for j, cell in enumerate(header_row_o):
                            n = _normalize_address(str(cell))
                            if not n:
                                continue
                            if 'αφμ' in n or 'afm' in n:
                                col_map_o['afm'] = j
                            elif any(tok in n for tok in ('επωνυ','επων','ονομα','name')):
                                col_map_o['name'] = j
                            elif 'ειδο' in n or 'σχεσ' in n or 'σχεση' in n:
                                col_map_o['role'] = j
                            elif any(tok in n for tok in ('έναρ','έναρχ')):
                                col_map_o.setdefault('dt_from', j)
                            elif any(tok in n for tok in ('διακοπ','λήξη','ληξη','end')):
                                col_map_o['dt_to'] = j

                        # parse data rows
                        for data_row_o in rows_o[header_o + 1:]:
                            if not isinstance(data_row_o, list):
                                continue
                            if not any(_norm_text(c) for c in data_row_o):
                                continue
                            if _row_looks_like_header(data_row_o):
                                continue
                            # extract
                            parsed_o = {'afm':'','name':'','dt_from':'','dt_to':'','role':'','percentage':''}
                            cells_o = [str(c).strip() for c in data_row_o if _norm_text(c)]
                            if 'afm' in col_map_o and col_map_o['afm'] < len(data_row_o):
                                parsed_o['afm'] = _norm_afm(data_row_o[col_map_o['afm']])
                            if 'name' in col_map_o and col_map_o['name'] < len(data_row_o):
                                parsed_o['name'] = _norm_text(data_row_o[col_map_o['name']])
                            if 'dt_from' in col_map_o and col_map_o['dt_from'] < len(data_row_o):
                                parsed_o['dt_from'] = _norm_text(data_row_o[col_map_o['dt_from']])
                            if 'dt_to' in col_map_o and col_map_o['dt_to'] < len(data_row_o):
                                parsed_o['dt_to'] = _norm_text(data_row_o[col_map_o['dt_to']])
                            if 'role' in col_map_o and col_map_o['role'] < len(data_row_o):
                                parsed_o['role'] = _normalize_role(data_row_o[col_map_o['role']])
                            # fallback inference
                            if (not parsed_o['afm'] or not parsed_o['name']) and cells_o:
                                inf = _infer_fields_from_cells(cells_o)
                                for k in ('afm','name','dt_from','dt_to','role','percentage'):
                                    if not parsed_o.get(k) and inf.get(k):
                                        parsed_o[k] = inf.get(k)

                            # format role for output
                            parsed_o['role'] = _format_role_for_output(parsed_o.get('role') or '')

                            key_o = parsed_o.get('afm') or parsed_o.get('name','').upper()
                            if not key_o:
                                continue
                            exist_o = members_by_key.get(key_o)
                            if exist_o:
                                if not exist_o.get('dt_from') and parsed_o.get('dt_from'):
                                    exist_o['dt_from'] = parsed_o.get('dt_from')
                                if not exist_o.get('dt_to') and parsed_o.get('dt_to'):
                                    exist_o['dt_to'] = parsed_o.get('dt_to')
                                if not exist_o.get('role') and parsed_o.get('role'):
                                    exist_o['role'] = parsed_o.get('role')
                            else:
                                members_by_key[key_o] = parsed_o
                except Exception:
                    # don't fail parsing extra tables; fall back to existing members
                    pass

                return list(members_by_key.values())

    # Fallback: previous token-based parsing (kept as conservative fallback)
    result = []
    if isinstance(tables, list):
        for t in tables:
            rows = t.get("rows") if isinstance(t, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, list) or not row:
                    continue

                # Tokenize cells conservatively (split on whitespace/tabs/newlines)
                tokens: List[str] = []
                for cell in row:
                    if cell is None:
                        continue
                    s = str(cell)
                    parts = [p for p in re.split(r"[\t\n\r\f\v]+|\s+", s) if p]
                    tokens.extend(parts)

                i = 0
                while i < len(tokens):
                    tok = tokens[i]
                    # start of a member record: AFM (9 digits)
                    if re.fullmatch(r"\d{9}", tok):
                        afm = tok
                        i += 1
                        name_parts: List[str] = []
                        dt_from = ""
                        dt_to = ""
                        role_parts: List[str] = []
                        percentage = ""

                        # gather name tokens until we hit a date-like token
                        while i < len(tokens):
                            tkn = tokens[i]
                            # date token dd/mm/YYYY
                            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", tkn):
                                if not dt_from:
                                    dt_from = tkn
                                elif not dt_to:
                                    dt_to = tkn
                                i += 1
                                continue

                            # percentage (simple numeric token) after dates
                            if dt_from and re.fullmatch(r"\d{1,3}", tkn):
                                percentage = tkn
                                i += 1
                                continue

                            # if next token is another AFM, stop current record
                            if re.fullmatch(r"\d{9}", tkn):
                                break

                            # Heuristic: after we have a start date, alphabetic tokens are likely role
                            if dt_from and re.search(r"[Α-Ωα-ωA-Za-z]", tkn):
                                role_parts.append(tkn)
                                i += 1
                                continue

                            # Otherwise accumulate as name
                            if not dt_from:
                                name_parts.append(tkn)
                                i += 1
                                continue

                            # fallback
                            i += 1

                        name = _norm_text(" ".join(name_parts))
                        role = _normalize_role(" ".join(role_parts))

                        result.append(
                            {
                                "afm": _norm_afm(afm),
                                "name": name,
                                "dt_from": dt_from,
                                "dt_to": dt_to,
                                "role": role,
                                "percentage": percentage,
                            }
                        )
                    else:
                        i += 1
    # Deduplicate fallback results
    by_k: Dict[str, Dict[str, Any]] = {}
    for m in result:
        key = m.get("afm") or m.get("name", "").upper()
        if not key:
            continue
        if key in by_k:
            exist = by_k[key]
            if not exist.get("name") and m.get("name"):
                exist["name"] = m.get("name")
            if not exist.get("dt_from") and m.get("dt_from"):
                exist["dt_from"] = m.get("dt_from")
            if not exist.get("dt_to") and m.get("dt_to"):
                exist["dt_to"] = m.get("dt_to")
            if not exist.get("role") and m.get("role"):
                exist["role"] = m.get("role")
            if not exist.get("percentage") and m.get("percentage"):
                exist["percentage"] = m.get("percentage")
        else:
            by_k[key] = m.copy()

    deduped = list(by_k.values())

    # If the conservative fallbacks produced nothing, try a permissive scan across
    # all registry tables: look for 9-digit AFM tokens and nearby dates/roles/percentages
    # (helps when AADE outputs concatenated or oddly-structured rows).
    if not deduped and isinstance(tables, list):
        def _robust_scan_tables_for_members(tables_list: List[Any]) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for t in tables_list:
                rows = t.get("rows") if isinstance(t, dict) else t
                if not isinstance(rows, list):
                    continue
                for i, row in enumerate(rows):
                    if not isinstance(row, list) or not any(_norm_text(c) for c in row):
                        continue
                    joined = " ".join(str(c) for c in row if _norm_text(c))
                    afm_matches = re.findall(r"(\d{9})", joined)
                    if not afm_matches:
                        # try next row if current row looks like header + data split
                        if i + 1 < len(rows) and isinstance(rows[i + 1], list):
                            joined2 = joined + " " + " ".join(str(c) for c in rows[i + 1] if _norm_text(c))
                            afm_matches = re.findall(r"(\d{9})", joined2)
                            if afm_matches:
                                joined = joined2
                            else:
                                continue
                        else:
                            continue

                    afm = _norm_afm(afm_matches[0])
                    # extract date tokens (dd/mm/YYYY)
                    dates = re.findall(r"\d{2}/\d{2}/\d{4}", joined)
                    dt_from = dates[0] if dates else ""
                    dt_to = dates[1] if len(dates) > 1 else ""

                    # detect role token in row cells
                    role_token = ""
                    for c in row:
                        if _is_role_token(c):
                            role_token = str(c)
                            break
                    # if not found, look in joined text for common abbreviations
                    if not role_token:
                        mrole = re.search(r"\b(Ο\.Μ\.|ΟΜ|Ε\.Μ\.|ΕΜ|ΟΜΟΡΡΥΘ|ΕΤΕΡΟΡΡΥΘ)\b", joined, flags=re.IGNORECASE)
                        if mrole:
                            role_token = mrole.group(0)

                    role = _normalize_role(role_token) if role_token else ""

                    # percentage: prefer a trailing small integer token (1-3 digits)
                    perc_candidates = re.findall(r"\b(\d{1,3})\b", joined)
                    perc = ""
                    if perc_candidates:
                        # pick last numeric token that is plausibly a percentage (not a year/day)
                        for p in reversed(perc_candidates):
                            if not re.fullmatch(r"\d{4}", p):
                                perc = p
                                break

                    # name: remove AFM, dates, perc and role from joined and normalize
                    clean = joined
                    clean = re.sub(r"\d{9}", "", clean)
                    clean = re.sub(r"\d{2}/\d{2}/\d{4}", "", clean)
                    if perc:
                        clean = re.sub(r"\b" + re.escape(perc) + r"\b", "", clean)
                    if role_token:
                        clean = clean.replace(role_token, "")
                    name = _norm_text(clean)

                    out.append({
                        "afm": afm,
                        "name": name,
                        "dt_from": dt_from,
                        "dt_to": dt_to,
                        "role": role,
                        "percentage": _norm_text(perc),
                    })

            # dedupe by AFM or name
            unique: Dict[str, Dict[str, Any]] = {}
            for o in out:
                k = o.get("afm") or o.get("name", "").upper()
                if not k:
                    continue
                if k not in unique:
                    unique[k] = o
            return list(unique.values())

        try:
            robust = _robust_scan_tables_for_members(tables)
            if robust:
                return robust
        except Exception:
            # swallow errors from permissive pass and fall through to return conservative result
            pass

    return deduped


def _compare_member_sets(gemi_members: List[Dict[str, Any]], company_info_members: List[Dict[str, Any]]) -> Dict[str, Any]:
    def _member_key(m: Dict[str, Any]) -> Optional[str]:
        if not isinstance(m, dict):
            return None
        afm = _norm_afm(m.get("afm") or "")
        if afm:
            return f"AFM:{afm}"
        # fallback: normalized name
        name = _norm_text(m.get("name") or m.get("full_name") or m.get("personName") or "")
        if name:
            return f"NAME:{name.upper()}"
        return None

    gemi_by_key: Dict[str, Dict[str, Any]] = {}
    for m in gemi_members or []:
        k = _member_key(m)
        if k:
            gemi_by_key[k] = m

    info_by_key: Dict[str, Dict[str, Any]] = {}
    for m in company_info_members or []:
        k = _member_key(m)
        if k:
            info_by_key[k] = m

    missing_in_company_info = [gemi_by_key[k] for k in set(gemi_by_key.keys()) - set(info_by_key.keys())]
    extra_in_company_info = [info_by_key[k] for k in set(info_by_key.keys()) - set(gemi_by_key.keys())]

    return {
        "ok": (not missing_in_company_info) and (not extra_in_company_info),
        "missing_in_company_info": missing_in_company_info,
        "extra_in_company_info": extra_in_company_info,
    }


def _sum_mydata_sub_code(aade_user: str, aade_key: str, year: int, code: str, sub_code: str) -> float:
    date_from = f"{year}-01-01"
    date_to = f"{year}-12-31"
    entries = fetch_e3_entries("0", date_from, date_to, aade_user, aade_key, debug=False)
    total = 0.0
    for row in entries:
        if str(row.get("code") or "") == code and str(row.get("sub_code") or "") == sub_code:
            total += float(row.get("amount") or 0.0)
    return round(total, 2)


def _pick_column(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    norms = {c: _normalize_address(c) for c in df.columns}
    for c, nc in norms.items():
        if nc in candidates:
            return c
    for c, nc in norms.items():
        if any(tok in nc for tok in candidates):
            return c
    return None


def _read_xlsx_rows_via_xml(excel_path: str) -> List[List[str]]:
    rows_out: List[List[str]] = []
    with zipfile.ZipFile(excel_path) as zf:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        ns_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

        sheets = wb.find(f"{ns_main}sheets")
        if sheets is None or not list(sheets):
            return rows_out
        first_sheet = list(sheets)[0]
        rid = first_sheet.attrib.get(f"{ns_rel}id")
        if not rid:
            return rows_out

        rels_xml = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_target = None
        for r in rels_xml:
            if r.attrib.get("Id") == rid:
                rel_target = r.attrib.get("Target")
                break
        if not rel_target:
            return rows_out

        rel_target = rel_target.lstrip("/")
        if not rel_target.startswith("xl/"):
            rel_target = f"xl/{rel_target}"
        rel_target = posix_normpath(rel_target)
        if rel_target.startswith("../"):
            rel_target = rel_target[3:]

        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            sst_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sst_root.findall(f"{ns_main}si"):
                text = "".join((t.text or "") for t in si.iter(f"{ns_main}t"))
                shared_strings.append(text)

        ws = ET.fromstring(zf.read(rel_target))
        for row in ws.findall(f".//{ns_main}row"):
            row_dict: Dict[int, str] = {}
            max_idx = -1
            for c in row.findall(f"{ns_main}c"):
                ref = c.attrib.get("r", "A1")
                idx = _xlsx_col_to_index(ref)
                max_idx = max(max_idx, idx)
                t = c.attrib.get("t", "")
                v = c.find(f"{ns_main}v")
                if v is None:
                    val = ""
                else:
                    raw = v.text or ""
                    if t == "s":
                        i = int(raw) if raw.isdigit() else -1
                        val = shared_strings[i] if 0 <= i < len(shared_strings) else raw
                    else:
                        val = raw
                row_dict[idx] = _norm_text(val)

            if max_idx < 0:
                continue
            dense = [row_dict.get(i, "") for i in range(max_idx + 1)]
            rows_out.append(dense)

    return rows_out


def _find_header_row_index(rows: List[List[str]], required_groups: List[set]) -> int:
    for i, row in enumerate(rows[:80]):
        norm_vals = {_normalize_address(v) for v in row if _norm_text(v)}
        if not norm_vals:
            continue
        ok = 0
        for group in required_groups:
            if any(v in group for v in norm_vals):
                ok += 1
        if ok >= len(required_groups):
            return i
    return -1


def _map_strict_headers(header_row: List[str]) -> Dict[str, int]:
    mapped: Dict[str, int] = {}
    for idx, raw in enumerate(header_row):
        n = _normalize_address(raw)
        if not n:
            continue
        for key, aliases in _STRICT_HEADER_ALIASES.items():
            if n in aliases and key not in mapped:
                mapped[key] = idx
    return mapped


def _parse_bulk_flat_rows(rows: List[List[str]]) -> List[Dict[str, Any]]:
    hdr_idx = _find_header_row_index(
        rows,
        [
            _STRICT_HEADER_ALIASES["afm"],
            _STRICT_HEADER_ALIASES["name"],
            _STRICT_HEADER_ALIASES["taxis_user"],
            _STRICT_HEADER_ALIASES["taxis_password"],
            _STRICT_HEADER_ALIASES["mydata_user"],
            _STRICT_HEADER_ALIASES["mydata_key"],
        ],
    )
    if hdr_idx < 0:
        return []

    header_map = _map_strict_headers(rows[hdr_idx])
    if "afm" not in header_map:
        return []

    out: List[Dict[str, Any]] = []
    for row in rows[hdr_idx + 1 :]:
        if not any(_norm_text(v) for v in row):
            continue

        def _at(key: str) -> str:
            j = header_map.get(key)
            return _norm_text(row[j]) if j is not None and j < len(row) else ""

        afm = _norm_afm(_at("afm"))
        if not afm:
            continue

        out.append(
            {
                "afm": afm,
                "name": _at("name"),
                "taxisnet_username": _at("taxis_user"),
                "taxisnet_password": _at("taxis_password"),
                "amka": _at("amka"),
                "mydata_user": _at("mydata_user"),
                "mydata_key": _at("mydata_key"),
                "excel_values": {
                    "E3_585_007": _to_float(_at("e3_585_007")) if "e3_585_007" in header_map else 0.0,
                    "E3_585_014": _to_float(_at("e3_585_014")) if "e3_585_014" in header_map else 0.0,
                },
                "address": _at("address"),
            }
        )
    return out


def _parse_bulk_production_template_rows(rows: List[List[str]]) -> List[Dict[str, Any]]:
    clients: List[Dict[str, Any]] = []
    i = 0
    while i < len(rows):
        row_text = _norm_text(" ".join(_norm_text(x) for x in rows[i]))
        m = re.search(r"^(\d{3,6})\s+(.+?)\s+(\d{9})$", row_text)
        if not m:
            i += 1
            continue

        company_name = _norm_text(m.group(2))
        afm = m.group(3)
        taxis_user = ""
        taxis_pass = ""
        mydata_user = ""
        mydata_key = ""

        j = i + 1
        while j < min(i + 10, len(rows)):
            txt = _norm_text(" ".join(_norm_text(x) for x in rows[j]))
            low = txt.lower()

            # Next company block starts; stop scanning current block.
            if j > i + 1 and re.search(r"^\d{3,6}\s+.+\s+\d{9}$", txt):
                break

            if "taxis" in low:
                vals = [v for v in rows[j] if _norm_text(v)]
                if len(vals) >= 3:
                    taxis_user = _norm_text(vals[1])
                    taxis_pass = _norm_text(vals[2])

            if "mydata" in low or "ηλεκτρονικά βιβλία" in low:
                vals = [v for v in rows[j] if _norm_text(v)]
                if len(vals) >= 3:
                    mydata_user = _norm_text(vals[1])
                    mydata_key = _norm_text(vals[2])
            j += 1

        clients.append(
            {
                "afm": afm,
                "name": company_name,
                "taxisnet_username": taxis_user,
                "taxisnet_password": taxis_pass,
                "amka": "",
                "mydata_user": mydata_user,
                "mydata_key": mydata_key,
                "excel_values": {
                    "E3_585_007": 0.0,
                    "E3_585_014": 0.0,
                },
                "address": "",
            }
        )

        i = j

    return clients


def _parse_bulk_clients(excel_path: str) -> List[Dict[str, Any]]:
    rows_xml = _read_xlsx_rows_via_xml(excel_path)

    # Deterministic 1: flat table with explicit headers.
    flat_clients = _parse_bulk_flat_rows(rows_xml)
    if flat_clients:
        return flat_clients

    # Deterministic 2: production "Κωδικοί Υπηρεσιών μέσω Internet" template blocks.
    template_clients = _parse_bulk_production_template_rows(rows_xml)
    if template_clients:
        return template_clients

    # Final fallback for regular workbooks readable by pandas/openpyxl.
    try:
        df = pd.read_excel(excel_path, dtype=str).fillna("")
    except Exception as exc:
        raise E3BrainError(
            "Δεν έγινε parsing του Excel. Βεβαιώσου ότι το αρχείο είναι είτε το παραγωγικό template ΤΕΣΤ_ΚΩΔΙΚΟΙ.xlsx "
            "είτε flat table με headers ΑΦΜ/επωνυμία/taxis/mydata."
        ) from exc

    c_afm = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["afm"]))
    c_name = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["name"]))
    c_user = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["taxis_user"]))
    c_pwd = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["taxis_password"]))
    c_amka = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["amka"]))
    c_mydata_user = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["mydata_user"]))
    c_mydata_key = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["mydata_key"]))
    c_585_007 = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["e3_585_007"]))
    c_585_014 = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["e3_585_014"]))
    c_address = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["address"]))
    c_legal_type = _pick_column(df, sorted(_STRICT_HEADER_ALIASES["legal_type"]))

    if not c_afm:
        raise E3BrainError("Το αρχείο Excel δεν περιέχει αναγνωρίσιμη στήλη ΑΦΜ.")

    clients: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        afm = _norm_afm(r.get(c_afm))
        if not afm:
            continue
        clients.append(
            {
                "afm": afm,
                "name": _norm_text(r.get(c_name)) if c_name else "",
                "taxisnet_username": _norm_text(r.get(c_user)) if c_user else "",
                "taxisnet_password": _norm_text(r.get(c_pwd)) if c_pwd else "",
                "amka": _norm_text(r.get(c_amka)) if c_amka else "",
                "mydata_user": _norm_text(r.get(c_mydata_user)) if c_mydata_user else "",
                "mydata_key": _norm_text(r.get(c_mydata_key)) if c_mydata_key else "",
                "excel_values": {
                    "E3_585_007": _to_float(r.get(c_585_007)) if c_585_007 else 0.0,
                    "E3_585_014": _to_float(r.get(c_585_014)) if c_585_014 else 0.0,
                },
                "address": _norm_text(r.get(c_address)) if c_address else "",
                "legal_type": _norm_text(r.get(c_legal_type)) if c_legal_type else "",
            }
        )
    return clients


def _extract_numeric_candidates_from_rows(rows: List[Dict[str, Any]], year: int) -> List[float]:
    strict_out: List[float] = []
    fallback_out: List[float] = []
    year_txt = str(year)
    value_tokens = ("συνολικ", "αξια", "αξία", "ακινητ", "αντικειμεν", "φορολογητε", "φορολογητέ")
    for row in rows:
        txt = _norm_text(row.get("text") or " ".join(str(x) for x in row.get("row", [])))
        if year_txt not in txt:
            continue
        vals = [
            _to_float(m.group(0))
            for m in re.finditer(r"\d{1,3}(?:[\.\s]\d{3})*(?:,\d{2})", txt)
            if _to_float(m.group(0)) > 0
        ]
        if not vals:
            continue

        low = txt.lower()
        if any(tok in low for tok in value_tokens):
            strict_out.extend(vals)
        else:
            fallback_out.extend(vals)

    return strict_out or fallback_out


def _run_script_and_read_json(args: List[str], cwd: Path, output_file: str) -> Tuple[bool, Dict[str, Any], str]:
    try:
        proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            return False, {}, (proc.stderr or proc.stdout or "Script failed").strip()
        path = cwd / output_file
        if not path.exists():
            return False, {}, f"Δεν βρέθηκε output αρχείο: {output_file}"
        with path.open("r", encoding="utf-8") as f:
            return True, json.load(f), ""
    except Exception as exc:
        return False, {}, str(exc)


def _extract_efka_teka_total(output_data: Dict[str, Any], year: int) -> Optional[float]:
    rows = output_data.get("rows") if isinstance(output_data, dict) else None
    if not isinstance(rows, list):
        return None

    preferred_headers = {
        "ποσο", "ποσό", "εισφορες", "εισφορές", "συνολο", "σύνολο", "οφειλη", "οφειλή"
    }
    strict_matches: List[float] = []
    fallback_matches: List[float] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        norm_items = { _normalize_address(str(k)): _norm_text(v) for k, v in row.items() }
        joined = " ".join(norm_items.values())
        if str(year) not in joined and f"/{year}" not in joined:
            continue

        row_hit = False
        for nk, value in norm_items.items():
            if any(tok in nk for tok in preferred_headers):
                for val in _parse_amount_candidates(value):
                    strict_matches.append(val)
                    row_hit = True

        if not row_hit:
            fallback_matches.extend(_parse_amount_candidates(joined))

    candidates = strict_matches or fallback_matches
    if not candidates:
        return None
    return round(max(candidates), 2)


def _pick_latest_lease(leases_payload: Any, address: str) -> Optional[Dict[str, Any]]:
    target = _normalize_address(address)
    if not target:
        return None

    best = None
    best_date = None

    if not isinstance(leases_payload, list):
        return None

    for lease in leases_payload:
        if not isinstance(lease, dict):
            continue
        detail = lease.get("detail") if isinstance(lease.get("detail"), dict) else {}
        tables = detail.get("detailTables") if isinstance(detail.get("detailTables"), list) else []

        raw_text = " ".join(
            _norm_text(cell)
            for t in tables
            for row in (t.get("rows") if isinstance(t, dict) and isinstance(t.get("rows"), list) else [])
            for cell in (row if isinstance(row, list) else [])
        )
        norm = _normalize_address(raw_text)
        if target not in norm:
            continue

        summary = lease.get("summary") if isinstance(lease.get("summary"), list) else []
        summary_txt = " ".join(_norm_text(x) for x in summary)
        dates = re.findall(r"\d{2}/\d{2}/\d{4}", summary_txt)
        submit_dt = _parse_date(dates[-1]) if dates else None

        if best is None:
            best = lease
            best_date = submit_dt
            continue
        if submit_dt and (best_date is None or submit_dt > best_date):
            best = lease
            best_date = submit_dt

    return best


def _extract_lease_amount_and_expiry(lease: Dict[str, Any]) -> Tuple[Optional[float], Optional[date]]:
    detail = lease.get("detail") if isinstance(lease.get("detail"), dict) else {}
    tables = detail.get("detailTables") if isinstance(detail.get("detailTables"), list) else []

    joined = "\n".join(
        " ".join(_norm_text(cell) for cell in row)
        for t in tables
        for row in (t.get("rows") if isinstance(t, dict) and isinstance(t.get("rows"), list) else [])
        if isinstance(row, list)
    )

    monthly_amount: Optional[float] = None
    expiry: Optional[date] = None

    for t in tables:
        if not isinstance(t, dict):
            continue
        rows = t.get("rows") if isinstance(t.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, list) or not row:
                continue

            cells = [_norm_text(c) for c in row]
            row_join = " ".join(cells).lower()
            if any(tok in row_join for tok in ("μηνια", "μισθωμα", "μίσθωμα", "ποσο μισθ", "ποσό μισθ")):
                vals = []
                for c in cells:
                    vals.extend(_parse_amount_candidates(c))
                if vals:
                    monthly_amount = max(vals) if monthly_amount is None else max(monthly_amount, max(vals))

            if any(tok in row_join for tok in ("εως", "έως", "ληξη", "λήξη")):
                for c in cells:
                    d = _parse_date(c)
                    if d and (expiry is None or d > expiry):
                        expiry = d

    if monthly_amount is None:
        amounts = _parse_amount_candidates(joined)
        monthly_amount = max(amounts) if amounts else None

    if expiry is None:
        date_matches = re.findall(r"\d{2}/\d{2}/\d{4}", joined)
        if date_matches:
            expiry = _parse_date(date_matches[-1])

    return monthly_amount, expiry


def _compare_amount(label: str, left_name: str, left: float, right_name: str, right: float, messages: List[str], tolerance: float = 0.01) -> None:
    diff = round(left - right, 2)
    if abs(diff) <= tolerance:
        return
    messages.append(
        f"{label}: διαφορά { _pretty_money(diff) } ({left_name}: {_pretty_money(left)}, {right_name}: {_pretty_money(right)})"
    )


def _build_targets(
    client: Dict[str, Any],
    is_individual: bool,
    active_members: List[Dict[str, Any]],
    member_credentials: Dict[str, Dict[str, Any]],
) -> List[PersonTarget]:
    if is_individual:
        return [
            PersonTarget(
                afm=_norm_afm(client.get("afm")),
                full_name=_norm_text(client.get("name")),
                amka=_norm_text(client.get("amka")),
                taxisnet_username=_norm_text(client.get("taxisnet_username")),
                taxisnet_password=_norm_text(client.get("taxisnet_password")),
            )
        ]

    targets: List[PersonTarget] = []
    for m in active_members:
        afm = _norm_afm(m.get("afm"))
        cred = member_credentials.get(afm, {}) if isinstance(member_credentials, dict) else {}
        targets.append(
            PersonTarget(
                afm=afm,
                full_name=_norm_text(m.get("name")),
                amka=_norm_text(cred.get("amka")),
                taxisnet_username=_norm_text(cred.get("taxisnet_username")),
                taxisnet_password=_norm_text(cred.get("taxisnet_password")),
            )
        )
    return targets


def _build_company_credential_snapshot(
    client: Dict[str, Any],
    resolved_name: str,
    active_members: List[Dict[str, Any]],
    member_credentials: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    company_afm = _norm_afm(client.get("afm"))
    out_members: List[Dict[str, Any]] = []
    for m in active_members:
        mafm = _norm_afm(m.get("afm"))
        mcred = member_credentials.get(mafm, {}) if isinstance(member_credentials, dict) else {}
        out_members.append(
            {
                "full_name": _norm_text(m.get("name")),
                "afm": mafm,
                "amka": _norm_text(mcred.get("amka")),
                "taxisnet_username": _norm_text(mcred.get("taxisnet_username")),
                "taxisnet_password": _norm_text(mcred.get("taxisnet_password")),
                "role": _format_role_for_output(m.get("role")),
            }
        )

    return {
        "company": {
            "afm": company_afm,
            "name": _norm_text(resolved_name),
            "taxisnet_username": _norm_text(client.get("taxisnet_username")),
            "taxisnet_password": _norm_text(client.get("taxisnet_password")),
            "amka": _norm_text(client.get("amka")),
            "mydata_user": _norm_text(client.get("mydata_user")),
            "mydata_key": _norm_text(client.get("mydata_key")),
            "address": _norm_text(client.get("address")),
        },
        "members": out_members,
    }


def process_client(
    client: Dict[str, Any],
    year: int,
    ref_date: date,
    range_start: Optional[date],
    range_end: Optional[date],
    active_group_clients: Dict[str, Dict[str, Any]],
    run_extractors: bool,
    headed: bool,
) -> Dict[str, Any]:
    messages: List[str] = []
    warnings: List[str] = []
    errors: List[str] = []

    afm = _norm_afm(client.get("afm"))
    if len(afm) != 9:
        return {
            "afm": afm,
            "ok": False,
            "messages": ["Μη έγκυρο ΑΦΜ (πρέπει να έχει 9 ψηφία)."],
            "warnings": [],
            "errors": ["invalid_afm"],
        }

    input_name = _norm_text(client.get("name"))
    vat_result = validate_greek_vat(afm)
    vat_name = _norm_text(vat_result.get("name")) if vat_result.get("valid") else ""
    if vat_name and not input_name:
        input_name = vat_name
        messages.append("Η επωνυμία συμπληρώθηκε αυτόματα από έλεγχο ΑΦΜ.")

    if input_name and vat_name and _normalize_address(input_name) != _normalize_address(vat_name):
        warnings.append(
            f"Ασυμφωνία επωνυμίας: εισαγόμενη '{input_name}' vs ΑΦΜ '{vat_name}'."
        )

    exists_in_group = afm in active_group_clients
    needs_mydata_credentials = not exists_in_group
    if needs_mydata_credentials and (not _norm_text(client.get("mydata_user")) or not _norm_text(client.get("mydata_key"))):
        warnings.append("Ο πελάτης δεν βρέθηκε στους πελάτες της ενεργής ομάδας. Απαιτούνται myDATA user/subscription key.")

    partners_result: Dict[str, Any] = {"success": False, "partners": [], "company": {}, "error": None}
    try:
        bp_fetcher = BusinessPortalFetcher()
        partners_result = bp_fetcher.fetch_partners(afm)
        if not partners_result.get("success") and partners_result.get("error"):
            warnings.append(f"Business Portal: {partners_result.get('error')}")
    except Exception as exc:
        warnings.append(f"Business Portal: {exc}")

    company_payload = partners_result.get("company") if isinstance(partners_result.get("company"), dict) else {}
    summary = _extract_company_summary(company_payload)

    legal_type = summary.get("legal_type") or ""
    is_individual = _is_individual_business(legal_type)

    active_members = _extract_active_members(
        partners_result,
        ref_date=ref_date,
        range_start=range_start,
        range_end=range_end,
    )
    company_info_payload = client.get("company_info") if isinstance(client.get("company_info"), dict) else {}
    company_info_members = _extract_company_info_members(company_info_payload)
    member_check = _compare_member_sets(active_members, company_info_members) if company_info_members else {"ok": True, "missing_in_company_info": [], "extra_in_company_info": []}

    if not member_check.get("ok"):
        warnings.append("Τα ενεργά μέλη από ΓΕΜΗ δεν συμφωνούν με company_info για την ημερομηνία ελέγχου.")

    member_credentials = client.get("member_credentials") if isinstance(client.get("member_credentials"), dict) else {}
    targets = _build_targets(client, is_individual, active_members, member_credentials)

    credential_snapshot = _build_company_credential_snapshot(
        client=client,
        resolved_name=(input_name or vat_name),
        active_members=active_members,
        member_credentials=member_credentials,
    )

    missing_member_credentials = [
        {
            "afm": t.afm,
            "name": t.full_name,
            "missing": [
                key
                for key, val in {
                    "taxisnet_username": t.taxisnet_username,
                    "taxisnet_password": t.taxisnet_password,
                    "amka": t.amka,
                }.items()
                if not val
            ],
        }
        for t in targets
        if not (t.taxisnet_username and t.taxisnet_password and t.amka)
    ]

    if missing_member_credentials:
        warnings.append("Λείπουν κωδικοί TAXISnet/ΑΜΚΑ για ένα ή περισσότερα μέλη.")

    efka_teka_total = 0.0
    efka_teka_available = False

    if run_extractors and not missing_member_credentials:
        root = Path(__file__).resolve().parents[2]
        checks_dir = root / "e3" / "checks"

        for t in targets:
            with tempfile.TemporaryDirectory(prefix=f"e3brain_{t.afm}_") as tmpdir:
                tmp = Path(tmpdir)

                efka_args = [
                    sys.executable,
                    str(checks_dir / "efka-extractor.py"),
                    "--username",
                    t.taxisnet_username,
                    "--password",
                    t.taxisnet_password,
                    "--amka",
                    t.amka,
                ]
                if not headed:
                    efka_args.append("--headless")

                ok_efka, efka_json, efka_err = _run_script_and_read_json(efka_args, tmp, "extracted_table_data.json")
                efka_amount = _extract_efka_teka_total(efka_json, year) if ok_efka else None

                teka_args = [
                    sys.executable,
                    str(checks_dir / "teka-extractor.py"),
                    "--username",
                    t.taxisnet_username,
                    "--password",
                    t.taxisnet_password,
                    "--amka",
                    t.amka,
                ]
                if not headed:
                    teka_args.append("--headless")

                ok_teka, teka_json, teka_err = _run_script_and_read_json(teka_args, tmp, "extracted_table_data_teka.json")
                teka_amount = _extract_efka_teka_total(teka_json, year) if ok_teka else None

                if efka_amount is None and teka_amount is None:
                    warnings.append(
                        f"Δεν βρέθηκε εκκαθάριση ΕΦΚΑ/ΤΕΚΑ για το έτος {year} στο μέλος {t.full_name or t.afm}."
                    )
                else:
                    efka_teka_available = True
                    efka_teka_total += float(efka_amount or 0.0) + float(teka_amount or 0.0)

                if not ok_efka:
                    warnings.append(f"EFKA extractor: {efka_err}")
                if not ok_teka:
                    warnings.append(f"TEKA extractor: {teka_err}")

    efka_teka_total = round(efka_teka_total, 2)

    mydata_user = _norm_text(client.get("mydata_user"))
    mydata_key = _norm_text(client.get("mydata_key"))
    mydata_585_007 = None
    mydata_585_014 = None
    if mydata_user and mydata_key:
        try:
            mydata_585_007 = _sum_mydata_sub_code(mydata_user, mydata_key, year, "585", "007")
            mydata_585_014 = _sum_mydata_sub_code(mydata_user, mydata_key, year, "585", "014")
        except Exception as exc:
            warnings.append(f"Αποτυχία ανάκτησης myDATA E3: {exc}")

    excel_vals = client.get("excel_values") if isinstance(client.get("excel_values"), dict) else {}
    excel_585_007 = round(float(excel_vals.get("E3_585_007") or 0.0), 2)
    excel_585_014 = round(float(excel_vals.get("E3_585_014") or 0.0), 2)

    if efka_teka_available:
        if mydata_585_007 is not None:
            _compare_amount("E3_585_007", "EFKA+TEKA", efka_teka_total, "myDATA", mydata_585_007, messages)
        if excel_585_007:
            _compare_amount("E3_585_007", "EFKA+TEKA", efka_teka_total, "Excel", excel_585_007, messages)

    headquarter_address = summary.get("headquarter_address") or _norm_text(client.get("address"))
    rent_annual = None
    rent_source = None

    # Prefer company-level TAXIS credentials for misth/E9 (entity-level registrations).
    # Fall back to first member's credentials if company ones are absent.
    company_taxis_user = _norm_text(client.get("taxisnet_username"))
    company_taxis_pass = _norm_text(client.get("taxisnet_password"))
    if (not company_taxis_user or not company_taxis_pass) and targets:
        company_taxis_user = company_taxis_user or targets[0].taxisnet_username
        company_taxis_pass = company_taxis_pass or targets[0].taxisnet_password

    if run_extractors and headquarter_address and company_taxis_user and company_taxis_pass:
        root = Path(__file__).resolve().parents[2]
        checks_dir = root / "e3" / "checks"

        with tempfile.TemporaryDirectory(prefix=f"e3brain_rent_{afm}_") as tmpdir:
            tmp = Path(tmpdir)

            misth_args = [
                sys.executable,
                str(checks_dir / "misth.py"),
                "--username",
                company_taxis_user,
                "--password",
                company_taxis_pass,
                "--output",
                "extracted_misth.json",
            ]
            if headed:
                misth_args.append("--headed")

            ok_misth, misth_json, misth_err = _run_script_and_read_json(misth_args, tmp, "extracted_misth.json")
            if ok_misth:
                matched = _pick_latest_lease(misth_json, headquarter_address)
                if matched:
                    monthly, expiry = _extract_lease_amount_and_expiry(matched)
                    if monthly:
                        rent_annual = round(monthly * 12.0, 2)
                        rent_source = "misth"
                        if mydata_585_014 is not None:
                            if abs(rent_annual - mydata_585_014) > 0.01:
                                rent_net = round(rent_annual / 1.036, 2)
                                if abs(rent_net - mydata_585_014) > 0.01:
                                    _compare_amount("E3_585_014", "Μισθωτήρια (annual)", rent_annual, "myDATA", mydata_585_014, messages)
                        if excel_585_014:
                            if abs(rent_annual - excel_585_014) > 0.01:
                                rent_net = round(rent_annual / 1.036, 2)
                                if abs(rent_net - excel_585_014) > 0.01:
                                    _compare_amount("E3_585_014", "Μισθωτήρια (annual)", rent_annual, "Excel", excel_585_014, messages)
                    if expiry and expiry < ref_date:
                        warnings.append(
                            f"Το πιο πρόσφατο μισθωτήριο για την έδρα φαίνεται ληγμένο ({expiry.isoformat()})."
                        )
                else:
                    warnings.append("Η διεύθυνση έδρας δεν βρέθηκε στα μισθωτήρια. Έλεγχος fallback με Ε9.")
            else:
                warnings.append(f"Misth extractor: {misth_err}")

            if rent_annual is None:
                e9_args = [
                    sys.executable,
                    str(checks_dir / "e9.py"),
                    "--username",
                    company_taxis_user,
                    "--password",
                    company_taxis_pass,
                    "--year",
                    str(year),
                    "--address",
                    headquarter_address,
                    "--output",
                    "extracted_etak_property_status.json",
                ]
                if headed:
                    e9_args.extend(["--headed", "--keep-pdf"])

                ok_e9, e9_json, e9_err = _run_script_and_read_json(e9_args, tmp, "extracted_etak_property_status.json")
                if ok_e9:
                    nums = _extract_numeric_candidates_from_rows(e9_json.get("pdfMatchedRows") or [], year)
                    if nums:
                        property_value = max(nums)
                        rent_annual = round(property_value * 0.03, 2)
                        rent_source = "e9_3_percent"
                        if mydata_585_014 is not None:
                            _compare_amount("E3_585_014", "Ε9 x 3%", rent_annual, "myDATA", mydata_585_014, messages)
                        if excel_585_014:
                            _compare_amount("E3_585_014", "Ε9 x 3%", rent_annual, "Excel", excel_585_014, messages)
                    else:
                        warnings.append("Δεν βρέθηκε αξία ακινήτου από Ε9 για υπολογισμό 3%.")
                else:
                    warnings.append(f"E9 extractor: {e9_err}")

    return {
        "afm": afm,
        "name": input_name or vat_name,
        "ok": len(errors) == 0,
        "legal_type": legal_type,
        "is_individual": is_individual,
        "headquarter": {
            "city": summary.get("city"),
            "street": summary.get("street"),
            "street_number": summary.get("street_number"),
            "zip_code": summary.get("zip_code"),
            "address": headquarter_address,
        },
        "exists_in_active_group_clients": exists_in_group,
        "needs_mydata_credentials": needs_mydata_credentials,
        "active_members": active_members,
        "member_crosscheck": member_check,
        "missing_member_credentials": missing_member_credentials,
        "checks": {
            "efka_teka_total": efka_teka_total if efka_teka_available else None,
            "mydata_585_007": mydata_585_007,
            "excel_585_007": excel_585_007,
            "mydata_585_014": mydata_585_014,
            "excel_585_014": excel_585_014,
            "rent_annual": rent_annual,
            "rent_source": rent_source,
        },
        "credential_snapshot": credential_snapshot,
        "messages": messages,
        "warnings": warnings,
        "errors": errors,
    }


def run_brain(payload: Dict[str, Any]) -> Dict[str, Any]:
    year = int(payload.get("year") or datetime.utcnow().year)
    range_start = _parse_date(payload.get("date_from"))
    range_end = _parse_date(payload.get("date_to"))
    ref_date = _parse_date(payload.get("as_of_date")) or date(year, 12, 31)

    if (range_start and not range_end) or (range_end and not range_start):
        raise E3BrainError("Όρισε και τα δύο πεδία date_from/date_to για interval έλεγχο.")
    if range_start and range_end and range_start > range_end:
        raise E3BrainError("Το date_from δεν μπορεί να είναι μετά το date_to.")

    active_group_clients_raw = payload.get("active_group_clients")
    active_group_clients: Dict[str, Dict[str, Any]] = {}
    if isinstance(active_group_clients_raw, list):
        for c in active_group_clients_raw:
            if not isinstance(c, dict):
                continue
            afm = _norm_afm(c.get("afm"))
            if afm:
                active_group_clients[afm] = c

    mode = _norm_text(payload.get("mode") or "single").lower()
    clients: List[Dict[str, Any]] = []

    if mode == "bulk":
        if isinstance(payload.get("clients"), list):
            clients = [c for c in payload.get("clients") if isinstance(c, dict)]
            if not clients:
                raise E3BrainError("Στο bulk mode απαιτείται λίστα με πελάτες για επεξεργασία.")
        else:
            excel_path = _norm_text(payload.get("excel_path"))
            if not excel_path:
                raise E3BrainError("Στο bulk mode απαιτείται το excel_path.")
            clients = _parse_bulk_clients(excel_path)
    else:
        single = payload.get("single_client") if isinstance(payload.get("single_client", {}), dict) else {}
        if not single:
            raise E3BrainError("Στο single mode απαιτείται το single_client.")
        clients = [single]

    run_extractors = bool(payload.get("run_extractors", False))
    headed = bool(payload.get("headed", False))

    results = [
        process_client(
            client=c,
            year=year,
            ref_date=ref_date,
            range_start=range_start,
            range_end=range_end,
            active_group_clients=active_group_clients,
            run_extractors=run_extractors,
            headed=headed,
        )
        for c in clients
    ]

    return {
        "ok": True,
        "mode": mode,
        "year": year,
        "date_from": range_start.isoformat() if range_start else None,
        "date_to": range_end.isoformat() if range_end else None,
        "as_of_date": ref_date.isoformat(),
        "total_clients": len(results),
        "clients": results,
    }


def _load_payload_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    if args.payload_json:
        return json.loads(args.payload_json)
    if args.payload_file:
        with open(args.payload_file, "r", encoding="utf-8") as f:
            return json.load(f)
    raise E3BrainError("Δώσε --payload-json ή --payload-file.")


def main() -> None:
    parser = argparse.ArgumentParser(description="E3 checks brain orchestrator")
    parser.add_argument("--payload-json", help="JSON string payload")
    parser.add_argument("--payload-file", help="Path to payload JSON file")
    parser.add_argument("--output", default="e3_brain_result.json", help="Output JSON path")
    args = parser.parse_args()

    payload = _load_payload_from_args(args)
    result = run_brain(payload)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Wrote E3 brain result to: {out}")


if __name__ == "__main__":
    main()
