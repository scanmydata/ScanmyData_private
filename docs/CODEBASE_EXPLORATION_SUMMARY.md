# ScanMyData Codebase Exploration Summary

## Overview
This document summarizes the key architectural components for customer selection/switching, repeat profiles, AFM rules, and receipts/invoices UI toggling.

---

## 1. Customer Selection & Switching

### Current Implementation

**Customer Selection UI Location:**
- File: [templates/base.html](templates/base.html#L2021)
- Display Element: `.layout-header__active-customer` - Shows active customer name + VAT in header
- Navigation Link: Clicking the active customer badge links to `/credentials` page

**Active Customer Detection:**
- **Session-based:** `get_active_credential_from_session()` (app.py line 5199)
  - Reads `session["active_credential"]` to find credential name
  - Looks up the full credential object from credentials.json
  
- **HTML Template Resolution:** `rcResolveActiveVat()` function in [templates/search.html](templates/search.html#L990)
  - Priority order:
    1. Server-side template variable: `{{ active_credential_vat|default('', true) }}`
    2. Body dataset attribute: `document.body.dataset.activeVat`
    3. DOM element selectors: `#vatSelector`, `[name="vat"]`
    4. Window variable: `window.__RC_ACTIVE_VAT` (cached)
    5. Falls back to empty string if nothing found

**Customer Switching API:**
- Route: `@app.route('/credentials/set_active', methods=['POST'])` (app.py line 7260)
  - Parameter: `active_name` (credential name)
  - Updates `active: true` flag for selected credential in credentials.json
  - Persists to disk via `save_credentials()`
  - Returns: redirect to `/credentials` page with flash message

**Context Injection:**
- Function: `@app.context_processor inject_active_credential()` (app.py)
  - Automatically injects to all templates:
    - `active_credential` - credential name
    - `active_credential_vat` - VAT/AFM of active credential
    - `app_settings` - application settings
    - `user_role` - admin or member
    - `active_group` - group name
    - `active_year` - fiscal year

---

## 2. Repeat Profiles - Loading & Saving

### Data Storage Structure

**Where Stored:**
- File: `credentials.json` → each credential object contains nested `repeat_entry`
- Structure:
  ```json
  {
    "name": "customer_name",
    "vat": "123456789",
    "repeat_entry": {
      "enabled": true,
      "mapping": {
        "0%": "category_slug",
        "6%": "category_slug",
        "13%": "category_slug",
        "17%": "category_slug",
        "24%": "category_slug"
      },
      "profile_name": "Profile Name",
      "invoice_mtype": "3.4.1",
      "receipt_mtype": "3.4.2",
      "general_mapping": { /* same structure as mapping */ },
      "updated_at": "2025-05-18T10:30:00Z"
    }
  }
  ```

### API Endpoints

**GET Repeat Entry State:**
- Routes: 
  - `/api/repeat_entry/get_v2` (preferred, returns profile_name)
  - `/api/repeat_entry/status2` (alias)
  - `/api/repeat_entry/get` (legacy)
- Query param: `?vat=...`
- Returns: `{ ok, repeat_entry: { enabled, mapping, profile_name, invoice_mtype, receipt_mtype }, expense_tags, category_labels }`

**SAVE Repeat Entry:**
- Route: `@app.post("/api/repeat_entry/save")` (app.py)
- Payload:
  ```json
  {
    "vat": "123456789",
    "enabled": true,
    "mapping": { "0%": "cat", "6%": "cat", ... },
    "general_mapping": { /* optional */ },
    "profile_name": "Profile Name",
    "invoice_mtype": "3.4.1",
    "receipt_mtype": "3.4.2"
  }
  ```
- Validation: All 5 VAT rates (0%, 6%, 13%, 17%, 24%) must have categories selected
- Returns: Updated `repeat_entry` object

### JavaScript Client-Side Loading

**fetch APIs in [static/receipts_repeat_direct.js](static/receipts_repeat_direct.js#L266):**
- Function: `fetchRepeatEntryState(vat)` (line 266)
  - Tries endpoints in order: `/api/repeat_entry/get_v2` → `/api/repeat_entry/get`
  - Returns full state object or null
  - Called to populate repeat modal and apply repeat mappings

- Function: `fetchReceiptProfileMapping(vat, profileName)` (line 280)
  - Fetches `/api/char_profiles?mode=receipts&vat=...`
  - Returns receipt-mode profiles filtered by name
  - Used for receipt analysis mode

**localStorage Persistence:**
- Keys:
  - `REPEAT:enabled` - boolean flag ('1' or '0')
  - `REPEAT:mapping` - JSON string of mapping
  - `UI:useReceipts` - boolean flag for receipts mode

---

## 3. Customer Change Listeners & Events

### Event System

**Bridge File:** [static/receipts_mode_repeat_bridge.js](static/receipts_mode_repeat_bridge.js#L1)
- Purpose: Synchronize repeat state, receipts mode, and form submission
- Custom Events Dispatched:
  - `useReceiptsChange` - fired when receipts toggle changes
  - `repeatStateChange` - fired when repeat enabled/mapping changes
  - `rc:markSubmit` - fired on form submit

**Form Hooks:**
- `hookSwitches()`: Attaches `change` listeners to:
  - `#useReceiptsSwitch` → calls `setUseReceiptsState()`
  - `#repeatEntrySwitch` → calls `setRepeatState()`
  
- `hookFormSubmit()`: Attaches to `#markSearchForm` submit
  - Persists UI state to localStorage before submission
  - Fires `rc:markSubmit` event

**Auto-Apply Logic:**
- File: [static/repeat_flow_guard.js](static/repeat_flow_guard.js#L1)
  - Watches for meaningful summaries
  - If repeat + receipts both enabled AND summary is a receipt → auto-submits (no modal)
  - Prevents infinite loops with `autoSaveInProgress` flag and 1200ms guard

### No Global Customer Change Handler

**Important:** There is **NO explicit global "customer changed" event listener** in the codebase.
- Customer change is detected via session/DOM attribute updates
- Each page reload reinitializes state from `/api/repeat_entry/get?vat=...`
- Repeat profile auto-loads on search.html page load via AJAX

---

## 4. Permission System for AFM Rules

### Access Control Model

**Who Can Edit AFM Rules:**
- Currently: **No explicit role check** - any authenticated user with active credential can save/delete
- Rules are per-credential (VAT-scoped)
- URL requires `?vat=...` parameter to identify credential

**API Endpoints for AFM Rules:**

1. **GET Rules:** `@app.get("/api/afm_rules")` (app.py line 11751)
   - Query param: `?vat=...`
   - Returns: List of rules for VAT
   - Retrieves via `_get_afm_rules(creds, vat)`

2. **SAVE Rule:** `@app.post("/api/afm_rules/save")` (app.py line 11796)
   - Payload: `{ vat, supplier_afm, supplier_name, mapping, invoice_mtype, receipt_mtype }`
   - Validation:
     - All 5 VAT rates must have category mappings
     - Supplier AFM must be normalized (9 digits)
   - Stores in `credentials[index]["afm_rules"]` array
   - Uses file lock for atomic writes

3. **DELETE Rule:** `@app.post("/api/afm_rules/delete")` (app.py line 11900)
   - Payload: `{ vat, supplier_afm }`
   - Removes matching rule from array
   - Persists to disk

4. **VALIDATE Rule:** `@app.post("/api/afm_rules/validate")` (app.py line 11943)
   - Payload: `{ vat, summary: { lines, invoice_mtype, ... } }`
   - Returns:
     ```json
     {
       "applies": false/true,
       "mismatch": false/true,
       "rule": { /* rule object */ },
       "supplier_afm": "...",
       "category_mismatches": [ { vat_key, expected, expected_label, actual, actual_labels } ],
       "mtype_mismatch": { expected, actual }
     }
     ```

**Rule Application Logic:**
- File: [app.py](app.py#L2864) - `_validate_summary_against_afm_rules()`
- Rules apply only when:
  - Client has rules defined
  - Supplier AFM matches a rule
  - Document type is NOT a receipt (invoices only)
  - User hasn't disabled rule enforcement
- Checks:
  - Each line's VAT rate → expect specific expense category
  - Invoice MTYPE (for Γ-category customers)

**UI Integration:**
- File: [templates/search.html](templates/search.html#L107)
- Button: "Κανόνες ΑΦΜ" (AFM Rules)
- Modal flow:
  1. User saves document → validation runs
  2. If mismatch detected → warning modal shown
  3. User can apply expected categories OR continue as-is
- Persisted decision: `AFM_RULE_DECISION_STORAGE_PREFIX + vat` in localStorage

---

## 5. Receipts/Invoices Toggle (`useReceiptsSwitch`)

### UI Toggle Element

**DOM Element:**
- File: [templates/search.html](templates/search.html#L53)
- Element: `<input type="checkbox" id="useReceiptsSwitch" role="switch">`
- Label: "Τιμολόγια" (Invoices) ↔ "Αποδείξεις" (Receipts)
- Visibility: Always visible on search page

### State Management

**Persistence Keys:**
- localStorage: `UI:useReceipts` - '1' for receipts, '0' for invoices
- window: `window.USE_RECEIPTS` - boolean
- DOM: `document.body.dataset.useReceipts` - '1' or '0'

**Initialization:**
- Priority order in [receipts_mode_repeat_bridge.js](static/receipts_mode_repeat_bridge.js#L39):
  1. URL param: `?use_receipts=1`
  2. localStorage: `UI:useReceipts`
  3. DOM checkbox initial state: `#useReceiptsSwitch.checked`
  4. Default: false (invoices mode)

### Effects on UI Visibility

**Hidden/Shown Based on receipts Flag:**

1. **Repeat Profile Selection Modal** - [static/receipts_repeat_direct.js](static/receipts_repeat_direct.js#L280)
   - Filters profiles by mode: `?mode=receipts` when `isReceipts()` is true
   - Profile names and MTYPE dropdowns shown only when repeat enabled

2. **MTYPE Dropdown in Repeat Modal:**
   - `#repeatMtypeContainer` shown only for G-category customers + invoices
   - Hidden for receipts (receipts use `receipt_mtype` field instead)

3. **Receipt Analysis Mode:**
   - Available only when: `useReceiptsSwitch.checked && repeatEntrySwitch.checked`
   - Shows receipt-specific UI for line-by-line classification
   - File: [templates/search.html](templates/search.html#L75) - `#receiptsModeMenu`
   - Options: "Ανάλυση" (Analysis) vs "Μικτό" (Mixed)

4. **Receipt Mode Persistence:**
   - localStorage key: `rc:receiptMode` - values: 'analysis' | 'mixed' | 'off'
   - Functions in [static/receipts_repeat_direct.js](static/receipts_repeat_direct.js#L28):
     - `isAnalysisMode()` - checks if 'analysis' mode active
     - `isMixedMode()` - checks if mixed/default mode active
     - `receiptMode()` - returns current mode string

### Server-Side Flag Usage

**Python Functions:**
- `_afm_rules_apply_for_client()` (app.py line 2991)
  - Returns `False` for receipts (rules don't apply)
  - Only validates invoices

- `_validate_summary_against_afm_rules()` (app.py line 2864)
  - Param: `is_receipt: bool`
  - Skips MTYPE validation when `is_receipt=True`
  - Only checks category mappings for receipts

- Route: `/api/afm_rules/validate` 
  - Auto-detects receipt type from:
    - `summary.get("docType").startswith("receipt")`
    - `summary.get("is_receipt") == true`
    - Contains keywords: "receipt", "αποδειξ", "λιαν", "pos"

### Receipt Analysis Specific

**Analysis Mode Features:**
- File: [static/receipts_repeat_direct.js](static/receipts_repeat_direct.js#L295)
  - Fetches profile mapping for **receipts only**: `/api/char_profiles?mode=receipts`
  - Applies per-line category classification
  - Stores `receipt_analysis_enabled` flag in summary
  - Requires lines with net + VAT breakdown

**Autosave Behavior:**
- B-category: Auto-saves with repeat mapping (no modal)
- G-category: Shows modal for MTYPE selection before save
- Detection: `isGCategoryCustomer()` function (line 218)

---

## 6. Related Routes & Helpers

### Repeat Profiles Management
- `@app.route("/api/profiles", methods=["GET"])` - List profiles for active customer
- `@app.route("/api/profiles/save", methods=["POST"])` - Save/update profile
- `@app.route("/api/profiles/delete", methods=["POST"])` - Delete profile

### Fiscal Year Management
- `@app.route('/get_fiscal_year', methods=['GET'])` - Get active fiscal year
- `@app.route('/set_fiscal_year', methods=['POST'])` - Set fiscal year
- Function: `set_active_fiscal_year(year)` - Persists to `fiscal_meta.json`

### Category & Label Helpers
- `_list_invoice_categories(client)` - Gets expense tags for customer
- `_category_labels_for_client(client)` - Gets human-readable labels
- `_category_vat_constraints(client)` - Maps which VAT rates allowed per category

### Repeat Entry Payload Builder
- `_build_repeat_entry_payload(repeat_raw)` - Normalizes repeat_entry structure
- `_normalize_repeat_mapping(mapping_in)` - Ensures all VAT keys present
- `_is_complete_repeat_mapping(candidate)` - Validates all 5 rates have values

---

## 7. Data Flow Diagram

```
User selects customer
    ↓
POST /credentials/set_active
    ↓
Update credentials.json (active flag)
    ↓
session["active_credential"] = name
    ↓
Template @context_processor loads active_credential + vat
    ↓
search.html renders rcResolveActiveVat() function
    ↓
GET /api/repeat_entry/get?vat=X
    ↓
Returns repeat_entry { enabled, mapping, profile_name }
    ↓
localStorage persists REPEAT:* keys
    ↓
User checks repeatEntrySwitch
    ↓
change event → fetchRepeatEntryState()
    ↓
Modal displays profile categories + MTYPE
    ↓
User saves → POST /api/repeat_entry/save
    ↓
credentials.json updated + persisted
```

---

## 8. Key Files Reference

| Component | File | Line |
|-----------|------|------|
| Customer Selection HTML | templates/base.html | 2021 |
| Active VAT Resolution | templates/search.html | 990 |
| Repeat State Bridge | static/receipts_mode_repeat_bridge.js | 1 |
| Repeat Flow Guard | static/repeat_flow_guard.js | 1 |
| Repeat Profile Fetch | static/receipts_repeat_direct.js | 266 |
| Set Active Credential | app.py | 7260 |
| Get Active from Session | app.py | 5199 |
| Repeat Entry APIs | app.py | 5923+ |
| AFM Rules Endpoints | app.py | 11751+ |
| AFM Validation | app.py | 2864 |
| Context Injection | app.py | inject_active_credential |

