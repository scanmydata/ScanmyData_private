import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
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


def _normalize_address(value: str) -> str:
    value = _norm_text(value).lower()
    value = re.sub(r"[^\w\d\sάέήίόύώϊϋΐΰα-ω]", " ", value)
    return " ".join(value.split())


def _pretty_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _extract_company_summary(company_payload: Dict[str, Any]) -> Dict[str, Any]:
    company = company_payload.get("company") if isinstance(company_payload.get("company"), dict) else company_payload
    legal_type = _norm_text(
        company.get("legalType")
        or company.get("legalTypeLabel")
        or company.get("coLegalType")
        or company.get("coLegalTypeLabel")
        or company.get("legalForm")
        or ""
    )

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


def _extract_active_members(partners_result: Dict[str, Any], ref_date: date) -> List[Dict[str, Any]]:
    partners = partners_result.get("partners") if isinstance(partners_result, dict) else []
    if not isinstance(partners, list):
        return []

    members: List[Dict[str, Any]] = []
    for p in partners:
        if not isinstance(p, dict):
            continue
        if not _is_active_on(ref_date, p.get("dtFrom"), p.get("dtTo")):
            continue
        name = _norm_text(p.get("personName") or p.get("businessName") or "")
        afm = _norm_afm(p.get("vat") or p.get("afm") or p.get("personAfm") or "")
        members.append(
            {
                "name": name,
                "afm": afm,
                "role": _norm_text(p.get("role") or ""),
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
                    "name": _norm_text(m.get("name") or m.get("full_name") or ""),
                    "afm": _norm_afm(m.get("afm") or ""),
                }
            )
        return out

    # Best-effort parse from extracted registry tables of company_info.py
    result = []
    tables = company_info_payload.get("registryTables")
    if isinstance(tables, list):
        for t in tables:
            rows = t.get("rows") if isinstance(t, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, list) or not row:
                    continue
                joined = " ".join(_norm_text(cell) for cell in row)
                afms = re.findall(r"\b\d{9}\b", joined)
                if not afms:
                    continue
                result.append({"name": _norm_text(joined), "afm": afms[0]})
    return result


def _compare_member_sets(gemi_members: List[Dict[str, Any]], company_info_members: List[Dict[str, Any]]) -> Dict[str, Any]:
    gemi_by_afm = {m.get("afm"): m for m in gemi_members if m.get("afm")}
    info_by_afm = {m.get("afm"): m for m in company_info_members if m.get("afm")}

    missing_in_company_info = [gemi_by_afm[k] for k in gemi_by_afm.keys() - info_by_afm.keys()]
    extra_in_company_info = [info_by_afm[k] for k in info_by_afm.keys() - gemi_by_afm.keys()]

    return {
        "ok": not missing_in_company_info and not extra_in_company_info,
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
                "role": _norm_text(m.get("role")),
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

    active_members = _extract_active_members(partners_result, ref_date)
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

    if run_extractors and headquarter_address and targets:
        main_target = targets[0]
        root = Path(__file__).resolve().parents[2]
        checks_dir = root / "e3" / "checks"

        with tempfile.TemporaryDirectory(prefix=f"e3brain_rent_{afm}_") as tmpdir:
            tmp = Path(tmpdir)

            misth_args = [
                sys.executable,
                str(checks_dir / "misth.py"),
                "--username",
                main_target.taxisnet_username,
                "--password",
                main_target.taxisnet_password,
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
                    main_target.taxisnet_username,
                    "--password",
                    main_target.taxisnet_password,
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
    ref_date = _parse_date(payload.get("as_of_date")) or date(year, 12, 31)

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
        excel_path = _norm_text(payload.get("excel_path"))
        if not excel_path:
            raise E3BrainError("Στο bulk mode απαιτείται το excel_path.")
        clients = _parse_bulk_clients(excel_path)
    else:
        single = payload.get("single_client") if isinstance(payload.get("single_client"), dict) else {}
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
