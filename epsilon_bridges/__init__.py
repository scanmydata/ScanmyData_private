from .epsilon_bridge_multiclient_strict import *
from .epsilon_bridge_g_category import *

# Expose legacy internal helpers used by app.py and other callers.
from .epsilon_bridge_multiclient_strict import (
    _safe_json_read,
    _to_date,
    _ddmmyyyy,
    _safe_int,
    _round2,
    _norm_key,
    _norm_afm,
    _settings_norm,
    _merge_custom_accounts,
    _canon_category,
    _infer_vat_rate_for_line,
    _is_receipt,
    _receipt_analysis_enabled,
    _parse_lines,
    _reason_for_rec_enhanced,
    _format_name_with_afm,
    _load_client_map,
    resolve_paths_for_vat,
    load_epsilon_invoices,
    _compose_invoice_value,
    _read_active_fiscal_year,
    characts_from_lines,
    _discover_client_db_in_data_dir,
)
