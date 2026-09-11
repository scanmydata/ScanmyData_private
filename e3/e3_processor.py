"""
e3_processor.py
===============
Επεξεργασία αρχείου Excel Ισοζυγίου (Γ Κατηγορίας) για εξαγωγή ποσών Ε3.

Υποστηρίζει ανοικτά και κλειστά (χρεοπιστωμένα) ισοζύγια:
  - Ανοικτό  : ποσό Ε3 = ABS(Υπόλοιπο)
  - Κλειστό  : ομάδα-6 → Χρέωση, ομάδα-7 → Πίστωση

Χρήση:
    from e3_processor import process_excel_file
    result = process_excel_file("/tmp/isozygio.xls")
    # result = {"ok": True, "is_closed_balance_sheet": ..., "e3_totals": [...], ...}
"""

import os
import pandas as pd
import numpy as np

from .e3_field_map import lookup as _e3_lookup, group_by_table as _e3_group


# ─────────────────────────────────────────────────────────────────────────────
# Εσωτερικές βοηθητικές συναρτήσεις
# ─────────────────────────────────────────────────────────────────────────────

def _load_dataframe(excel_path: str):
    """
    Φορτώνει το Excel και εντοπίζει αυτόματα τη γραμμή επικεφαλίδων
    αναζητώντας τις λέξεις-κλειδιά «κωδικός» και «υπόλοιπο».

    Επιστρέφει DataFrame ή None αν δεν βρεθεί έγκυρη γραμμή.
    """
    xls = pd.ExcelFile(excel_path)
    for sheet in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet, header=None)
        for i, row_vals in raw.iterrows():
            row_str = " ".join(str(v) for v in row_vals if pd.notna(v)).lower()
            if "κωδικός" in row_str and ("υπόλοιπο" in row_str or "υπολοιπο" in row_str):
                return pd.read_excel(xls, sheet_name=sheet, header=i)
    return None


def _normalise_columns(df: pd.DataFrame):
    """
    Αντιστοιχίζει τις ελληνικές επικεφαλίδες σε εσωτερικά ονόματα
    (code, desc, debit, credit, balance, e3).

    Επιστρέφει (DataFrame με νέα ονόματα, λίστα λείπουσων στηλών).
    """
    col_map = {}
    for c in df.columns:
        cs = str(c).strip().lower()
        if "κωδικός" in cs or "κωδικος" in cs:
            col_map["code"] = c
        elif "περιγραφή" in cs or "περιγραφη" in cs:
            col_map["desc"] = c
        elif "χρέωση" in cs or "χρεωση" in cs:
            col_map["debit"] = c
        elif "πίστωση" in cs or "πιστωση" in cs:
            col_map["credit"] = c
        elif "υπόλοιπο" in cs or "υπολοιπο" in cs:
            col_map["balance"] = c
        elif "ε3" in cs or "εντύπου" in cs or "εντυπου" in cs:
            col_map["e3"] = c

    required = ["code", "debit", "credit", "balance", "e3"]
    missing = [k for k in required if k not in col_map]

    rename = {v: k for k, v in col_map.items()}
    df = df.rename(columns=rename)

    # Καθαρισμός & μετατροπή τύπων
    df["code"] = df["code"].astype(str).str.strip()
    for num_col in ("debit", "credit", "balance"):
        df[num_col] = pd.to_numeric(df[num_col], errors="coerce").fillna(0.0)
    df["e3"] = pd.to_numeric(df["e3"], errors="coerce")

    # Απόρριψη γραμμών χωρίς κωδικό
    df = df[df["code"].str.len() > 0]
    df = df[df["code"].str.lower() != "nan"]

    return df, missing


def _detect_closed_balance(df: pd.DataFrame) -> bool:
    """
    Ανιχνεύει αν το ισοζύγιο είναι κλειστό (χρεοπιστωμένο).

    Κριτήριο: ≥50% των leaf-λογαριασμών ομάδας-6 έχουν:
      Υπόλοιπο ≈ 0  ΚΑΙ  Χρέωση > 0  ΚΑΙ  Πίστωση > 0
    """
    grp6_leaf = df[
        df["code"].str.startswith("6") &
        df["code"].str.contains("-")
    ]
    if len(grp6_leaf) == 0:
        return False

    closed_mask = (
        (grp6_leaf["balance"].abs() < 0.01) &
        (grp6_leaf["debit"] > 0) &
        (grp6_leaf["credit"] > 0)
    )
    return closed_mask.sum() / len(grp6_leaf) >= 0.5


def _extract_e3_amount(row: pd.Series, is_closed: bool):
    """
    Υπολογίζει το ποσό Ε3 για μία leaf-γραμμή.

    Επιστρέφει dict {e3_code, amount} ή None αν η γραμμή δεν έχει κωδικό Ε3.
    """
    e3_val = row["e3"]
    if pd.isna(e3_val) or e3_val == -1:
        return None

    first_char = row["code"][0] if row["code"] else ""
    try:
        account_group = int(first_char)
    except ValueError:
        account_group = 0

    if is_closed:
        if account_group == 6:
            amount = abs(row["debit"])
        elif account_group == 7:
            amount = abs(row["credit"])
        else:
            amount = abs(row["balance"])
    else:
        amount = abs(row["balance"])

    return {"e3_code": float(e3_val), "amount": amount}


def _enrich_totals(e3_totals: dict) -> tuple:
    """
    Εμπλουτίζει τα σύνολα Ε3 με περιγραφές από το e3_field_map.

    Επιστρέφει (totals_list, grouped_by_table).
    """
    totals_list = []
    for k, v in sorted(e3_totals.items()):
        entry = {"e3_code": k, "amount": round(v, 2)}
        info = _e3_lookup(k)
        if info:
            entry["label"]       = info["label"]
            entry["sub_label"]   = info["sub_label"]
            entry["table"]       = info["table"]
            entry["activity"]    = info["activity"]
            entry["description"] = info["full_description"]
        totals_list.append(entry)

    grouped = _e3_group(totals_list)
    return totals_list, grouped


# ─────────────────────────────────────────────────────────────────────────────
# Δημόσια API
# ─────────────────────────────────────────────────────────────────────────────

def extract_account_leaf_rows(excel_path: str) -> dict:
    """
    Επεξεργάζεται ένα αρχείο Excel ισοζυγίου (ίδια μορφή με process_excel_file)
    αλλά επιστρέφει ΚΑΘΕ leaf-λογαριασμό με μη μηδενικό ποσό, ανεξάρτητα από το αν
    έχει οριζόμενο κωδικό Ε3 — χρησιμοποιείται από το "Λογιστικό Αποτέλεσμα" για να
    αντικαταστήσει (override) τα αυτόματα υπολογισμένα από myDATA ποσά ανά λογαριασμό.

    Returns:
        Dict με ok, is_closed_balance_sheet, leaf_rows: [{account, description, amount, side}]
    """
    df = _load_dataframe(excel_path)
    if df is None:
        return {
            "ok": False,
            "error": (
                "Δεν βρέθηκαν οι στήλες Κωδικός / Υπόλοιπο. "
                "Βεβαιωθείτε ότι το αρχείο είναι ισοζύγιο (Γ κατηγορίας)."
            ),
        }

    df, missing = _normalise_columns(df)
    if missing:
        return {
            "ok": False,
            "error": (
                f"Λείπουν στήλες: {missing}. "
                "Αναμένονται: Κωδικός, Χρέωση, Πίστωση, Υπόλοιπο, Πεδίο Ε3."
            ),
        }

    is_closed = _detect_closed_balance(df)

    leaf_df = df[df["code"].str.contains("-")].copy()
    if len(leaf_df) == 0:
        leaf_df = df.copy()

    rows = []
    for _, row in leaf_df.iterrows():
        first_char = row["code"][0] if row["code"] else ""
        try:
            account_group = int(first_char)
        except ValueError:
            account_group = 0

        if is_closed:
            if account_group == 6:
                amount, side = abs(row["debit"]), "debit"
            elif account_group == 7:
                amount, side = abs(row["credit"]), "credit"
            else:
                amount, side = abs(row["balance"]), "balance"
        else:
            amount, side = abs(row["balance"]), "balance"

        if amount == 0:
            continue

        rows.append({
            "account": row["code"],
            "description": str(row.get("desc", "")).strip() if "desc" in row else "",
            "amount": round(float(amount), 2),
            "side": side,
        })

    return {"ok": True, "is_closed_balance_sheet": is_closed, "leaf_rows": rows}


def process_excel_file(excel_path: str) -> dict:
    """
    Επεξεργάζεται ένα αρχείο Excel ισοζυγίου και εξάγει τα ποσά Ε3.

    Args:
        excel_path: Απόλυτο μονοπάτι προς το .xls/.xlsx αρχείο.

    Returns:
        Dict με κλειδιά:
          ok                    – True αν η επεξεργασία πέτυχε
          error                 – Μήνυμα σφάλματος (μόνο αν ok=False)
          is_closed_balance_sheet – bool
          total_rows            – συνολικές γραμμές μετά φόρτωση
          leaf_rows_processed   – γραμμές που επεξεργάστηκαν (leaf)
          e3_totals             – λίστα {e3_code, amount, label?, table?,...}
          e3_by_table           – dict ομαδοποιημένος ανά πίνακα Ε3
          detail                – ανά-λογαριασμός λεπτομέρεια
    """
    # 1. Φόρτωση
    df = _load_dataframe(excel_path)
    if df is None:
        return {
            "ok": False,
            "error": (
                "Δεν βρέθηκαν οι στήλες Κωδικός / Υπόλοιπο. "
                "Βεβαιωθείτε ότι το αρχείο είναι ισοζύγιο (Γ κατηγορίας)."
            ),
        }

    # 2. Κανονικοποίηση στηλών
    df, missing = _normalise_columns(df)
    if missing:
        return {
            "ok": False,
            "error": (
                f"Λείπουν στήλες: {missing}. "
                "Αναμένονται: Κωδικός, Χρέωση, Πίστωση, Υπόλοιπο, Πεδίο Ε3."
            ),
        }

    # 3. Ανίχνευση κλειστού ισοζυγίου
    is_closed = _detect_closed_balance(df)

    # 4. Επεξεργασία leaf-γραμμών
    leaf_df = df[df["code"].str.contains("-")].copy()
    if len(leaf_df) == 0:
        leaf_df = df.copy()

    e3_totals = {}
    detail_rows = []
    for _, row in leaf_df.iterrows():
        result_item = _extract_e3_amount(row, is_closed)
        if result_item is None:
            continue
        code_key = result_item["e3_code"]
        e3_totals[code_key] = e3_totals.get(code_key, 0.0) + result_item["amount"]
        detail_rows.append({
            "account":     row["code"],
            "description": str(row.get("desc", "")).strip() if "desc" in row else "",
            "e3_code":     code_key,
            "amount":      result_item["amount"],
        })

    # 5. Εμπλουτισμός με περιγραφές & ομαδοποίηση
    totals_list, grouped = _enrich_totals(e3_totals)

    return {
        "ok":                     True,
        "is_closed_balance_sheet": is_closed,
        "total_rows":             len(df),
        "leaf_rows_processed":    len(leaf_df),
        "e3_totals":              totals_list,
        "e3_by_table":            grouped,
        "detail":                 detail_rows,
    }
