# E3 Check Notes (2026-05-25)

## Scope
This note documents important behavior and design decisions for the E3 check page and APIs.

## 1) myDATA fetch must populate only MYDATA column

When the user runs myDATA retrieval from the E3 check page:
- `mydata_value` is populated from `RequestE3Info`.
- `e3_value` must remain empty/zero until an Excel file is uploaded.

Implementation detail:
- In `templates/e3_check.html`, `loadE3Data()` now resets `E3_EXCEL_TOTALS` before rendering fetched rows.
- This prevents stale Excel totals from previous uploads being shown in the E3 column during a new myDATA fetch.

Why this matters:
- The user can run repeated myDATA fetches for different periods/customers.
- Without reset, previously uploaded Excel totals could incorrectly appear in E3 values (observed especially in expense rows).

## 2) Active clients vs client_db counterparties

Clarification:
- `client_db` is counterparties/suppliers (`συναλλασσόμενοι`), not active customer portfolio.
- It must not be used as source of "active clients" for E3 brain checks.

Implementation detail:
- In `app.py` routes:
  - `/api/e3/brain`
  - `/api/e3/brain/upload`
- Auto-population of `active_group_clients` now uses `load_credentials()` entries (`vat`, `name`) instead of `client_db` parsing.

Why this matters:
- Eligibility checks like "exists in active group clients" must be evaluated against credentials (actual customer set), not counterparties.

## 3) Related endpoints and files

Main routes:
- `app.py` -> `/api/e3/fetch`
- `app.py` -> `/api/e3/upload_excel`
- `app.py` -> `/api/e3/brain`
- `app.py` -> `/api/e3/brain/upload`

Frontend:
- `templates/e3_check.html`

Orchestrator:
- `e3/checks/e3_brain.py`

## 4) Operational guidance

Recommended page workflow:
1. Run myDATA fetch first (fills only MYDATA column).
2. Upload accounting Excel when available (fills E3 column overlay for comparison).
3. Optional: run E3 brain single/bulk flows for legal form/member/headquarter checks.

If results look inconsistent:
- Re-run myDATA fetch (now clears previous Excel overlay by design).
- Re-upload the desired Excel to restore E3 comparison values.

## 5) Save checked credentials/person data to active group JSON

New capability:
- After E3 Brain execution (single or bulk), user can save checked company/member credentials data.

Frontend:
- `templates/e3_check.html` adds button: `Αποθήκευση Credentials Ομάδας`.
- It sends last successful E3 Brain result to backend endpoint.

Backend endpoint:
- `POST /api/e3/brain/save_credentials`
- Auth required (`@login_required`).
- Authorization: global admin OR user with active-group role `admin`/`member`.
- Scope enforcement: writes only inside active group's folder:
  - `data/<active_group.data_folder>/e3_company_credentials_store.json`

Stored structure (per company AFM):
- Company fields: AFM, name, TAXIS username/password, AMKA, myDATA user/key, address.
- Members array: full_name, AFM, AMKA, TAXIS username/password, role.
- Metadata: group info, updated_by, updated_at, per-record saved_at.

Merge behavior:
- Existing JSON is loaded if present.
- Upsert by company AFM (new data replaces previous entry for same AFM).

Source of snapshots:
- `e3/checks/e3_brain.py` now emits `credential_snapshot` per processed client result.
