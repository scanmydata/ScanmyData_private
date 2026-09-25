/**
 * accounting_result.js
 * Λογιστικό Αποτέλεσμα page: single + bulk compute, inventory-resolution popups,
 * Ισοζύγιο Excel override upload, client-side PDF export (window.print(), same
 * pattern as the E3-check page — this app has no server-side PDF generation).
 */

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = String(s == null ? '' : s);
  return div.innerHTML;
}

function fmtMoney(v) {
  if (v === null || v === undefined || v === '') return '—';
  return Number(v).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtAmountOrBlank(v) {
  if (v === null || v === undefined || Number(v) === 0) return '';
  return fmtMoney(v);
}

function fmtPct(v) {
  if (v === null || v === undefined) return 'NAN';
  return Number(v).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%';
}

function todayStr() {
  const d = new Date();
  return String(d.getDate()).padStart(2, '0') + '/' + String(d.getMonth() + 1).padStart(2, '0') + '/' + d.getFullYear();
}

function ddmmyyyy(dateStr) {
  // Date fields are flatpickr'd to dd/mm/yyyy already (matching the E3-check
  // page); this only converts the legacy ISO (yyyy-mm-dd) shape if it's ever
  // passed in from somewhere else.
  if (!dateStr) return '';
  if (dateStr.indexOf('/') !== -1) return dateStr;
  const parts = dateStr.split('-');
  if (parts.length !== 3) return dateStr;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

function yearFromDMY(dateStr) {
  const parts = String(dateStr || '').split('/');
  if (parts.length === 3 && parts[2].length === 4) return parseInt(parts[2], 10);
  const isoParts = String(dateStr || '').split('-');
  if (isoParts.length === 3 && isoParts[0].length === 4) return parseInt(isoParts[0], 10);
  return new Date().getFullYear();
}

// Same overlay + show/hide contract as the E3-check page's
// showGlobalWaitOverlay()/hideGlobalWaitOverlay() (#waitOverlay), including
// its E3_BRAIN_BULK_RUNNING-style suppression: the bulk tab explicitly opted
// for a non-blocking flash + cross-page banner instead of a modal overlay
// (setBulkTableLocked() already prevents interaction), so while a bulk run
// is in flight this diverts straight to the #arBulkStatus text.
var AR_OVERLAY_TIMEOUT = null;
var AR_BULK_RUNNING = false;

function showArOverlay(title, message) {
  if (AR_BULK_RUNNING) {
    const statusEl = document.getElementById('arBulkStatus');
    if (statusEl && message) statusEl.textContent = String(message);
    if (message) showArFlash(String(message), 'warning', 0);
    return;
  }
  // Defensive re-move: the DOMContentLoaded-time moveModalsToBody() call can
  // lose the race against a fast click or a slow multi-script partial-nav
  // load (base_08.js sequentially awaits every <script> on the page before
  // firing the queued DOMContentLoaded), leaving this modal still nested
  // inside #appShell — and thus vulnerable to the page-entering transform —
  // right when it's about to be shown. Calling this again here is a no-op
  // once already moved, so it's cheap insurance on every open.
  moveModalsToBody();
  const overlay = document.getElementById('waitOverlay');
  const titleEl = document.getElementById('waitOverlayTitle');
  const msgEl = document.getElementById('waitOverlayMsg');
  if (titleEl && title) titleEl.textContent = String(title);
  if (msgEl && message) msgEl.textContent = String(message);
  if (overlay) {
    overlay.style.display = 'flex';
    overlay.style.pointerEvents = 'all';
    overlay.setAttribute('aria-hidden', 'false');
  }
  if (AR_OVERLAY_TIMEOUT) clearTimeout(AR_OVERLAY_TIMEOUT);
  AR_OVERLAY_TIMEOUT = setTimeout(hideArOverlay, 90000);
}

function hideArOverlay() {
  if (AR_OVERLAY_TIMEOUT) { clearTimeout(AR_OVERLAY_TIMEOUT); AR_OVERLAY_TIMEOUT = null; }
  const overlay = document.getElementById('waitOverlay');
  if (overlay) {
    overlay.style.display = 'none';
    overlay.style.pointerEvents = 'none';
    overlay.setAttribute('aria-hidden', 'true');
  }
}

async function postJson(url, body) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    // A non-JSON body here (almost always HTML) means something outside
    // Flask's own error handling intervened — a hosting-platform gateway
    // timeout page, or a redirect to the login page because the session
    // expired mid-request — not an error this app ever returns itself.
    // res.json() would still throw on that, but with a raw
    // "Unexpected token '<'..." message that's meaningless to the
    // accountant using this page, so detect it up front and say what
    // actually likely happened instead.
    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
      if (res.status === 401 || res.status === 403 || /\/login/i.test(res.url)) {
        return { ok: false, error: 'Η σύνδεσή σας έληξε — κάντε ανανέωση της σελίδας και ξανασυνδεθείτε.' };
      }
      return { ok: false, error: `Ο διακομιστής δεν απάντησε σωστά (HTTP ${res.status}) — πιθανό timeout στην επικοινωνία με το myDATA. Δοκιμάστε ξανά.` };
    }
    return await res.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ---------------- Report table rendering ----------------

// vat -> {phone,email} from the shared saved-clients store, filled by
// arLoadContactCache() at page init and after every saved-list reload, so the
// (synchronous) PDF/report builders can show the client's contact details.
window.__arContactByVat = window.__arContactByVat || {};
async function arLoadContactCache() {
  try {
    const resp = await (await fetch('/api/e3/brain/credentials_store')).json();
    if (!resp.ok) return;
    const map = {};
    (resp.companies || []).forEach((e) => {
      const c = e.company || {};
      if (c.afm) map[String(c.afm)] = { mobile: c.mobile || '', phone: c.phone || '', email: c.email || '' };
    });
    window.__arContactByVat = map;
  } catch (_) { /* contact line just stays empty */ }
}
function arContactBits(vat) {
  const c = (window.__arContactByVat || {})[String(vat || '')] || {};
  const bits = [];
  if (c.mobile) bits.push('Κινητό: ' + escapeHtml(c.mobile));
  if (c.phone) bits.push('Σταθερό: ' + escapeHtml(c.phone));
  if (c.email) bits.push('Email: ' + escapeHtml(c.email));
  return bits;
}
function arContactLineHtml(vat) {
  const bits = arContactBits(vat);
  return bits.length ? '<div style="font-weight:400;font-size:0.8rem;color:#475569;">' + bits.join(' &nbsp;·&nbsp; ') + '</div>' : '';
}

function buildReportSectionHtml(name, vat, from, to, r) {
  const stockRowsHtml = r.stock_rows.map((row) => `
    <tr>
      <td class="ar-label">${escapeHtml(row.code)} ${escapeHtml(row.label)}</td>
      <td class="ar-num">${fmtAmountOrBlank(row.opening)}</td>
      <td class="ar-num">${fmtAmountOrBlank(row.purchases)}</td>
      <td class="ar-num">${fmtAmountOrBlank(row.closing)}</td>
      <td class="ar-num">${fmtAmountOrBlank(row.cogs)}</td>
    </tr>`).join('');

  const salesExpenseRowsHtml = r.sales_rows.map((s, i) => {
    const e = r.expense_rows[i];
    return `<tr>
      <td class="ar-label">${escapeHtml(s.code)} ${escapeHtml(s.label)}</td>
      <td class="ar-num">${fmtAmountOrBlank(s.amount)}</td>
      <td class="ar-label">${e ? escapeHtml(e.code) + ' ' + escapeHtml(e.label) : ''}</td>
      <td class="ar-num">${e ? fmtAmountOrBlank(e.amount) : ''}</td>
    </tr>`;
  }).join('');

  const extraRowsHtml = r.extra_sales_rows.map((s, i) => {
    const e = r.extra_expense_rows[i];
    return `<tr>
      <td class="ar-label">${escapeHtml(s.code)} ${escapeHtml(s.label)}</td>
      <td class="ar-num">${fmtAmountOrBlank(s.amount)}</td>
      <td class="ar-label">${e ? escapeHtml(e.code) + ' ' + escapeHtml(e.label) : ''}</td>
      <td class="ar-num">${e ? fmtAmountOrBlank(e.amount) : ''}</td>
    </tr>`;
  }).join('');

  const unclassifiedCount = (r.unclassified_marks || []).length;
  const unclassifiedRow = r.unclassified_net
    ? `<tr>
        <td colspan="2"></td>
        <td class="ar-label">Αχαρακτήριστα παραστατικά (myDATA${unclassifiedCount ? ', ' + unclassifiedCount + ' MARK' : ''})</td>
        <td class="ar-num">−${fmtMoney(Math.abs(r.unclassified_net))}</td>
      </tr>`
    : '';

  const unresolvedNote = r.unresolved_purchase_count
    ? `<div class="text-xs" style="color:#b45309;margin-top:4px;">⚠ ${fmtMoney(r.unresolved_purchase_total)} € (${r.unresolved_purchase_count} γραμμές) δεν αντιστοιχίστηκαν σε λογαριασμό — ελέγξτε τις ρυθμίσεις κατηγοριών.</div>`
    : '';

  const methodologyNote = `<div class="ar-footnote" style="font-size:12px;color:#333;margin-top:8px;line-height:1.5;">
    * Αποσβέσεις: υπολογίζονται αναλογικά (pro-rata, βάσει μηνών της περιόδου) από τις εγγραφές κωδ. 587 του myDATA του <strong>προηγούμενου</strong> έτους.` +
    (r.inventory_method_label
      ? ` Απόθεμα λήξης: ${escapeHtml(r.inventory_method_label)}.`
      : '') +
    `</div>` + renderReportNotesHtml(r.notes, name, yearFromDMY(to), r.legal_kind, from, to);

  return `
  <div class="ar-report-section">
    <div class="ar-report-header">
      <div class="ar-company-block">${escapeHtml(name)} <span class="ar-vat">(ΑΦΜ: ${escapeHtml(vat || '')})</span>${arContactLineHtml(vat)}</div>
      <div style="text-align:right;">Στοιχεία Λογιστή: ${escapeHtml(window.AR_ACCOUNTANT_NAME || '')}<br><strong>Ημερομηνία: ${todayStr()}</strong></div>
    </div>
    <div class="ar-report-title">Λογιστικό Αποτέλεσμα</div>
    <div class="ar-report-period">Από: ${ddmmyyyy(from)}&nbsp;&nbsp;Έως: ${ddmmyyyy(to)}</div>

    <table class="ar-report-table">
      <thead><tr>
        <th>Λογαριασμός</th><th>Αποθέματα Έναρξης</th><th>Αγορές Χρήσης</th><th>Αποθέματα Λήξης</th><th>Κόστος Πωληθέντων</th>
      </tr></thead>
      <tbody>
        ${stockRowsHtml}
        <tr><td class="ar-label">Δαπάνες Παραγωγής</td><td></td><td></td><td></td><td class="ar-num">${fmtAmountOrBlank(r.production_expenses)}</td></tr>
        <tr><td class="ar-label">Αγορές Παγίων</td><td></td><td class="ar-num">${fmtMoney(r.fixed_asset_purchases)}</td><td></td><td></td></tr>
        <tr class="ar-total-row"><td class="ar-label">Σύνολο Κόστους Πωληθέντων Εμπορευμάτων</td><td colspan="3"></td><td class="ar-num">${fmtMoney(r.cogs_goods)}</td></tr>
        <tr class="ar-total-row"><td class="ar-label">Σύνολο Κόστους Πωληθέντων Προϊόντων</td><td colspan="3"></td><td class="ar-num">${fmtMoney(r.cogs_products)}</td></tr>
        <tr class="ar-total-row"><td class="ar-label">Σύνολο Κόστους Πωληθέντων</td><td colspan="3"></td><td class="ar-num">${fmtMoney(r.cogs_total)}</td></tr>
      </tbody>
    </table>

    <table class="ar-report-table" style="margin-top:8px;">
      <thead><tr><th>Πωλήσεις</th><th class="ar-num">Ποσό</th><th>Δαπάνες</th><th class="ar-num">Ποσό</th></tr></thead>
      <tbody>
        ${salesExpenseRowsHtml}
        <tr class="ar-total-row">
          <td class="ar-label">Σύνολο Πωλήσεων Εκμ/σης</td><td class="ar-num">${fmtMoney(r.sales_ekm)}</td>
          <td class="ar-label">Σύνολο Εξόδων Εκμ/σης</td><td class="ar-num">${fmtMoney(r.expenses_ekm)}</td>
        </tr>
        ${extraRowsHtml}
        <tr class="ar-total-row">
          <td class="ar-label">Σύνολα Πωλήσεων</td><td class="ar-num">${fmtMoney(r.sales_total)}</td>
          <td class="ar-label">Σύνολα Δαπανών</td><td class="ar-num">${fmtMoney(r.expenses_total)}</td>
        </tr>
        <tr><td class="ar-label">Πωλήσεις Παγίων</td><td class="ar-num">${fmtAmountOrBlank(r.sales_paggion)}</td><td></td><td></td></tr>
      </tbody>
    </table>

    <table class="ar-report-table" style="margin-top:8px;">
      <tbody>
        <tr>
          <td class="ar-label">Μικτό Κέρδος Εμπορευμάτων</td><td class="ar-num">${fmtAmountOrBlank(r.gross_goods)}</td>
          <td class="ar-label ar-total-row">Καθαρά Κέρδη</td><td class="ar-num ar-total-row">${fmtMoney(r.net_profit)}</td>
        </tr>
        <tr>
          <td class="ar-label">Μικτό Κέρδος Προϊόντων</td><td class="ar-num">${fmtAmountOrBlank(r.gross_products)}</td>
          <td class="ar-label">Ζημίες Προηγούμενου Έτους</td><td class="ar-num">${fmtAmountOrBlank(r.prior_year_losses)}</td>
        </tr>
        <tr class="ar-total-row">
          <td class="ar-label">Συνολικό Μικτό Κέρδος</td><td class="ar-num">${fmtMoney(r.gross_total)}</td>
          <td class="ar-label">Τελικά Καθαρά Κέρδη</td><td class="ar-num">${fmtMoney(r.final_net_profit)}</td>
        </tr>
        ${unclassifiedRow}
        <tr class="ar-total-row">
          <td colspan="2"></td>
          <td class="ar-label">Φορολογητέο Αποτέλεσμα</td><td class="ar-num">${fmtMoney(r.taxable_result)}</td>
        </tr>
      </tbody>
    </table>

    ${r.vat_applicable !== false && r.vat_period_from && r.vat_period_to ? `
    <div style="font-size:1rem;font-weight:700;color:#0f172a;margin-top:6px;">ΦΠΑ περιόδου ${escapeHtml(ddmmyyyy(r.vat_period_from))} – ${escapeHtml(ddmmyyyy(r.vat_period_to))}</div>` : ''}
    <table class="ar-report-table" style="margin-top:4px;">
      <tbody>
        <tr>
          <td>% μεικτό εμπορικό αποτέλεσμα επί κόστους</td><td class="ar-num">${fmtPct(r.pct_gross_on_cost)}</td>
          ${r.vat_applicable === false ? '<td></td><td></td>' : `<td class="ar-label">ΦΠΑ Εκροών</td><td class="ar-num">${fmtMoney(r.vat_outflow)}</td>`}
        </tr>
        <tr>
          <td>% μεικτό εμπορικό αποτέλεσμα επί πωλήσεων</td><td class="ar-num">${fmtPct(r.pct_gross_on_sales)}</td>
          ${r.vat_applicable === false ? '<td></td><td></td>' : `<td class="ar-label">Μείον ΦΠΑ Εισροών</td><td class="ar-num">${fmtMoney(r.vat_inflow)}</td>`}
        </tr>
        <tr>
          <td>% αποτελέσματα παροχής υπ. επί εσόδων Π/Υ</td><td class="ar-num">${fmtPct(r.pct_services)}</td>
          ${r.vat_applicable === false ? '<td class="ar-label" style="color:#6b7280;font-style:italic;">Μη υπόχρεη ΦΠΑ</td><td></td>' : `<td class="ar-label">Μείον Πιστ.Υπόλ.Προηγ.Περ.</td><td class="ar-num">${fmtMoney(r.vat_prior_credit)}</td>`}
        </tr>
        ${r.vat_applicable === false ? '' : `
        <tr>
          <td></td><td></td>
          <td class="ar-label">Μείον Πληρωμές στο Δημόσιο</td><td class="ar-num">${fmtMoney(r.vat_state_payments)}</td>
        </tr>
        <tr class="ar-total-row">
          <td></td><td></td>
          <td class="ar-label">Χρεωστικό Υπόλοιπο Περιόδου</td><td class="ar-num">${fmtMoney(r.vat_period_balance)}</td>
        </tr>`}
      </tbody>
    </table>
    ${unresolvedNote}
    ${methodologyNote}
  </div>`;
}

// ---------------- Real PDF download (html2pdf.js, self-hosted) ----------------
// Renders the same styled DOM already on the page into an actual PDF file and
// triggers a browser download — no print dialog, no popup window (which was
// silently getting blocked and showing up as an empty tab).

function safeFilename(s) {
  return String(s || 'Λογιστικό_Αποτέλεσμα').replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, '_').slice(0, 150);
}

// dd/mm/yyyy period -> "dd-mm-yyyy_dd-mm-yyyy", for filenames (both single
// and bulk exports carry the computed period so two runs never overwrite
// each other on disk).
function periodSuffix(from, to) {
  const f = ddmmyyyy(from).replace(/\//g, '-');
  const t = ddmmyyyy(to).replace(/\//g, '-');
  return f && t ? `${f}_${t}` : '';
}

// var, not let/const — base_08.js's partial-nav re-runs this whole file as a
// fresh <script src> on every navigation into this page (it only rewrites
// let/const->var for INLINE scripts, not external ones like this file), and
// a top-level let/const throws "already declared" the second time, aborting
// the entire script and leaving the page's buttons unwired.
var __arHtml2PdfPromise = null;
function ensureHtml2Pdf() {
  if (window.html2pdf) return Promise.resolve();
  if (!__arHtml2PdfPromise) {
    __arHtml2PdfPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = '/static/vendor/html2pdf.bundle.min.js';
      s.onload = () => resolve();
      s.onerror = () => reject(new Error('Αποτυχία φόρτωσης βιβλιοθήκης PDF'));
      document.head.appendChild(s);
    });
  }
  return __arHtml2PdfPromise;
}

var __arJsZipPromise = null;
function ensureJsZip() {
  if (window.JSZip) return Promise.resolve();
  if (!__arJsZipPromise) {
    __arJsZipPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = '/static/vendor/jszip.min.js';
      s.onload = () => resolve();
      s.onerror = () => reject(new Error('Αποτυχία φόρτωσης βιβλιοθήκης ZIP'));
      document.head.appendChild(s);
    });
  }
  return __arJsZipPromise;
}

// Renders `innerHtml` to a real PDF Blob (no download side-effect — callers
// either save it directly or bundle several into a ZIP). Confirmed by
// isolated testing (4 positioning variants compared byte-for-byte):
// html2canvas's "computed rendering" clone path in the bundled version
// measures ANY out-of-flow element — position:fixed OR position:absolute,
// regardless of coordinates or z-index — as having ZERO height, producing
// an effectively-blank PDF. Only a plain static, normal-flow element
// renders with its real height, so the export container must NOT be
// positioned at all. Separately, html2pdf.js silently caps the html2canvas
// capture at a fixed ~794px logical width (~A4 at 96dpi) UNLESS
// `html2canvas.width`/`height` (not `windowWidth` — that one has no effect
// here) are set explicitly — our tables have long Greek labels in nowrap
// cells that routinely exceed that, cropping columns silently, so both
// must always be read from the container's actual rendered size.
// html2pdf only turns <a href> into EXTERNAL url links, so the consolidated
// report's name -> contact-legend jumps are drawn here instead: elements
// marked data-ar-goto="<id>" get a jsPDF internal link to the page holding
// #<id>. Page mapping mirrors html2pdf's 'legacy' slicing (fixed-height
// slices of the captured container, inner box = page minus margins).
function arAddInternalPdfLinks(pdf, container, orientation, marginMm) {
  try {
    const pageW = orientation === 'landscape' ? 297 : 210;
    const pageH = orientation === 'landscape' ? 210 : 297;
    const innerW = pageW - 2 * marginMm;
    const innerH = pageH - 2 * marginMm;
    const cw = container.scrollWidth;
    const slice = Math.floor(cw * innerH / innerW);
    const toMm = innerW / cw;
    const cr = container.getBoundingClientRect();
    const pages = pdf.internal.getNumberOfPages();
    const pos = (el) => {
      const r = el.getBoundingClientRect();
      const top = r.top - cr.top;
      const page = Math.floor(top / slice) + 1;
      return { page, x: marginMm + (r.left - cr.left) * toMm, y: marginMm + (top - (page - 1) * slice) * toMm, w: r.width * toMm, h: r.height * toMm };
    };
    container.querySelectorAll('[data-ar-goto]').forEach((el) => {
      const target = container.querySelector('#' + el.getAttribute('data-ar-goto'));
      if (!target) return;
      const from = pos(el);
      const to = pos(target);
      if (from.page > pages || to.page > pages) return;
      pdf.setPage(from.page);
      pdf.link(from.x, from.y, from.w, from.h, { pageNumber: to.page });
    });
    pdf.setPage(pages);
  } catch (e) {
    console.warn('PDF internal links skipped:', e);
  }
}

async function buildPdfBlob(innerHtml, orientation, fitToOnePage) {
  await ensureHtml2Pdf();
  const container = document.createElement('div');
  container.style.width = orientation === 'landscape' ? '1500px' : '1000px';
  container.style.background = '#fff';
  container.style.color = '#111';
  container.style.padding = '10px';
  container.innerHTML = innerHtml;
  document.body.appendChild(container);
  try {
    // Wait for the container to actually finish settling before measuring/
    // capturing it. A single-company report on a "cold" page (its first
    // html2canvas call — web fonts, Tailwind's computed styles etc. not yet
    // warmed up) was observed truncating mid-table with only a 50ms delay,
    // while the identical render succeeded every time inside the ZIP export
    // loop (later, "warm" calls). document.fonts.ready + a longer buffer
    // covers the cold-start case without meaningfully slowing anything down.
    try { await document.fonts.ready; } catch (_) {}
    await new Promise((r) => setTimeout(r, 250));
    const capturedWidth = container.scrollWidth;
    const capturedHeight = container.scrollHeight;
    const worker = window.html2pdf().from(container).set({
      margin: 8,
      image: { type: 'jpeg', quality: 0.98 },
      // scrollX/scrollY: 0 — required whenever the page is scrolled when
      // export runs, otherwise html2canvas offsets the capture by the
      // current scroll position: content shifts down inside a canvas still
      // sized for the *unshifted* height, producing a blank leading page
      // and clipping the tail of the report (confirmed via a real exported
      // e3_check.html PDF using this same pipeline).
      html2canvas: { scale: 2, useCORS: true, backgroundColor: '#ffffff', width: capturedWidth, height: capturedHeight, scrollX: 0, scrollY: 0 },
      jsPDF: { unit: 'mm', format: 'a4', orientation: orientation || 'portrait' },
      // 'css' mode's page-break-inside:avoid detection is what produced a
      // blank leading page and mis-cropped columns on a real multi-client
      // export in e3_check.html's identical pipeline — plain height-based
      // slicing ('legacy' only) is less "smart" (a table row can split
      // across a page) but doesn't have that failure mode.
      pagebreak: { mode: ['legacy'] },
    });

    if (!fitToOnePage) {
      const pdf = await worker.toPdf().get('pdf');
      arAddInternalPdfLinks(pdf, container, orientation, 8);
      return pdf.output('blob');
    }
    // Force everything onto a single A4 page (shrinking, never cropping):
    // render the full canvas, then scale it down to fit the page's usable
    // box instead of letting html2pdf slice it across N pages. html2pdf
    // doesn't expose the jsPDF constructor on window, so we reuse the
    // jsPDF instance it already built internally (verified in isolated
    // testing — worker.toPdf().get('pdf') always has real addImage/
    // deletePage/setFillColor methods, regardless of how many pages
    // html2pdf's own default pagination produced).
    const canvas = await worker.toCanvas().get('canvas');
    const pdf = await worker.toPdf().get('pdf');

    const marginMm = 6;
    const pageWmm = orientation === 'landscape' ? 297 : 210;
    const pageHmm = orientation === 'landscape' ? 210 : 297;
    const usableW = pageWmm - marginMm * 2;
    const usableH = pageHmm - marginMm * 2;
    const aspect = canvas.height / canvas.width;
    let wMm = usableW;
    let hMm = usableW * aspect;
    if (hMm > usableH) {
      hMm = usableH;
      wMm = usableH / aspect;
    }
    const xMm = marginMm + (usableW - wMm) / 2;
    const yMm = marginMm;

    while (pdf.internal.getNumberOfPages() > 1) {
      pdf.deletePage(pdf.internal.getNumberOfPages());
    }
    pdf.setPage(1);
    pdf.setFillColor(255, 255, 255);
    pdf.rect(0, 0, pageWmm, pageHmm, 'F');
    const imgData = canvas.toDataURL('image/jpeg', 0.98);
    pdf.addImage(imgData, 'JPEG', xMm, yMm, wMm, hMm);
    return pdf.output('blob');
  } finally {
    document.body.removeChild(container);
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function exportHtmlAsPdf(innerHtml, filename, orientation, fitToOnePage) {
  await arLoadContactCache();
  showArOverlay('Δημιουργία PDF...', 'Παρακαλώ περιμένετε όσο δημιουργείται το αρχείο.');
  try {
    const blob = await buildPdfBlob(innerHtml, orientation, fitToOnePage);
    downloadBlob(blob, safeFilename(filename) + '.pdf');
  } finally {
    hideArOverlay();
  }
}

// One PDF per company, bundled into a single ZIP download (replaces the old
// "one giant concatenated multi-page PDF" behaviour for "Λήψη PDF όλες").
async function exportZipOfIndividualPdfs(companies, zipFilename, statusEl) {
  await Promise.all([ensureHtml2Pdf(), ensureJsZip()]);
  showArOverlay('Δημιουργία ZIP...', `Δημιουργία PDF για ${companies.length} εταιρίες...`);
  try {
    const zip = new window.JSZip();
    for (let i = 0; i < companies.length; i++) {
      const c = companies[i];
      if (statusEl) statusEl.textContent = `Δημιουργία PDF ${i + 1}/${companies.length} — ${c.name}...`;
      const blob = await buildPdfBlob(buildReportSectionHtml(c.name, c.vat, c.from, c.to, c.report), 'portrait', true);
      const suffix = periodSuffix(c.from, c.to);
      zip.file(safeFilename('Λογιστικό_Αποτέλεσμα_' + c.name + (suffix ? '_' + suffix : '')) + '.pdf', blob);
    }
    const zipBlob = await zip.generateAsync({ type: 'blob' });
    downloadBlob(zipBlob, safeFilename(zipFilename) + '.zip');
  } finally {
    hideArOverlay();
  }
}

function buildConsolidatedTableHtml(companies) {
  // Findings (report notes) become markers next to the affected cell —
  // * μισθοδοσία, ** ενοίκιο, *** ΕΦΚΑ on Δαπάνες, † on Αχαρακτ. — each
  // linking (inside the PDF) to its explanation in «Παρατηρήσεις» below.
  const noteRows = [];
  const marksFor = (c, i, cell) => (c.notes || (c.report && c.report.notes) || [])
    .filter((n) => AR_NOTE_TYPES[n.type] && AR_NOTE_TYPES[n.type].cell === cell)
    .map((n) => {
      const id = `ar-note-${i}-${noteRows.length}`;
      noteRows.push({ id, mark: AR_NOTE_TYPES[n.type].mark, name: c.name, message: n.message });
      return `<sup data-ar-goto="${id}" style="color:#b91c1c;font-weight:700;">${escapeHtml(AR_NOTE_TYPES[n.type].mark)}</sup>`;
    }).join('');
  const rowsHtml = companies.map((c, i) => {
    const r = c.report;
    const openingSum = (r.stock_rows || []).reduce((a, s) => a + (s.opening || 0), 0);
    const purchasesSum = (r.stock_rows || []).reduce((a, s) => a + (s.purchases || 0), 0);
    const closingSum = (r.stock_rows || []).reduce((a, s) => a + (s.closing || 0), 0);
    const expenseMarks = marksFor(c, i, 'expenses');
    const unclassifiedMarks = marksFor(c, i, 'unclassified');
    // Not subject to ΦΠΑ -> "Χ" instead of an empty/zero balance.
    const vatCell = r.vat_applicable === false ? 'Χ' : fmtAmountOrBlank(r.vat_period_balance);
    return `<tr>
      <td class="ar-num">${i + 1}</td>
      <td>${arContactBits(c.vat).length ? `<span data-ar-goto="ar-contact-${i}" style="color:#1d4ed8;text-decoration:underline;">${escapeHtml(c.name)}</span>` : escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.vat || '')}</td>
      <td>${todayStr()}</td>
      <td>${ddmmyyyy(c.from)}</td>
      <td>${ddmmyyyy(c.to)}</td>
      <td class="ar-num">${fmtAmountOrBlank(openingSum)}</td>
      <td class="ar-num">${fmtAmountOrBlank(purchasesSum)}</td>
      <td class="ar-num">${fmtAmountOrBlank(closingSum)}</td>
      <td class="ar-num">${fmtAmountOrBlank(r.cogs_total)}</td>
      <td class="ar-num">${fmtAmountOrBlank(r.expenses_total)}${expenseMarks}</td>
      <td class="ar-num">${fmtAmountOrBlank(r.sales_total)}</td>
      <td class="ar-num"></td>
      <td class="ar-num">${fmtAmountOrBlank(-Math.abs(r.unclassified_net || 0))}${unclassifiedMarks}</td>
      <td class="ar-num">${fmtAmountOrBlank(r.taxable_result)}</td>
      <td class="ar-num"></td>
      <td class="ar-num" style="${r.vat_applicable === false ? 'text-align:center;font-weight:700;' : ''}">${vatCell}</td>
    </tr>`;
  }).join('');

  const notesHtml = noteRows.length
    ? `<div style="margin-top:24px;">
      <div style="font-size:16px;font-weight:700;margin-bottom:4px;">Παρατηρήσεις</div>
      <div style="font-size:11px;color:#475569;margin-bottom:6px;">${Object.values(AR_NOTE_TYPES).map((t) => `<b>${escapeHtml(t.mark)}</b> ${escapeHtml(t.label)}`).join(' &nbsp;·&nbsp; ')} &nbsp;·&nbsp; <b>Χ</b> στη στήλη ΦΠΑ: μη υπόχρεος ΦΠΑ</div>
      <table class="ar-consolidated-table"><thead><tr><th></th><th>Επωνυμία</th><th>Παρατηρήσεις</th></tr></thead><tbody>
        ${noteRows.map((n, k) => {
          // One block per client: the name cell spans all of its notes.
          if (k > 0 && noteRows[k - 1].name === n.name) {
            return `<tr id="${n.id}"><td style="color:#b91c1c;font-weight:700;text-align:center;">${escapeHtml(n.mark)}</td><td style="white-space:normal;">${escapeHtml(n.message)}</td></tr>`;
          }
          let span = 1;
          while (noteRows[k + span] && noteRows[k + span].name === n.name) span++;
          return `<tr id="${n.id}"><td style="color:#b91c1c;font-weight:700;text-align:center;">${escapeHtml(n.mark)}</td><td rowspan="${span}" style="font-weight:600;vertical-align:top;">${escapeHtml(n.name)}</td><td style="white-space:normal;">${escapeHtml(n.message)}</td></tr>`;
        }).join('')}
      </tbody></table>
    </div>`
    : '';

  const legendRows = companies.map((c, i) => {
    const bits = arContactBits(c.vat);
    if (!bits.length) return '';
    return `<tr id="ar-contact-${i}"><td style="font-weight:600;white-space:nowrap;">${escapeHtml(c.name)}</td><td class="ar-mono">${escapeHtml(c.vat || '')}</td><td>${bits.join(' &nbsp;·&nbsp; ')}</td></tr>`;
  }).join('');
  const legendHtml = legendRows
    ? `<div style="margin-top:28px;">
      <div style="font-size:16px;font-weight:700;margin-bottom:6px;">Υπόμνημα — Στοιχεία επικοινωνίας πελατών</div>
      <table class="ar-consolidated-table"><thead><tr><th>Επωνυμία</th><th>ΑΦΜ</th><th>Επικοινωνία</th></tr></thead><tbody>${legendRows}</tbody></table>
    </div>`
    : '';

  return `
  <div>
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">
      <div style="font-size:20px;font-weight:700;">Λογιστικό Αποτέλεσμα (Συγκεντρωτική)</div>
      <div style="font-size:12px;font-weight:600;">Ημερομηνία: ${todayStr()}</div>
    </div>
    <table class="ar-consolidated-table">
      <thead><tr>
        <th>Κωδ.</th><th>Επωνυμία</th><th>ΑΦΜ</th><th>Ημερ. Υπολ.</th><th>Από</th><th>Έως</th>
        <th>Απ. Έναρξης</th><th>Αγορές Χρ.</th><th>Απ. Τέλους</th><th>Κόστος Πωλ.</th><th>Δαπάνες</th>
        <th>Ακ. Έσοδα Βιβ.</th><th>Ακ. Έσοδα Αυτ.</th><th>Εκκρεμ. myDATA (Αχαρακτ.)</th>
        <th>Φορολογητέα Κέρδη</th><th>Τελ. Κέρδη Β.Α.</th><th>ΦΠΑ</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    ${notesHtml}
    ${legendHtml}
  </div>`;
}

// ---------------- Inventory resolution popups ----------------

function showManualInventoryModal(title, opening) {
  moveModalsToBody(); // see showArOverlay for why this defensive call is needed
  return new Promise((resolve) => {
    const modal = document.getElementById('arManualInvModal');
    document.getElementById('arManualInvTitle').textContent = title;
    const fields = document.getElementById('arManualInvFields');
    fields.innerHTML = '';
    (window.AR_STOCK_CODES || []).forEach((code) => {
      const wrap = document.createElement('div');
      const label = document.createElement('label');
      label.className = 'block text-xs text-gray-600 mb-1';
      label.textContent = code + ' ' + (window.AR_STOCK_LABELS[code] || '');
      const input = document.createElement('input');
      input.type = 'number';
      input.step = '0.01';
      input.className = 'border rounded px-2 py-1 w-full text-sm';
      input.value = (opening && opening[code] != null) ? opening[code] : 0;
      input.dataset.code = code;
      wrap.appendChild(label);
      wrap.appendChild(input);
      fields.appendChild(wrap);
    });
    modal.classList.remove('hidden');

    const saveBtn = document.getElementById('arManualInvSave');
    const cancelBtn = document.getElementById('arManualInvCancel');

    function cleanup() {
      modal.classList.add('hidden');
      saveBtn.removeEventListener('click', onSave);
      cancelBtn.removeEventListener('click', onCancel);
    }
    function onSave() {
      const values = {};
      fields.querySelectorAll('input').forEach((inp) => {
        values[inp.dataset.code] = parseFloat(inp.value) || 0;
      });
      cleanup();
      resolve(values);
    }
    function onCancel() {
      cleanup();
      resolve(null);
    }
    saveBtn.addEventListener('click', onSave);
    cancelBtn.addEventListener('click', onCancel);
  });
}

async function resolveInventoryForCompany(name, vat, year, opening, dateFrom, dateTo) {
  const choice = await showModalChoice(
    `Άγνωστο απόθεμα λήξης — ${name} (${year})`,
    'Δεν έχει καταχωρηθεί απόθεμα λήξης για αυτή την εταιρία/έτος. Επιλέξτε πώς θα υπολογιστεί:',
    [
      { key: 'manual', label: 'Καταχώρηση χειροκίνητα' },
      { key: 'pct10_up', label: '+10% επί έναρξης' },
      { key: 'pct10_down', label: '-10% επί έναρξης' },
      { key: 'same_as_opening', label: 'Ίσο με έναρξη' },
    ]
  );
  if (!choice) return false;

  let value = null;
  if (choice === 'manual') {
    value = await showManualInventoryModal(`Απόθεμα λήξης — ${name} (${year})`, opening);
    if (!value) return false;
  }

  const resp = await postJson('/api/accounting_result/inventory/resolve', {
    credential_name: name, year, method: choice, value, date_from: dateFrom, date_to: dateTo,
  });
  return !!resp.ok;
}

// ---------------- Payroll monthly-completeness resolution ----------------

var AR_MONTH_LABELS = ['Ιαν', 'Φεβ', 'Μαρ', 'Απρ', 'Μάι', 'Ιούν', 'Ιούλ', 'Αύγ', 'Σεπ', 'Οκτ', 'Νοέ', 'Δεκ'];

// The Από/Έως fields are flatpickr'd to dd/mm/yyyy (see ddmmyyyy's own
// comment), NOT native <input type="date"> — so `new Date(dateStr)` silently
// mis-parses or Invalid-Dates a "16/09/2026" string (JS's slash-separated
// parser assumes US mm/dd/yyyy), which is exactly what made the manual
// payroll modal render with zero month fields. Parsed directly via regex
// instead of going through Date at all, mirroring yearFromDMY's own
// dd/mm/yyyy-first-ISO-fallback handling.
function _arParseDmyOrIso(dateStr) {
  const s = String(dateStr || '').trim();
  let m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) return { year: parseInt(m[3], 10), month: parseInt(m[2], 10) - 1 };
  m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) return { year: parseInt(m[1], 10), month: parseInt(m[2], 10) - 1 };
  return null;
}

// Every calendar month touched by dateFrom..dateTo, as {key:"YYYY-MM", label}
// — feeds the manual monthly-totals entry form (one field per month) the
// same way check_monthly_completeness counts "expected months" server-side.
function monthsInRange(dateFrom, dateTo) {
  const from = _arParseDmyOrIso(dateFrom);
  const to = _arParseDmyOrIso(dateTo);
  const months = [];
  if (!from || !to) return months;
  let y = from.year;
  let m = from.month;
  const endY = to.year;
  const endM = to.month;
  while (y < endY || (y === endY && m <= endM)) {
    const key = `${y}-${String(m + 1).padStart(2, '0')}`;
    months.push({ key, label: `${AR_MONTH_LABELS[m]} ${y}` });
    m += 1;
    if (m > 11) { m = 0; y += 1; }
  }
  return months;
}

// `prefill` ({"YYYY-MM": amount}, optional) seeds each month's field with
// what myDATA already found for it (see engine.monthly_totals_for_code)
// instead of leaving every field at 0 — the accountant only needs to type
// over the genuinely missing months, and can see at a glance what's
// already on file for the rest. Resolves to:
//   - null: cancelled outright (Άκυρο)
//   - {__continue: true}: proceed with myDATA's own totals as-is (Συνέχεια),
//     same as picking "Συνέχεια με τα τρέχοντα στοιχεία" on the choice modal
//     one step back, without having to close this form to get there
//   - {"YYYY-MM": amount, ...}: the entered/edited totals (Αποθήκευση)
function showManualPayrollModal(title, months, prefill) {
  moveModalsToBody();
  return new Promise((resolve) => {
    const modal = document.getElementById('arManualPayrollModal');
    document.getElementById('arManualPayrollTitle').textContent = title;
    const fields = document.getElementById('arManualPayrollFields');
    fields.innerHTML = '';
    months.forEach(({ key, label }) => {
      const wrap = document.createElement('div');
      const lbl = document.createElement('label');
      lbl.className = 'block text-xs text-gray-600 mb-1';
      lbl.textContent = label;
      const input = document.createElement('input');
      input.type = 'number';
      input.step = '0.01';
      input.className = 'border rounded px-2 py-1 w-full text-sm';
      input.value = (prefill && prefill[key] != null) ? prefill[key] : 0;
      input.dataset.key = key;
      wrap.appendChild(lbl);
      wrap.appendChild(input);
      fields.appendChild(wrap);
    });
    modal.classList.remove('hidden');

    const saveBtn = document.getElementById('arManualPayrollSave');
    const continueBtn = document.getElementById('arManualPayrollContinue');
    const cancelBtn = document.getElementById('arManualPayrollCancel');
    function cleanup() {
      modal.classList.add('hidden');
      saveBtn.removeEventListener('click', onSave);
      continueBtn.removeEventListener('click', onContinue);
      cancelBtn.removeEventListener('click', onCancel);
    }
    function onSave() {
      const values = {};
      fields.querySelectorAll('input').forEach((inp) => {
        const v = parseFloat(inp.value) || 0;
        if (v) values[inp.dataset.key] = v;
      });
      cleanup();
      resolve(values);
    }
    function onContinue() {
      cleanup();
      resolve({ __continue: true });
    }
    function onCancel() {
      cleanup();
      resolve(null);
    }
    saveBtn.addEventListener('click', onSave);
    continueBtn.addEventListener('click', onContinue);
    cancelBtn.addEventListener('click', onCancel);
  });
}

// Mirrors resolveInventoryForCompany's exact UX, per the user's own request
// that this work "like the inventory check": a blocking modal choice, then
// (for "manual") a per-month numeric entry form, POSTed to a resolve
// endpoint so the same question isn't asked again for this company/year.
async function resolvePayrollForCompany(name, year, dateFrom, dateTo, payrollCheck) {
  const found = payrollCheck ? payrollCheck.found_months : '?';
  const expected = payrollCheck ? payrollCheck.expected_months : '?';
  const choice = await showModalChoice(
    `Ελλιπείς εγγραφές μισθοδοσίας — ${name} (${year})`,
    `Βρέθηκαν ${found} από ${expected} αναμενόμενες μηνιαίες εγγραφές μισθοδοσίας (κωδ. 581) στην περίοδο. ` +
    'Μπορεί η εταιρία να σταμάτησε να έχει μισθοδοσία εντός του έτους. Πώς θέλετε να προχωρήσετε;',
    [
      { key: 'manual', label: 'Καταχώρηση μηνιαίων συνόλων' },
      { key: 'skip', label: 'Συνέχεια με τα τρέχοντα στοιχεία' },
    ]
  );
  if (!choice) return false;

  let resolution = choice;
  let monthlyTotals = {};
  if (choice === 'manual') {
    const months = monthsInRange(dateFrom, dateTo);
    showArOverlay('Λήψη δεδομένων από myDATA...', 'Έλεγχος μηνιαίων ποσών μισθοδοσίας ανά μήνα - η διαδικασία μπορεί να διαρκέσει.');
    const prefillResp = await postJson('/api/accounting_result/payroll/monthly_totals', {
      credential_name: name, date_from: dateFrom, date_to: dateTo,
    });
    hideArOverlay();
    const values = await showManualPayrollModal(`Μηνιαία σύνολα μισθοδοσίας — ${name}`, months, prefillResp.ok ? prefillResp.monthly_totals : null);
    if (!values) return false;
    if (values.__continue) {
      resolution = 'skip';
    } else {
      monthlyTotals = values;
    }
  }

  const resp = await postJson('/api/accounting_result/payroll/resolve', {
    credential_name: name, year, resolution, monthly_totals: monthlyTotals,
  });
  return !!resp.ok;
}

// ---------------- Rent monthly-completeness resolution ----------------

// Same UX as resolvePayrollForCompany (reuses the same generic monthly-
// totals modal), for ενοίκια (Ε3 code 585/014).
async function resolveRentForCompany(name, year, dateFrom, dateTo, rentCheck) {
  const found = rentCheck ? rentCheck.found_months : '?';
  const expected = rentCheck ? rentCheck.expected_months : '?';
  const choice = await showModalChoice(
    `Ελλιπείς εγγραφές ενοικίου — ${name} (${year})`,
    `Βρέθηκαν ${found} από ${expected} αναμενόμενες μηνιαίες εγγραφές ενοικίου (κωδ. 585/014) στην περίοδο. ` +
    'Μπορεί η εταιρία να άλλαξε/έληξε τη μίσθωση εντός του έτους. Πώς θέλετε να προχωρήσετε;',
    [
      { key: 'manual', label: 'Καταχώρηση μηνιαίων συνόλων' },
      { key: 'skip', label: 'Συνέχεια με τα τρέχοντα στοιχεία' },
    ]
  );
  if (!choice) return false;

  let resolution = choice;
  let monthlyTotals = {};
  if (choice === 'manual') {
    const months = monthsInRange(dateFrom, dateTo);
    showArOverlay('Λήψη δεδομένων από myDATA...', 'Έλεγχος μηνιαίων ποσών ενοικίου ανά μήνα - η διαδικασία μπορεί να διαρκέσει.');
    const prefillResp = await postJson('/api/accounting_result/rent/monthly_totals', {
      credential_name: name, date_from: dateFrom, date_to: dateTo,
    });
    hideArOverlay();
    const values = await showManualPayrollModal(`Μηνιαία σύνολα ενοικίου — ${name}`, months, prefillResp.ok ? prefillResp.monthly_totals : null);
    if (!values) return false;
    if (values.__continue) {
      resolution = 'skip';
    } else {
      monthlyTotals = values;
    }
  }

  const resp = await postJson('/api/accounting_result/rent/resolve', {
    credential_name: name, year, resolution, monthly_totals: monthlyTotals,
  });
  return !!resp.ok;
}

// ---------------- Μαζικός pre-check: ΑΑΔΕ + grouped-by-type resolution ----------------

// Kinds of findings a report note can carry, with the marker the
// consolidated PDF puts next to the affected cell (see
// buildConsolidatedTableHtml) and the label used in grouped popups.
var AR_NOTE_TYPES = {
  payroll_shortfall: { label: 'Μισθοδοσία (κωδ. 581)', mark: '*', cell: 'expenses' },
  rent_shortfall: { label: 'Ενοίκιο (κωδ. 585/014)', mark: '**', cell: 'expenses' },
  efka_self_employed_shortfall: { label: 'ΕΦΚΑ Μη-Μισθωτών (κωδ. 585/007)', mark: '***', cell: 'expenses' },
  uncharacterized_last_quarter: { label: 'Αχαρακτήριστα παραστατικά (τελευταίο τρίμηνο)', mark: '†', cell: 'unclassified' },
};

function arLegalKindBadge(kind) {
  if (kind === 'sole_proprietor') return '<span style="background:#dbeafe;color:#1d4ed8;border:1px solid #93c5fd;border-radius:9999px;padding:0 0.45rem;font-size:0.7rem;font-weight:700;">Ατομική</span>';
  if (kind === 'legal_entity') return '<span style="background:#f0fdf4;color:#166534;border:1px solid #86efac;border-radius:9999px;padding:0 0.45rem;font-size:0.7rem;font-weight:700;">Εταιρία</span>';
  return '<span style="color:#94a3b8;">—</span>';
}

function arEfkaFindingText(check, legalKind) {
  if (!check) return 'Πιθανή οφειλή — έλεγξε ΚΕΑΟ';
  let t = `Βρέθηκαν ${check.found_months} από ${check.expected_months} πληρωμές`;
  if (check.partners) t += ` (${check.months} μήνες × ${check.partners} εταίροι)`;
  else if (legalKind === 'legal_entity') t += ' (εταίροι άγνωστοι — υπολογισμός ανά μήνα)';
  return t;
}

// Step 1 of Μαζικός: before anything is checked, make sure every selected
// company's type, contact details and ΦΠΑ are on file (one ΑΑΔΕ login each,
// skipped when already stored) and — for companies — their partners, so the
// ΕΦΚΑ check counts months × partners and its dropdown only offers the
// options that apply to that company type. All the profile logins run
// FIRST; the (heavy, Playwright) partner fetches start only afterwards, one
// at a time — running them next to the next companies' logins starved the
// server and made those logins fail. Returns {warnings, aborted}.
async function arBulkAadePrecheck(names, jobId) {
  const creds = window.AR_CREDENTIALS || [];
  const warnings = [];
  const needMembers = [];
  for (let i = 0; i < names.length; i++) {
    if (await isBulkAbortRequested(jobId)) { hideArOverlay(); return { warnings, aborted: true }; }
    const cred = creds.find((c) => c.name === names[i]);
    const vat = cred && cred.vat;
    if (!vat) continue;
    const label = `Βήμα 1/3 — ΑΑΔΕ (τύπος, επικοινωνία, ΦΠΑ): ${names[i]} (${i + 1}/${names.length})`;
    showArOverlay('Έλεγχος ΑΑΔΕ...', label);
    setBulkCrossPageLabel(label);
    const body = { afm: vat, apply_vat: true, only_if_missing: true, defer_members: true };
    let r = await postJson('/api/accounting_result/company_info', body);
    if (!r.ok && !/TAXISnet/.test(r.error || '')) {
      await new Promise((res) => setTimeout(res, 3000));
      r = await postJson('/api/accounting_result/company_info', body);
    }
    if (!r.ok) warnings.push(`${names[i]}: ${r.error || 'σφάλμα'}`);
    else if (r.needs_members || r.members_pending) needMembers.push(vat);
  }
  if (needMembers.length) {
    await postJson('/api/accounting_result/company_info/fetch_members', { afms: needMembers });
    const deadline = Date.now() + 90000 * needMembers.length;
    let left = needMembers;
    while (left.length && Date.now() < deadline) {
      if (await isBulkAbortRequested(jobId)) { hideArOverlay(); return { warnings, aborted: true }; }
      const label = `Βήμα 1/3 — ανάκτηση εταίρων (για ΕΦΚΑ: μήνες × εταίροι): απομένουν ${left.length}`;
      showArOverlay('Έλεγχος ΑΑΔΕ...', label);
      setBulkCrossPageLabel(label);
      await new Promise((res) => setTimeout(res, 5000));
      try {
        const st = await (await fetch('/api/accounting_result/company_info/members_pending?afms=' + encodeURIComponent(left.join(',')))).json();
        if (st.ok) left = st.pending || [];
      } catch (_) { /* retry on the next tick */ }
    }
    if (left.length) warnings.push(`Οι εταίροι δεν ανακτήθηκαν έγκαιρα για ${left.length} εταιρίες — ο έλεγχος ΕΦΚΑ τους γίνεται ανά μήνα.`);
  }
  hideArOverlay();
  return { warnings, aborted: false };
}

// Pre-check findings grouped by KIND (one table per απόθεμα/μισθοδοσία/
// ενοίκιο/ΕΦΚΑ, a row per company) instead of one screen per company.
function arBuildCheckGroups(rows) {
  const ok = (rows || []).filter((r) => !r.error);
  const monthly = (c) => (c ? `Βρέθηκαν ${c.found_months} από ${c.expected_months} μηνιαίες εγγραφές` : '');
  return [
    {
      key: 'inventory', title: '📦 Απόθεμα λήξης',
      rows: ok.filter((r) => r.inventory_applicable && !r.closing_inventory_known),
      finding: (r) => (r.inventory_obligation_reason === 'turnover_threshold' ? 'Νέα υποχρέωση (πωλήσεις > 150.000€) — δεν έχει καταχωρηθεί' : 'Δεν έχει καταχωρηθεί απόθεμα λήξης'),
      options: () => [
        { key: 'manual', label: 'Χειροκίνητα' },
        { key: 'same_as_opening', label: 'Ίσο με έναρξη' },
        { key: 'pct10_up', label: '+10% επί έναρξης' },
        { key: 'pct10_down', label: '-10% επί έναρξης' },
      ],
    },
    {
      key: 'payroll', title: '💼 Μισθοδοσία (κωδ. 581)',
      rows: ok.filter((r) => r.payroll_needs_input),
      finding: (r) => monthly(r.payroll_check),
      options: () => [{ key: 'manual', label: 'Μηνιαία σύνολα (χειροκίνητα)' }, { key: 'skip', label: 'Συνέχεια με τα τρέχοντα' }],
    },
    {
      key: 'rent', title: '🏠 Ενοίκιο (κωδ. 585/014)',
      rows: ok.filter((r) => r.rent_needs_input),
      finding: (r) => monthly(r.rent_check),
      options: () => [{ key: 'manual', label: 'Μηνιαία σύνολα (χειροκίνητα)' }, { key: 'skip', label: 'Συνέχεια με τα τρέχοντα' }],
    },
    {
      key: 'efka', title: '🧾 ΕΦΚΑ Μη-Μισθωτών (κωδ. 585/007)',
      rows: ok.filter((r) => r.efka_shortfall),
      finding: (r) => arEfkaFindingText(r.efka_check, r.legal_kind),
      options: (r) => {
        const applicable = AR_EFKA_EXCEPTION_OPTIONS.filter((o) => !r.legal_kind || o.legalKind === r.legal_kind);
        return [{ key: '', label: 'Σημείωση στην αναφορά (έλεγξε ΚΕΑΟ)' }, ...applicable, AR_EFKA_MANUAL_OPTION];
      },
    },
  ].filter((g) => g.rows.length);
}

// Resolves to {groupKey: {companyName: choiceKey}} or null (cancelled).
function showGroupedChecksModal(groups) {
  return new Promise((resolve) => {
    const optionsHtml = (opts) => opts.map((o) => `<option value="${escapeHtml(o.key)}">${escapeHtml(o.label)}</option>`).join('');
    const sectionsHtml = groups.map((g) => {
      // «Όλες» offers only the options every row of the group has (the ΕΦΚΑ
      // exception reason differs per company type).
      const common = g.options(g.rows[0]).filter((o) => g.rows.every((r) => g.options(r).some((x) => x.key === o.key)));
      const trs = g.rows.map((r) => `<tr>
          <td style="white-space:nowrap;">${escapeHtml(r.name)}</td>
          <td>${arLegalKindBadge(r.legal_kind)}</td>
          <td style="font-size:0.78rem;">${escapeHtml(g.finding(r))}</td>
          <td><select class="ar-gc-select border rounded px-1 py-0.5 text-xs" data-group="${g.key}" data-name="${escapeHtml(r.name)}">${optionsHtml(g.options(r))}</select></td>
        </tr>`).join('');
      return `<div class="mb-4">
        <div class="flex items-center justify-between gap-2 mb-1">
          <div class="font-semibold text-sm">${g.title} — ${g.rows.length} εταιρίες</div>
          <label class="text-xs text-gray-600">Όλες:
            <select class="ar-gc-all border rounded px-1 py-0.5 text-xs" data-group="${g.key}"><option value="__">—</option>${optionsHtml(common)}</select>
          </label>
        </div>
        <table class="ar-bulk-summary-table"><thead><tr><th>Εταιρία</th><th>Τύπος</th><th>Εύρημα</th><th>Ενέργεια</th></tr></thead><tbody>${trs}</tbody></table>
      </div>`;
    }).join('');
    const modal = document.createElement('div');
    modal.className = 'fixed inset-0 flex items-center justify-center bg-black/40 z-[110000]';
    modal.setAttribute('data-managed', '1');
    modal.innerHTML = `
      <div class="modal-warning-panel w-11/12" style="max-width:56rem;max-height:88vh;overflow-y:auto;">
        <div class="modal-warning-title">Διαφορές προελέγχου — ανά είδος</div>
        <div class="modal-warning-body">
          <p class="text-xs text-gray-500 mb-3">Διάλεξε ενέργεια ανά εταιρία (ή για όλες μιας ομάδας). Τα «χειροκίνητα» ζητούνται αμέσως μετά, ένα-ένα.</p>
          ${sectionsHtml}
        </div>
        <div class="modal-warning-actions">
          <button type="button" class="modal-warning-btn modal-warning-btn--muted" id="arGcCancel">Άκυρο</button>
          <button type="button" class="modal-warning-btn" id="arGcRun">Συνέχεια</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    modal.querySelectorAll('.ar-gc-all').forEach((sel) => {
      sel.addEventListener('change', () => {
        if (sel.value === '__') return;
        modal.querySelectorAll(`.ar-gc-select[data-group="${sel.dataset.group}"]`).forEach((s) => { s.value = sel.value; });
      });
    });
    const finish = (val) => { document.removeEventListener('keydown', onKey); modal.remove(); resolve(val); };
    const onKey = (e) => { if (e.key === 'Escape') finish(null); };
    document.addEventListener('keydown', onKey);
    modal.querySelector('#arGcCancel').addEventListener('click', () => finish(null));
    modal.querySelector('#arGcRun').addEventListener('click', () => {
      const out = {};
      modal.querySelectorAll('.ar-gc-select').forEach((s) => {
        (out[s.dataset.group] = out[s.dataset.group] || {})[s.dataset.name] = s.value;
      });
      finish(out);
    });
  });
}

var AR_MONTHLY_ENDPOINTS = {
  payroll: { totals: '/api/accounting_result/payroll/monthly_totals', label: 'μισθοδοσίας' },
  rent: { totals: '/api/accounting_result/rent/monthly_totals', label: 'ενοικίου' },
  efka: { totals: '/api/accounting_result/efka_self_employed/monthly_totals', label: 'ΕΦΚΑ Μη-Μισθωτών' },
};

// Prefilled monthly-totals form for one company; same return values as
// showManualPayrollModal.
async function arAskMonthlyTotals(kind, name, dateFrom, dateTo) {
  const ep = AR_MONTHLY_ENDPOINTS[kind];
  showArOverlay('Λήψη δεδομένων από myDATA...', `Έλεγχος μηνιαίων ποσών ${ep.label} — ${name}...`);
  const prefill = await postJson(ep.totals, { credential_name: name, date_from: dateFrom, date_to: dateTo });
  hideArOverlay();
  return showManualPayrollModal(`Μηνιαία σύνολα ${ep.label} — ${name}`, monthsInRange(dateFrom, dateTo), prefill.ok ? prefill.monthly_totals : null);
}

// Applies the grouped popup's choices; manual ones open their entry form
// one company at a time. false = the user cancelled a form (stop the run).
async function applyGroupedChecks(choices, rows, year, dateFrom, dateTo, statusEl) {
  const rowByName = new Map((rows || []).map((r) => [r.name, r]));
  for (const [name, method] of Object.entries(choices.inventory || {})) {
    let value = null;
    if (method === 'manual') {
      value = await showManualInventoryModal(`Απόθεμα λήξης — ${name} (${year})`, (rowByName.get(name) || {}).opening_inventory);
      if (!value) return false;
    }
    if (statusEl) statusEl.textContent = `Απόθεμα λήξης — ${name}...`;
    await postJson('/api/accounting_result/inventory/resolve', {
      credential_name: name, year, method, value, date_from: dateFrom, date_to: dateTo,
    });
  }
  for (const kind of ['payroll', 'rent']) {
    for (const [name, choice] of Object.entries(choices[kind] || {})) {
      let resolution = 'skip';
      let monthlyTotals = {};
      if (choice === 'manual') {
        const values = await arAskMonthlyTotals(kind, name, dateFrom, dateTo);
        if (!values) return false;
        if (!values.__continue && Object.keys(values).length) { resolution = 'manual'; monthlyTotals = values; }
      }
      await postJson(`/api/accounting_result/${kind}/resolve`, { credential_name: name, year, resolution, monthly_totals: monthlyTotals });
    }
  }
  for (const [name, choice] of Object.entries(choices.efka || {})) {
    if (!choice) continue;
    if (choice === 'manual_totals') {
      const values = await arAskMonthlyTotals('efka', name, dateFrom, dateTo);
      if (!values) return false;
      if (values.__continue || !Object.keys(values).length) continue;
      await postJson('/api/accounting_result/efka_self_employed/resolve_totals', { credential_name: name, year, monthly_totals: values });
    } else {
      await postJson('/api/accounting_result/efka_self_employed/resolve', { credential_name: name, year, reason: choice });
    }
  }
  return true;
}

// After a Μαζικός run: every finding (report note) grouped by kind, a row
// per company — the «📋 Διαφορές ανά είδος» button of the results flash.
function showBulkNotesByTypeModal(results) {
  const byType = {};
  // Companies that got no result at all come first, with the reason.
  (results || []).forEach((r) => {
    const reason = r.error || (r.needs_payroll_input ? 'εκκρεμεί μισθοδοσία'
      : r.needs_rent_input ? 'εκκρεμεί ενοίκιο' : r.needs_inventory_input ? 'εκκρεμεί απόθεμα λήξης' : '');
    if (!r.ok || reason) (byType.__failed = byType.__failed || []).push({ name: r.credential_name, message: reason || 'σφάλμα' });
  });
  (results || []).forEach((r) => {
    (r.notes || []).forEach((n) => {
      (byType[n.type] = byType[n.type] || []).push({ name: r.credential_name, message: n.message });
    });
  });
  const types = Object.keys(byType);
  const body = types.length
    ? types.map((t) => {
      const info = t === '__failed' ? { label: '❌ Δεν υπολογίστηκαν' } : (AR_NOTE_TYPES[t] || { label: t });
      const trs = byType[t].map((x) => `<tr><td style="white-space:nowrap;">${escapeHtml(x.name)}</td><td style="font-size:0.78rem;">${escapeHtml(x.message)}</td></tr>`).join('');
      return `<div class="mb-4"><div class="font-semibold text-sm mb-1">${escapeHtml(info.label)} — ${byType[t].length} εταιρίες</div>
        <table class="ar-bulk-summary-table"><thead><tr><th>Εταιρία</th><th>Παρατήρηση</th></tr></thead><tbody>${trs}</tbody></table></div>`;
    }).join('')
    : '<p class="text-sm">Δεν βρέθηκαν διαφορές.</p>';
  const modal = document.createElement('div');
  modal.className = 'fixed inset-0 flex items-center justify-center bg-black/40 z-[110000]';
  modal.setAttribute('data-managed', '1');
  modal.innerHTML = `
    <div class="modal-warning-panel w-11/12" style="max-width:56rem;max-height:88vh;overflow-y:auto;">
      <div class="modal-warning-title">📋 Διαφορές ανά είδος</div>
      <div class="modal-warning-body">${body}</div>
      <div class="modal-warning-actions"><button type="button" class="modal-warning-btn" id="arNotesClose">Κλείσιμο</button></div>
    </div>`;
  document.body.appendChild(modal);
  const close = () => { document.removeEventListener('keydown', onKey); modal.remove(); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  modal.querySelector('#arNotesClose').addEventListener('click', close);
  modal.addEventListener('mousedown', (e) => { if (e.target === modal) close(); });
}

// ---------------- ΕΦΚΑ Μη-Μισθωτών exception ----------------

var AR_EFKA_EXCEPTION_OPTIONS = [
  { key: 'sole_prop_also_employed', label: 'Ατομική επιχ. — ο πελάτης είναι παράλληλα μισθωτός', legalKind: 'sole_proprietor' },
  { key: 'company_partners_exempt', label: 'Εταιρία — οι εταίροι έχουν δικές τους ατομικές επιχειρήσεις', legalKind: 'legal_entity' },
];
// Always offered, whatever the company type: key in the totals by hand
// (per month or as a lump sum), like payroll/rent.
var AR_EFKA_MANUAL_OPTION = { key: 'manual_totals', label: 'Καταχώρηση συνόλων ΕΦΚΑ (χειροκίνητα, ανά μήνα ή σύνολο)' };

async function resolveEfkaSelfEmployedException(name, year, legalKind, dateFrom, dateTo) {
  // Only offer the one reason that actually matches this company's known
  // type (from the ΑΑΔΕ Μητρώο auto-detect — see report.legal_kind) instead
  // of always showing both; when it's not known yet, fall back to both
  // rather than guessing.
  const options = AR_EFKA_EXCEPTION_OPTIONS.filter((o) => !legalKind || o.legalKind === legalKind);
  const choice = await showModalChoice(
    `Εξαίρεση ΕΦΚΑ Μη-Μισθωτών — ${name} (${year})`,
    'Γιατί δεν θεωρείτε την εταιρία υπόχρεη σε ΕΦΚΑ Μη-Μισθωτών; Η επιλογή αποθηκεύεται και η σημείωση δεν θα ξαναεμφανιστεί για αυτό το έτος.',
    [...(options.length ? options : AR_EFKA_EXCEPTION_OPTIONS), AR_EFKA_MANUAL_OPTION],
  );
  if (!choice) return false;
  if (choice === 'manual_totals') {
    if (!dateFrom || !dateTo) {
      showArFlash('Δεν βρέθηκε η περίοδος — ξαναϋπολόγισε την εταιρία και δοκίμασε ξανά.', 'warning', 6000);
      return false;
    }
    showArOverlay('Λήψη δεδομένων από myDATA...', 'Έλεγχος μηνιαίων ποσών ΕΦΚΑ Μη-Μισθωτών ανά μήνα - η διαδικασία μπορεί να διαρκέσει.');
    const prefillResp = await postJson('/api/accounting_result/efka_self_employed/monthly_totals', {
      credential_name: name, date_from: dateFrom, date_to: dateTo,
    });
    hideArOverlay();
    const values = await showManualPayrollModal(`Μηνιαία σύνολα ΕΦΚΑ Μη-Μισθωτών — ${name}`, monthsInRange(dateFrom, dateTo), prefillResp.ok ? prefillResp.monthly_totals : null);
    if (!values || values.__continue || !Object.keys(values).length) return false;
    const r = await postJson('/api/accounting_result/efka_self_employed/resolve_totals', {
      credential_name: name, year, monthly_totals: values,
    });
    if (r.ok) showArFlash(`Αποθηκεύτηκαν τα σύνολα ΕΦΚΑ για ${name} — πάτησε ξανά «Υπολογισμός» για να εφαρμοστούν στο αποτέλεσμα.`, 'success', 8000);
    else showArFlash('Σφάλμα αποθήκευσης συνόλων ΕΦΚΑ: ' + (r.error || ''), 'error', 7000);
    return !!r.ok;
  }
  const resp = await postJson('/api/accounting_result/efka_self_employed/resolve', {
    credential_name: name, year, reason: choice,
  });
  if (resp.ok) showArFlash(`Αποθηκεύτηκε η εξαίρεση ΕΦΚΑ Μη-Μισθωτών για ${name}.`, 'success', 5000);
  return !!resp.ok;
}

// ---------------- Report notes (payroll / ΕΦΚΑ Μη-Μισθωτών / αχαρακτήριστα) ----------------

function renderReportNotesHtml(notes, name, year, legalKind, from, to) {
  if (!notes || !notes.length) return '';
  const items = notes.map((n) => {
    const exceptionBtn = n.type === 'efka_self_employed_shortfall'
      ? ` <button type="button" class="ar-efka-exception-btn" data-html2canvas-ignore="true" data-name="${escapeHtml(name)}" data-year="${year}" data-legal-kind="${escapeHtml(legalKind || '')}" data-from="${escapeHtml(from || '')}" data-to="${escapeHtml(to || '')}" style="font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid #ccc;background:#fff;cursor:pointer;">🔧 εξαίρεση / σύνολα</button>`
      : '';
    return `<li style="display:list-item;list-style-type:disc;margin-bottom:2px;">${escapeHtml(n.message)}${exceptionBtn}</li>`;
  }).join('');
  // Explicit disc bullets (Tailwind's preflight resets list-style to none)
  // so each note reads as its own line, on screen and in the PDF; the
  // exception button is web-only (data-html2canvas-ignore keeps it out of
  // the exported PDF).
  return `<div class="ar-notes" style="margin-top:6px;"><strong>Σημειώσεις:</strong><ul style="margin:4px 0 0 18px;padding:0;list-style-type:disc;">${items}</ul></div>`;
}

function bindReportNoteButtons(container) {
  container.querySelectorAll('.ar-efka-exception-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await resolveEfkaSelfEmployedException(btn.dataset.name, btn.dataset.year, btn.dataset.legalKind || null, btn.dataset.from || '', btn.dataset.to || '');
    });
  });
}

// ---------------- Depreciation disambiguation popup ----------------

function showDepreciationPickModal(entries) {
  moveModalsToBody(); // see showArOverlay for why this defensive call is needed
  return new Promise((resolve) => {
    const modal = document.getElementById('arDepPickModal');
    const fields = document.getElementById('arDepPickFields');
    fields.innerHTML = '';
    entries.forEach((e) => {
      const label = document.createElement('label');
      label.className = 'flex items-center gap-2 py-1 border-b last:border-0';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.dataset.mark = e.mark;
      const span = document.createElement('span');
      span.textContent = `MARK ${e.mark} — ${fmtMoney(e.amount)} €`;
      label.appendChild(cb);
      label.appendChild(span);
      fields.appendChild(label);
    });
    modal.classList.remove('hidden');

    const saveBtn = document.getElementById('arDepPickSave');
    const cancelBtn = document.getElementById('arDepPickCancel');
    function cleanup() {
      modal.classList.add('hidden');
      saveBtn.removeEventListener('click', onSave);
      cancelBtn.removeEventListener('click', onCancel);
    }
    function onSave() {
      const marks = Array.from(fields.querySelectorAll('input:checked')).map((cb) => cb.dataset.mark);
      cleanup();
      resolve(marks);
    }
    function onCancel() {
      cleanup();
      resolve(null);
    }
    saveBtn.addEventListener('click', onSave);
    cancelBtn.addEventListener('click', onCancel);
  });
}

async function resolveDepreciationChoice(entries) {
  const choice = await showModalChoice(
    `Πολλαπλές εγγραφές αποσβέσεων (${entries.length})`,
    `Βρέθηκαν ${entries.length} ξεχωριστές εγγραφές αποσβέσεων (κωδ. 587) στο προηγούμενο έτος. ` +
    'Θέλετε να αθροιστούν όλες ή να επιλέξετε συγκεκριμένες;',
    [
      { key: 'sum_all', label: 'Άθροισμα όλων' },
      { key: 'pick', label: 'Επιλογή συγκεκριμένων' },
    ]
  );
  if (!choice) return null;
  if (choice === 'sum_all') return { mode: 'sum_all' };

  const marks = await showDepreciationPickModal(entries);
  if (!marks || !marks.length) return null;
  return { mode: 'marks', marks };
}

// ---------------- Single mode ----------------

async function computeSingle() {
  const name = document.getElementById('arSingleCredential').value;
  const from = document.getElementById('arSingleFrom').value;
  const to = document.getElementById('arSingleTo').value;
  const statusEl = document.getElementById('arSingleStatus');
  document.getElementById('arSinglePdfBtn').classList.add('hidden');
  statusEl.textContent = '';

  if (!name || !from || !to) {
    statusEl.textContent = 'Επιλέξτε εταιρία και περίοδο.';
    return;
  }

  const body = {
    credential_name: name, date_from: from, date_to: to,
    excel_group_totals: window.__arSingleExcelTotals || null,
  };

  try {
    showArOverlay('Λήψη δεδομένων από myDATA...', 'Βήμα 1: έλεγχος αποσβέσεων, μισθοδοσίας, ενοικίου και απογραφής.');
    let resp = await postJson('/api/accounting_result/compute', body);
    if (!resp.ok) {
      statusEl.textContent = 'Σφάλμα: ' + (resp.error || '');
      showArFlash('Λογιστικό Αποτέλεσμα (' + name + '): σφάλμα — ' + (resp.error || ''), 'error');
      return;
    }
    // showArFlash only ever shows ONE banner at a time (a later call replaces
    // an earlier one instantly, see its own comment) — so every advisory
    // message this response can carry is combined into a single flash
    // instead of racing separate calls that would silently drop all but
    // the last one.
    const advisories = [];
    let advisoriesKind = 'success';
    const vatAutoMsg = vatAutoCheckFlashMessage(resp.vat_auto_check, name);
    if (vatAutoMsg) { advisories.push(vatAutoMsg); if (!resp.vat_auto_check.ok) advisoriesKind = 'warning'; }
    const inventoryObligationMsg = inventoryObligationFlashMessage(resp.inventory_obligation, name);
    if (inventoryObligationMsg) { advisories.push(inventoryObligationMsg); advisoriesKind = 'warning'; }
    const booksCategoryMsg = booksCategoryMismatchFlashMessage(resp.books_category_mismatch, name);
    if (booksCategoryMsg) { advisories.push(booksCategoryMsg); advisoriesKind = 'warning'; }
    if (advisories.length) showArFlash(advisories.join(' • '), advisoriesKind, 9000);

    if (resp.needs_depreciation_input) {
      hideArOverlay();
      const sel = await resolveDepreciationChoice(resp.depreciation_entries);
      if (!sel) {
        statusEl.textContent = 'Ακυρώθηκε.';
        return;
      }
      body.depreciation_selection = sel;
      showArOverlay('Λήψη δεδομένων από myDATA...', 'Βήμα 2: επεξεργασία αποσβέσεων, συνέχεια με μισθοδοσία/ενοίκιο/απογραφή.');
      resp = await postJson('/api/accounting_result/compute', body);
      if (!resp.ok) {
        statusEl.textContent = 'Σφάλμα: ' + (resp.error || '');
        showArFlash('Λογιστικό Αποτέλεσμα (' + name + '): σφάλμα — ' + (resp.error || ''), 'error');
        return;
      }
    }

    if (resp.needs_payroll_input) {
      hideArOverlay();
      const resolved = await resolvePayrollForCompany(name, resp.year, from, to, resp.payroll_check);
      if (!resolved) {
        statusEl.textContent = 'Ακυρώθηκε.';
        return;
      }
      showArOverlay('Λήψη δεδομένων από myDATA...', 'Βήμα 3: επεξεργασία μισθοδοσίας, συνέχεια με ενοίκιο/απογραφή.');
      resp = await postJson('/api/accounting_result/compute', body);
      if (!resp.ok) {
        statusEl.textContent = 'Σφάλμα: ' + (resp.error || '');
        showArFlash('Λογιστικό Αποτέλεσμα (' + name + '): σφάλμα — ' + (resp.error || ''), 'error');
        return;
      }
    }

    if (resp.needs_rent_input) {
      hideArOverlay();
      const resolved = await resolveRentForCompany(name, resp.year, from, to, resp.rent_check);
      if (!resolved) {
        statusEl.textContent = 'Ακυρώθηκε.';
        return;
      }
      showArOverlay('Λήψη δεδομένων από myDATA...', 'Βήμα 4: επεξεργασία ενοικίου, συνέχεια με απογραφή.');
      resp = await postJson('/api/accounting_result/compute', body);
      if (!resp.ok) {
        statusEl.textContent = 'Σφάλμα: ' + (resp.error || '');
        showArFlash('Λογιστικό Αποτέλεσμα (' + name + '): σφάλμα — ' + (resp.error || ''), 'error');
        return;
      }
    }

    if (resp.needs_efka_input) {
      hideArOverlay();
      // Asked before the final computation; resolved or cancelled, it is not
      // asked again (efka_skip) — a cancel just leaves the standing note.
      await resolveEfkaSelfEmployedException(name, resp.year, resp.legal_kind || null, from, to);
      body.efka_skip = true;
      showArOverlay('Λήψη δεδομένων από myDATA...', 'Επεξεργασία ΕΦΚΑ Μη-Μισθωτών, συνέχεια με απογραφή και τελικό υπολογισμό.');
      resp = await postJson('/api/accounting_result/compute', body);
      if (!resp.ok) {
        statusEl.textContent = 'Σφάλμα: ' + (resp.error || '');
        showArFlash('Λογιστικό Αποτέλεσμα (' + name + '): σφάλμα — ' + (resp.error || ''), 'error');
        return;
      }
    }

    if (resp.needs_inventory_input) {
      hideArOverlay();
      const resolved = await resolveInventoryForCompany(name, resp.vat, resp.year, resp.opening_inventory, from, to);
      if (!resolved) {
        statusEl.textContent = 'Ακυρώθηκε.';
        return;
      }
      showArOverlay('Λήψη δεδομένων από myDATA...', 'Βήμα 5: επεξεργασία απογραφής και τελικός υπολογισμός αποτελέσματος.');
      resp = await postJson('/api/accounting_result/compute', body);
      if (!resp.ok) {
        statusEl.textContent = 'Σφάλμα: ' + (resp.error || '');
        showArFlash('Λογιστικό Αποτέλεσμα (' + name + '): σφάλμα — ' + (resp.error || ''), 'error');
        return;
      }
    }

    const container = document.getElementById('arSingleReportContainer');
    container.innerHTML = buildReportSectionHtml(name, resp.vat, from, to, resp.report);
    bindReportNoteButtons(container);
    window.__arSingleLastSection = { name, vat: resp.vat, from, to, report: resp.report };
    document.getElementById('arSinglePdfBtn').classList.remove('hidden');
    loadSingleHistory(name);
    // Μισθοδοσία/ΕΦΚΑ Μη-Μισθωτών/αχαρακτήριστα notes only exist once the
    // final report is built (they're not known before the depreciation/
    // payroll/inventory gates above clear) — same treatment as the
    // απογραφή/ΦΠΑ/κατηγορία-βιβλίων advisories: popped as a flash here too,
    // per the user's own request that these behave "like the inventory
    // checks" in both Ατομικός and Μαζικός.
    (resp.report.notes || []).forEach((n) => { advisories.push(n.message); advisoriesKind = 'warning'; });
    // showArResultsFlash clears the transient showArFlash banner above the
    // moment it renders (see its own comment) — often before the user can
    // even read it, since nothing awaits in between on the common no-dialog
    // path. Repeat the same advisories here so they survive in the
    // persistent results banner too.
    let resultMsg = 'Λογιστικό Αποτέλεσμα (Ατομικός): ολοκληρώθηκε — ' + name + ' (Φορολογητέα Κέρδη ' + fmtMoney(resp.report.taxable_result) + ').';
    if (advisories.length) resultMsg += ' ' + advisories.join(' • ');
    showArResultsFlash(
      resultMsg,
      advisoriesKind === 'warning' ? 'warning' : 'success',
      {
        flashId: 'arSingleResultsFlash',
        view: () => container.scrollIntoView({ behavior: 'smooth', block: 'start' }),
        pdf: () => document.getElementById('arSinglePdfBtn').click(),
      },
    );
  } finally {
    hideArOverlay();
  }
}

// ---------------- History ----------------

function renderHistoryTable(entries) {
  if (!entries || !entries.length) return '<div class="text-xs text-gray-500">Δεν υπάρχει ακόμα ιστορικό υπολογισμών για αυτή την εταιρία.</div>';
  const rows = entries.map((e) => {
    const ts = e.timestamp ? new Date(e.timestamp) : null;
    const tsStr = ts ? (String(ts.getDate()).padStart(2, '0') + '/' + String(ts.getMonth() + 1).padStart(2, '0') + '/' + ts.getFullYear() + ' ' + String(ts.getHours()).padStart(2, '0') + ':' + String(ts.getMinutes()).padStart(2, '0')) : '';
    const isBulk = e.mode === 'bulk';
    const deleteBtn = isBulk
      ? `<span class="text-xs text-gray-400" title="Τα αποτελέσματα μαζικού υπολογισμού διατηρούνται — δεν διαγράφονται μεμονωμένα εδώ.">🔒</span>`
      : `<button type="button" class="text-xs px-2 py-1 rounded border border-red-300 text-red-600 hover:bg-red-50 ar-history-delete-btn" data-id="${escapeHtml(e.id || '')}" data-vat="${escapeHtml(e.vat || '')}" title="Διαγραφή">🗑</button>`;
    return `<tr>
      <td>${escapeHtml(tsStr)}</td>
      <td>${escapeHtml(ddmmyyyy(e.date_from))} – ${escapeHtml(ddmmyyyy(e.date_to))}</td>
      <td>${escapeHtml(e.computed_by || '')}</td>
      <td class="ar-num">${fmtMoney(e.taxable_result)}</td>
      <td>${escapeHtml(isBulk ? 'Μαζικός' : 'Ατομικός')}</td>
      <td style="white-space:nowrap;">
        <button type="button" class="text-xs px-2 py-1 rounded border hover:bg-gray-50 ar-history-open-btn" data-id="${escapeHtml(e.id || '')}" data-vat="${escapeHtml(e.vat || '')}" data-name="${escapeHtml(e.credential_name || '')}">📂 Άνοιγμα</button>
        ${deleteBtn}
      </td>
    </tr>`;
  }).join('');
  return `<table class="ar-history-table"><thead><tr>
    <th>Υπολογίστηκε στις</th><th>Περίοδος</th><th>Από</th><th>Φορολογητέα Κέρδη</th><th>Τρόπος</th><th></th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

async function deleteHistoryEntry(vat, entryId, onDone) {
  const res = await fetch('/api/accounting_result/history/' + encodeURIComponent(entryId) + '?vat=' + encodeURIComponent(vat), { method: 'DELETE' });
  const data = await res.json();
  if (data.ok && typeof onDone === 'function') onDone();
  return data;
}

async function openHistoryEntry(vat, entryId, name) {
  const statusEl = document.getElementById('arSingleStatus');
  if (!vat || !entryId) return;
  statusEl.textContent = 'Άνοιγμα αποθηκευμένου αποτελέσματος...';
  try {
    const res = await fetch('/api/accounting_result/history/' + encodeURIComponent(entryId) + '?vat=' + encodeURIComponent(vat));
    const data = await res.json();
    if (!data.ok || !data.entry || !data.entry.report) {
      statusEl.textContent = 'Σφάλμα: ' + (data.error || 'δεν βρέθηκε αποθηκευμένο αποτέλεσμα');
      return;
    }
    const e = data.entry;
    const container = document.getElementById('arSingleReportContainer');
    container.innerHTML = buildReportSectionHtml(e.credential_name || name, e.vat || vat, e.date_from, e.date_to, e.report);
    window.__arSingleLastSection = { name: e.credential_name || name, vat: e.vat || vat, from: e.date_from, to: e.date_to, report: e.report };
    document.getElementById('arSinglePdfBtn').classList.remove('hidden');
    statusEl.textContent = '📂 Προβολή αποθηκευμένου αποτελέσματος από ' + ddmmyyyy(e.date_from) + ' – ' + ddmmyyyy(e.date_to) + ' (υπολογίστηκε ' + new Date(e.timestamp).toLocaleString('el-GR') + ').';
  } catch (err) {
    statusEl.textContent = 'Σφάλμα: ' + String(err);
  }
}

async function loadSingleHistory(name) {
  const wrap = document.getElementById('arSingleHistoryWrap');
  const container = document.getElementById('arSingleHistoryContainer');
  if (!name) { wrap.classList.add('hidden'); return; }
  try {
    const res = await fetch('/api/accounting_result/history?credential_name=' + encodeURIComponent(name));
    const data = await res.json();
    if (!data.ok) { wrap.classList.add('hidden'); return; }
    container.innerHTML = renderHistoryTable(data.history);
    container.querySelectorAll('.ar-history-open-btn').forEach((btn) => {
      btn.addEventListener('click', () => openHistoryEntry(btn.dataset.vat, btn.dataset.id, btn.dataset.name));
    });
    container.querySelectorAll('.ar-history-delete-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        // Same ⚠️-styled centered dialog as the bulk-run delete, for
        // consistency — every "are you sure you want to delete a saved
        // result" prompt on this page now looks the same.
        let proceed = false;
        try {
          proceed = await showModalConfirm(
            'Διαγραφή αποτελέσματος',
            'Διαγραφή αυτού του αποθηκευμένου αποτελέσματος;',
            'Διαγραφή', 'Άκυρο',
          );
        } catch (_) { proceed = false; }
        if (!proceed) return;
        btn.disabled = true;
        const resp = await deleteHistoryEntry(btn.dataset.vat, btn.dataset.id, () => loadSingleHistory(name));
        if (!resp.ok) { btn.disabled = false; showArFlash(resp.error || 'Αποτυχία διαγραφής.', 'error'); }
      });
    });
    wrap.classList.remove('hidden');
  } catch (e) {
    wrap.classList.add('hidden');
  }
}

async function uploadSingleExcel(file) {
  const note = document.getElementById('arSingleExcelNote');
  if (!file) {
    window.__arSingleExcelTotals = null;
    note.classList.add('hidden');
    return;
  }
  const fd = new FormData();
  fd.append('excel_file', file);
  let data;
  try {
    const res = await fetch('/api/accounting_result/upload_excel', { method: 'POST', body: fd });
    data = await res.json();
  } catch (e) {
    data = { ok: false, error: String(e) };
  }
  if (!data.ok) {
    window.__arSingleExcelTotals = null;
    note.textContent = 'Σφάλμα Excel: ' + (data.error || '');
    note.classList.remove('hidden');
    return;
  }
  window.__arSingleExcelTotals = data.group_totals || {};
  const n = Object.keys(window.__arSingleExcelTotals).length;
  note.textContent = `✅ Φορτώθηκαν ${n} λογαριασμοί από το Excel (θα αντικαταστήσουν τα αντίστοιχα ποσά myDATA στον επόμενο υπολογισμό).`;
  note.classList.remove('hidden');
}

// ---------------- DataTables wiring (sortable + searchable) ----------------
// jQuery + DataTables are already vendored/loaded globally (base.html), same
// library already used elsewhere in the app (e.g. epsilon_preview.html).

var AR_DT_LANG = {
  search: '🔎 Αναζήτηση:',
  lengthMenu: 'Εμφάνιση _MENU_ εταιριών',
  info: '_START_–_END_ από _TOTAL_',
  infoEmpty: '0 εταιρίες',
  infoFiltered: '(φιλτραρισμένο από _MAX_)',
  zeroRecords: 'Δεν βρέθηκαν εταιρίες.',
  paginate: { previous: '‹', next: '›' },
};

// Drag a column's right edge to resize (plain DOM, no DataTables extension
// vendored). Delegated resize handles are appended once per <th>; re-running
// this after a DataTable (re)draw is a harmless no-op for th's that already
// have one.
function makeColumnsResizable(tableEl) {
  if (!tableEl) return;
  const ths = tableEl.querySelectorAll('thead th');
  // table-layout:fixed ignores cell content and uses ONLY each column's own
  // explicit width (or, for columns left unset, an even split of whatever's
  // left) — switching to it without first freezing every column's current
  // natural width is what collapsed Επωνυμία/ΑΦΜ down to near-nothing last
  // time (only the checkbox column had an explicit width, from DataTables'
  // own columnDefs). Snapshot each th's rendered width BEFORE flipping the
  // layout mode so the visual proportions carry over unchanged.
  if (tableEl.style.tableLayout !== 'fixed') {
    ths.forEach((th) => {
      if (!th.style.width) th.style.width = th.offsetWidth + 'px';
    });
    tableEl.style.tableLayout = 'fixed';
  }
  ths.forEach((th) => {
    if (th.querySelector('.ar-col-resizer')) return;
    const cs = getComputedStyle(th);
    if (cs.position === 'static') th.style.position = 'relative';
    const handle = document.createElement('span');
    handle.className = 'ar-col-resizer';
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const startX = e.pageX;
      const startWidth = th.offsetWidth;
      function onMove(ev) {
        th.style.width = Math.max(28, startWidth + (ev.pageX - startX)) + 'px';
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
    th.appendChild(handle);
  });
}

// document.querySelectorAll only sees checkboxes in rows a DataTable has
// currently attached to the DOM — rows on any OTHER page of a paginated
// table are detached (not destroyed, just out of the live tree) until the
// user pages back to them, so a plain querySelectorAll silently misses
// anything checked on a page other than the one showing right now. That
// was the actual cause of "select several companies across pages, only 1
// gets processed": every selection/count/lock call below used to go
// straight through document.querySelectorAll. These helpers go through the
// DataTables API instead (table.$(), which knows about every row
// regardless of which page is displayed), falling back to a plain DOM
// query only when the table isn't (yet) a DataTable at all — e.g. jQuery/
// DataTables failed to load.
function arTableCheckboxes(tableSelector, checkboxSelector) {
  const $ = window.jQuery;
  if ($ && $.fn && $.fn.DataTable && $.fn.DataTable.isDataTable(tableSelector)) {
    // table.$() is the LEGACY DataTables shortcut, and unlike .rows()/
    // .cells()/.nodes() (whose selector-modifier defaults to page:'all'),
    // .$() defaults to the CURRENTLY DISPLAYED PAGE ONLY. That silently
    // dropped every checkbox checked on any page other than the one showing
    // at read time — exactly why "select one row, page to another page,
    // select another, delete both" only ever deleted the one on the page
    // left open, and why rows checked on an earlier page then never
    // revisited looked like they could never be deleted at all.
    // table.rows().nodes() is explicit and documented to span every page.
    const table = $(tableSelector).DataTable();
    return $(table.rows().nodes()).find(checkboxSelector).toArray();
  }
  return Array.from(document.querySelectorAll(checkboxSelector));
}
function arTableCheckedValues(tableSelector, checkboxSelector) {
  return arTableCheckboxes(tableSelector, checkboxSelector)
    .filter((cb) => cb.checked)
    .map((cb) => cb.value);
}

function updateArBulkSelectedCount() {
  const el = document.getElementById('arBulkSelectedCount');
  if (!el) return;
  const n = arTableCheckedValues('.ar-bulk-table', '.ar-bulk-cb').length;
  el.textContent = n === 1 ? '1 επιλεγμένη' : `${n} επιλεγμένες`;
}

function updateArSavedSelectedCount() {
  const el = document.getElementById('arSavedSelectedCount');
  if (!el) return;
  const n = arTableCheckedValues('.ar-saved-table', '.ar-saved-cb').length;
  el.textContent = n === 1 ? '1 επιλεγμένη' : `${n} επιλεγμένες`;
}

function initBulkDataTable() {
  if (typeof window.jQuery === 'undefined' || !window.jQuery.fn.DataTable) return;
  const $ = window.jQuery;
  const $table = $('.ar-bulk-table');
  if (!$table.length) return;
  if ($.fn.DataTable.isDataTable($table)) $table.DataTable().destroy();
  $table.DataTable({
    order: [[1, 'asc']],
    pageLength: 25,
    autoWidth: false, // cede all column-width management to makeColumnsResizable()
    columnDefs: [{ orderable: false, searchable: false, targets: 0 }],
    language: AR_DT_LANG,
  });
  makeColumnsResizable($table.get(0));
  updateArBulkSelectedCount();
}

function initSavedDataTable() {
  arInstallNoMydataFilter();
  if (typeof window.jQuery === 'undefined' || !window.jQuery.fn.DataTable) return;
  const $ = window.jQuery;
  const $table = $('.ar-saved-table');
  if (!$table.length) return;
  if ($.fn.DataTable.isDataTable($table)) $table.DataTable().destroy();
  $table.DataTable({
    order: [[2, 'asc']],
    pageLength: 25,
    // Remembers the user's page-length (and sort/search/page) choice in
    // localStorage, keyed by this page's URL — survives both a table
    // rebuild after a delete/import (this function runs again from
    // scratch each time) and a real full/partial page reload.
    // stateDuration:-1 means it never expires on its own.
    stateSave: true,
    stateDuration: -1,
    // Τύπος(3)/ΦΠΑ(4) are now fully searchable+orderable too — they used to
    // be excluded because their content only exists after fillSaved*Cells()
    // fills it in async, which previously never told DataTables it changed
    // (see redrawSavedDataTable). 0=checkbox, 6=action buttons stay excluded
    // since neither has meaningful text to search/sort by.
    columnDefs: [{ orderable: false, searchable: false, targets: [0, 6] }],
    language: AR_DT_LANG,
  });
  updateArSavedSelectedCount();
}

// ---------------- Bulk mode ----------------

// ---------------- Bulk background job (progress + stop) ----------------
// Mirrors the Έλεγχος Ε3 bulk job pattern exactly: the compute POST itself
// runs synchronously on the request thread, but it publishes progress after
// each company and checks an abort flag between companies
// (accounting_result/job_registry.py — same shape as e3_brain's own
// registry). The progress banner itself is NOT page-local — it's the
// cross-page, top-right sticky banner in static/js/base_01.js (same slot/
// style as the E3 Bulk one and as fetch.py's bulk-download banner), driven
// by a sessionStorage flag so it keeps showing (and stays abortable) even
// if the user navigates away from this page while the run continues.
function startBulkCrossPageBanner(jobId, total, label) {
  try {
    sessionStorage.setItem('arBulkActiveJob', JSON.stringify({ jobId, total, startedAt: Date.now(), label: label || '', updatedAt: Date.now() }));
  } catch (_) {}
}

// Label for the cross-page progress flash (static/js/base_01.js) while the
// run is in its browser-driven steps (ΑΑΔΕ/myDATA pre-check, choices), where
// the server has no progress of its own to report yet.
function setBulkCrossPageLabel(label) {
  try {
    const active = JSON.parse(sessionStorage.getItem('arBulkActiveJob') || 'null');
    if (!active) return;
    active.label = label;
    active.updatedAt = Date.now();
    sessionStorage.setItem('arBulkActiveJob', JSON.stringify(active));
  } catch (_) {}
  const span = document.querySelector('#arBulkProgressFlash span');
  if (span) span.textContent = 'Λογιστικό Αποτέλεσμα — ' + label;
}

async function isBulkAbortRequested(jobId) {
  try {
    const d = await (await fetch('/api/accounting_result/bulk_progress/' + encodeURIComponent(jobId), { cache: 'no-store' })).json();
    return !!(d && d.aborted);
  } catch (_) {
    return false;
  }
}

function stopBulkCrossPageBanner() {
  try {
    sessionStorage.removeItem('arBulkActiveJob');
    const el = document.getElementById('arBulkProgressFlash');
    if (el) el.remove();
  } catch (_) {}
}

// base_06.js defines ensureFlashContainer()/showStandardFetchFlash() but wraps
// its entire file in a private IIFE, so neither is ever exposed on window —
// this page keeps its own fixed top-right stack (#arFlashContainer, styled in
// accounting_result.html) rather than depending on globals that don't exist.
function _arEnsureFlashContainer() {
  let container = document.getElementById('arFlashContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'arFlashContainer';
    document.body.appendChild(container);
  }
  _arStartFlashStacking();
  return container;
}

// #arFlashContainer lives on <body> (so it survives partial navigation, unlike
// the app-wide #flashContainer inside #appShell) but sits at the same
// top-right spot — messages from both ended up drawn on top of each other.
// While ours has anything in it, keep it placed right BELOW the app-wide one,
// so all messages read as one column.
function _arStartFlashStacking() {
  if (window.__arFlashStackTimer) return;
  window.__arFlashStackTimer = setInterval(() => {
    const ours = document.getElementById('arFlashContainer');
    if (!ours || !ours.children.length) {
      if (ours) ours.style.top = '';
      clearInterval(window.__arFlashStackTimer);
      window.__arFlashStackTimer = null;
      return;
    }
    const app = document.getElementById('flashContainer');
    const hasApp = app && app.offsetHeight > 0 && app.children.length;
    ours.style.top = hasApp ? (app.getBoundingClientRect().bottom + 8) + 'px' : '';
  }, 300);
}

function showArFlash(message, type, ttl) {
  if (!message) return;
  const kind = (type === 'error' || type === 'danger') ? 'error' : (type === 'warning' ? 'warning' : 'success');
  const container = _arEnsureFlashContainer();
  const id = 'arBulkFlash';
  // Always remove and recreate rather than reusing an existing element.
  // base_01.js runs a GLOBAL MutationObserver on every .flash-banner
  // element that independently schedules its OWN removal after
  // data-ttl ms (default 6000, see AUTO_TTL there) the moment the element
  // is FIRST added to the DOM — it never re-evaluates on later attribute
  // changes. So reusing the same element across calls with different ttl
  // values would leave a stale removal timer from whichever call first
  // created it, regardless of what this function does. Recreating the
  // node on every call guarantees the observer always sees a fresh
  // insertion with the correct data-ttl already set.
  const existing = document.getElementById(id);
  if (existing) { clearTimeout(existing.__arFlashTimer); existing.remove(); }
  const el = document.createElement('div');
  el.id = id;
  if (ttl === 0) el.setAttribute('data-ttl', '0');
  const cls = kind === 'error' ? 'flash-error' : (kind === 'warning' ? 'flash-warning' : 'flash-success');
  el.className = 'flash-banner ' + cls;
  el.style.display = 'flex';
  el.style.alignItems = 'center';
  el.style.justifyContent = 'space-between';
  el.style.gap = '0.5rem';
  const textNode = document.createElement('span');
  textNode.textContent = message;
  textNode.style.cssText = 'flex:1 1 auto;font-size:13px;';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '×';
  closeBtn.style.cssText = 'background:transparent;border:0;cursor:pointer;font-size:18px;line-height:1;opacity:.75;';
  closeBtn.addEventListener('click', () => { clearTimeout(el.__arFlashTimer); el.remove(); });
  el.append(textNode, closeBtn);
  container.prepend(el);

  // Mirrored into the Μαζικός status line only while that tab is the one
  // showing — otherwise e.g. an Αποθηκευμένα ΦΠΑ search message sat there
  // and resurfaced under the Μαζικός table later.
  const statusEl = document.getElementById('arBulkStatus');
  const bulkTab = document.getElementById('arTabBulk');
  if (statusEl && bulkTab && !bulkTab.classList.contains('hidden')) statusEl.textContent = message;

  // ttl === 0 means "stays until explicitly replaced or closed" — used for
  // in-progress status messages whose real duration isn't known upfront
  // (a fixed timeout there just makes the flash vanish while the operation
  // is still running, looking like it silently died).
  if (ttl !== 0) {
    el.__arFlashTimer = setTimeout(() => { try { el.remove(); } catch (_) {} }, ttl || 6000);
  }
}

// The completion flash's "jump straight to the results" links need their own
// small custom banner in the same top-right slot as showArFlash.
function showArResultsFlash(message, kind, opts) {
  // A final flash means any in-progress status flash (showArOverlay's
  // ttl:0 "Έλεγχος αποθεμάτων λήξης"-style messages, left up deliberately
  // since their real duration isn't known upfront) is now stale — clear it
  // so it doesn't sit there stacked above the actual result.
  const staleProgress = document.getElementById('arBulkFlash');
  if (staleProgress) { clearTimeout(staleProgress.__arFlashTimer); staleProgress.remove(); }
  const container = _arEnsureFlashContainer();
  const id = (opts && opts.flashId) || 'arBulkResultsFlash';
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    el.setAttribute('data-flash', '');
    container.prepend(el);
  }
  const cls = kind === 'error' ? 'flash-error' : (kind === 'warning' ? 'flash-warning' : 'flash-success');
  el.className = 'flash-banner ' + cls;
  el.style.display = 'flex';
  el.style.flexDirection = 'column';
  el.style.gap = '0.35rem';
  el.style.pointerEvents = 'auto';
  el.innerHTML = '';

  const row1 = document.createElement('div');
  row1.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:0.5rem;';
  const textNode = document.createElement('span');
  textNode.textContent = message;
  textNode.style.cssText = 'flex:1 1 auto;font-size:13px;';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '×';
  closeBtn.style.cssText = 'background:transparent;border:0;cursor:pointer;font-size:18px;line-height:1;opacity:.75;';
  closeBtn.addEventListener('click', () => { clearTimeout(el.__arFlashTimer); el.remove(); });
  row1.append(textNode, closeBtn);
  el.appendChild(row1);

  if (opts && (opts.zip || opts.consolidated || opts.pdf || opts.view || opts.notes)) {
    const row2 = document.createElement('div');
    row2.style.cssText = 'display:flex;flex-wrap:wrap;gap:0.4rem;';
    const linkBtnStyle = 'font-size:11px;padding:3px 8px;border-radius:6px;border:none;background:rgba(255,255,255,.3);color:inherit;cursor:pointer;font-weight:700;';
    const addBtn = (label, handler) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.style.cssText = linkBtnStyle;
      b.addEventListener('click', handler);
      row2.appendChild(b);
    };
    if (opts.view) addBtn('👁 Προβολή', opts.view);
    if (opts.pdf) addBtn('⬇ PDF', opts.pdf);
    if (opts.zip) addBtn('⬇ ZIP (ανά εταιρία)', opts.zip);
    if (opts.consolidated) addBtn('⬇ Συγκεντρωτικό PDF', opts.consolidated);
    if (opts.notes) addBtn('📋 Διαφορές ανά είδος', opts.notes);
    el.appendChild(row2);
  }

  clearTimeout(el.__arFlashTimer);
  // sticky: stays until closed (a run with errors/warnings — the reasons
  // must not vanish before they've been read).
  if (!(opts && opts.sticky)) {
    el.__arFlashTimer = setTimeout(() => { try { el.remove(); } catch (_) {} }, 15000);
  }
}


// Prevent the selection from changing while a bulk run is in flight.
function setBulkTableLocked(locked) {
  arTableCheckboxes('.ar-bulk-table', '.ar-bulk-cb').forEach((cb) => { cb.disabled = locked; });
  ['arBulkSelectAllBtn', 'arBulkSelectNoneBtn', 'arBulkRunBtn'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = locked;
  });
}

// Renders the compact per-company summary table + wires window.__arBulkCompanies
// and the ZIP/Συγκεντρωτικό buttons — shared by a fresh runBulk() and by
// reopening a past run from the «φάκελος αποθηκευμένων μαζικών».
// Fixed global numbering (not re-numbered per batch) so the same note type
// always carries the same asterisk across different Μαζικός runs — simpler
// to keep straight than a batch-local renumbering, and a legend line is
// only ever printed for numbers that actually occur in THIS batch.
var AR_BULK_NOTE_TYPE_ORDER = ['payroll_shortfall', 'rent_shortfall', 'efka_self_employed_shortfall', 'uncharacterized_last_quarter'];
var AR_BULK_NOTE_TYPE_LEGEND = {
  payroll_shortfall: 'Βρέθηκαν λιγότερες μηνιαίες εγγραφές μισθοδοσίας από τους μήνες της περιόδου.',
  rent_shortfall: 'Βρέθηκαν λιγότερες μηνιαίες εγγραφές ενοικίου από τους μήνες της περιόδου.',
  efka_self_employed_shortfall: 'Βρέθηκαν λιγότερες μηνιαίες πληρωμές ΕΦΚΑ Μη-Μισθωτών από τους μήνες της περιόδου — πιθανή οφειλή, έλεγξε ΚΕΑΟ (ή αποθήκευσε εξαίρεση από τον Ατομικό υπολογισμό).',
  uncharacterized_last_quarter: 'Αχαρακτήριστα παραστατικά άνω του 25% του συνόλου στο τελευταίο τρίμηνο — παρέδωσε τα στον λογιστή για χαρακτηρισμό/καταχώρηση.',
};
var AR_BULK_NOTE_SUPERSCRIPTS = ['¹', '²', '³', '⁴', '⁵'];

function renderBulkCompaniesSummary(companies) {
  const container = document.getElementById('arBulkReportContainer');
  container.innerHTML = '';
  const usedNoteNumbers = new Set();
  const summaryTable = document.createElement('table');
  // Its OWN class, not "ar-saved-table" — that class is also the jQuery
  // selector initSavedDataTable() uses (`$('.ar-saved-table')`), and since
  // the Bulk/Saved tab panels are only hidden via CSS (both stay in the
  // DOM), reusing that class here made this 4-column summary table get
  // caught by the Saved tab's 6-column columnDefs too, producing DataTables'
  // "Incorrect column count" warning. Same border/header/hover look, kept
  // as a separate selector in accounting_result.html's <style>.
  summaryTable.className = 'ar-bulk-summary-table';
  summaryTable.innerHTML = '<thead><tr><th>Επωνυμία</th><th>ΑΦΜ</th><th>Φορολογητέα Κέρδη</th><th></th></tr></thead><tbody></tbody>';
  const tbody = summaryTable.querySelector('tbody');
  companies.forEach((c, idx) => {
    const tr = document.createElement('tr');
    const dlBtn = document.createElement('button');
    dlBtn.type = 'button';
    dlBtn.className = 'ar-bulk-dl-btn';
    dlBtn.textContent = '⬇ PDF';
    dlBtn.addEventListener('click', () => {
      const cc = window.__arBulkCompanies[idx];
      exportHtmlAsPdf(buildReportSectionHtml(cc.name, cc.vat, cc.from, cc.to, cc.report), 'Λογιστικό_Αποτέλεσμα_' + cc.name + '_' + periodSuffix(cc.from, cc.to), 'portrait', true);
    });
    const noteNumbers = (c.notes || [])
      .map((n) => AR_BULK_NOTE_TYPE_ORDER.indexOf(n.type) + 1)
      .filter((num) => num > 0);
    noteNumbers.forEach((num) => usedNoteNumbers.add(num));
    const superscripts = noteNumbers.map((num) => AR_BULK_NOTE_SUPERSCRIPTS[num - 1] || `[${num}]`).join('');

    const tdName = document.createElement('td'); tdName.textContent = c.name + (superscripts ? ' ' + superscripts : '');
    const tdVat = document.createElement('td'); tdVat.className = 'ar-mono'; tdVat.textContent = c.vat || '';
    const tdAmt = document.createElement('td'); tdAmt.className = 'ar-num'; tdAmt.textContent = fmtMoney(c.report.taxable_result);
    const tdBtn = document.createElement('td'); tdBtn.appendChild(dlBtn);
    tr.append(tdName, tdVat, tdAmt, tdBtn);
    tbody.appendChild(tr);
  });
  container.appendChild(summaryTable);

  if (usedNoteNumbers.size) {
    const legend = document.createElement('div');
    legend.style.cssText = 'font-size:12px;color:#333;margin-top:8px;line-height:1.6;';
    legend.innerHTML = Array.from(usedNoteNumbers).sort((a, b) => a - b).map((num) => {
      const type = AR_BULK_NOTE_TYPE_ORDER[num - 1];
      const sup = AR_BULK_NOTE_SUPERSCRIPTS[num - 1] || `[${num}]`;
      return `<div>${sup} ${escapeHtml(AR_BULK_NOTE_TYPE_LEGEND[type] || '')}</div>`;
    }).join('');
    container.appendChild(legend);
  }

  window.__arBulkCompanies = companies;
  document.getElementById('arBulkPdfBtn').disabled = companies.length === 0;
  document.getElementById('arBulkConsolidatedPdfBtn').disabled = companies.length === 0;
}

// ---------------- Past bulk runs («φάκελος αποθηκευμένων μαζικών») ----------------
// Bulk history entries can't be deleted one at a time (history_store.
// delete_entry refuses mode="bulk") — this is how the accountant finds a
// past run again, downloads it individually/consolidated, or deletes the
// whole run (optionally cascading into the per-company entries too).
// Everything about bulk history lives inside this one popup: a list view
// and a detail view for the currently-open batch, toggled in place rather
// than bouncing the user back out to the main Μαζικός tab.

var AR_OPEN_BATCH = null; // { batch, companies } for whichever run is open in the modal

function _arBatchTimestamp(iso) {
  if (!iso) return '';
  const ts = new Date(iso);
  return String(ts.getDate()).padStart(2, '0') + '/' + String(ts.getMonth() + 1).padStart(2, '0') + '/' + ts.getFullYear() +
    ' ' + String(ts.getHours()).padStart(2, '0') + ':' + String(ts.getMinutes()).padStart(2, '0');
}

function showBulkRunsListView() {
  document.getElementById('arBulkRunsDetailView').classList.add('hidden');
  document.getElementById('arBulkRunsListView').classList.remove('hidden');
  AR_OPEN_BATCH = null;
}

async function showBulkRunsModal() {
  moveModalsToBody(); // see showArOverlay for why this defensive call is needed
  const modal = document.getElementById('arBulkRunsModal');
  const listEl = document.getElementById('arBulkRunsList');
  if (!modal || !listEl) return;
  showBulkRunsListView();
  modal.classList.remove('hidden');
  modal.style.display = ''; // defensive: clear any stray inline style so the Tailwind class governs display again
  listEl.innerHTML = '<div class="text-sm text-gray-500 p-2">Φόρτωση...</div>';
  try {
    const res = await fetch('/api/accounting_result/bulk_runs');
    const data = await res.json();
    if (!data.ok) { listEl.innerHTML = '<div class="text-sm text-red-600 p-2">Σφάλμα: ' + escapeHtml(data.error || '') + '</div>'; return; }
    const batches = data.batches || [];
    if (!batches.length) { listEl.innerHTML = '<div class="text-sm text-gray-500 p-2">Δεν υπάρχουν ακόμα αποθηκευμένες μαζικές καταστάσεις.</div>'; return; }
    const rows = batches.map((b) => {
      const companies = b.companies || [];
      const okCount = companies.filter((c) => c.ok).length;
      return `<tr>
        <td>${escapeHtml(_arBatchTimestamp(b.timestamp))}</td>
        <td>${escapeHtml(ddmmyyyy(b.date_from))} – ${escapeHtml(ddmmyyyy(b.date_to))}${b.aborted ? ' <span class="text-amber-600">(διακόπηκε)</span>' : ''}</td>
        <td>${escapeHtml(b.computed_by || '')}</td>
        <td>${okCount}/${companies.length}</td>
        <td style="white-space:nowrap;">
          <button type="button" class="text-xs px-2 py-1 rounded border hover:bg-gray-50 ar-bulk-run-open-btn" data-id="${escapeHtml(b.id || '')}">📂 Άνοιγμα</button>
          <button type="button" class="text-xs px-2 py-1 rounded border border-red-300 text-red-600 hover:bg-red-50 ar-bulk-run-delete-btn" data-id="${escapeHtml(b.id || '')}">🗑</button>
        </td>
      </tr>`;
    }).join('');
    listEl.innerHTML = `<table class="ar-history-table"><thead><tr>
      <th>Ημερομηνία υπολογισμού</th><th>Περίοδος</th><th>Υπολογίστηκε από</th><th>Εταιρίες</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`;
    listEl.querySelectorAll('.ar-bulk-run-open-btn').forEach((btn) => {
      btn.addEventListener('click', () => openBulkRun(btn.dataset.id));
    });
    listEl.querySelectorAll('.ar-bulk-run-delete-btn').forEach((btn) => {
      btn.addEventListener('click', () => deleteBulkRun(btn.dataset.id, showBulkRunsModal));
    });
  } catch (e) {
    listEl.innerHTML = '<div class="text-sm text-red-600 p-2">Σφάλμα: ' + escapeHtml(String(e)) + '</div>';
  }
}

async function openBulkRun(batchId) {
  if (!batchId) return;
  const detailStatus = document.getElementById('arBulkRunsDetailStatus');
  document.getElementById('arBulkRunsListView').classList.add('hidden');
  document.getElementById('arBulkRunsDetailView').classList.remove('hidden');
  detailStatus.textContent = 'Φόρτωση...';
  document.getElementById('arBulkRunsDetailContainer').innerHTML = '';
  try {
    const res = await fetch('/api/accounting_result/bulk_runs/' + encodeURIComponent(batchId));
    const data = await res.json();
    if (!data.ok) { detailStatus.textContent = 'Σφάλμα: ' + (data.error || ''); return; }
    AR_OPEN_BATCH = { batch: data.batch, companies: data.companies || [] };
    detailStatus.textContent = '📂 Μαζική κατάσταση από ' + ddmmyyyy(data.batch.date_from) + ' – ' + ddmmyyyy(data.batch.date_to) +
      ' (υπολογίστηκε ' + _arBatchTimestamp(data.batch.timestamp) + ').';
    const container = document.getElementById('arBulkRunsDetailContainer');
    const table = document.createElement('table');
    table.className = 'ar-bulk-summary-table';
    table.innerHTML = '<thead><tr><th>Επωνυμία</th><th>ΑΦΜ</th><th>Φορολογητέα Κέρδη</th><th></th></tr></thead><tbody></tbody>';
    const tbody = table.querySelector('tbody');
    AR_OPEN_BATCH.companies.forEach((c) => {
      const tr = document.createElement('tr');
      const dlBtn = document.createElement('button');
      dlBtn.type = 'button';
      dlBtn.className = 'ar-bulk-dl-btn';
      dlBtn.textContent = '⬇ PDF';
      dlBtn.addEventListener('click', () => {
        exportHtmlAsPdf(buildReportSectionHtml(c.name, c.vat, c.from, c.to, c.report), 'Λογιστικό_Αποτέλεσμα_' + c.name + '_' + periodSuffix(c.from, c.to), 'portrait', true);
      });
      const tdName = document.createElement('td'); tdName.textContent = c.name;
      const tdVat = document.createElement('td'); tdVat.className = 'ar-mono'; tdVat.textContent = c.vat || '';
      const tdAmt = document.createElement('td'); tdAmt.className = 'ar-num'; tdAmt.textContent = fmtMoney(c.report.taxable_result);
      const tdBtn = document.createElement('td'); tdBtn.appendChild(dlBtn);
      tr.append(tdName, tdVat, tdAmt, tdBtn);
      tbody.appendChild(tr);
    });
    (data.failed || []).forEach((f) => {
      const tr = document.createElement('tr');
      tr.style.background = '#fef2f2';
      const tdName = document.createElement('td'); tdName.textContent = f.name || '';
      const tdVat = document.createElement('td'); tdVat.className = 'ar-mono'; tdVat.textContent = f.vat || '';
      const tdErr = document.createElement('td'); tdErr.colSpan = 2; tdErr.style.color = '#b91c1c'; tdErr.style.fontSize = '0.8rem';
      tdErr.textContent = '❌ Δεν υπολογίστηκε: ' + (f.error || '');
      tr.append(tdName, tdVat, tdErr);
      tbody.appendChild(tr);
    });
    container.appendChild(table);
  } catch (e) {
    detailStatus.textContent = 'Σφάλμα: ' + String(e);
  }
}

// Same ⚠️-styled centered dialog e3_check.html uses for its own "are you
// sure" prompts (showModalConfirm, static/modal_utils.js) — not the flash
// banner used for the per-company history delete, since the accountant
// specifically asked for this one to look like that dialog instead.
async function deleteBulkRun(batchId, onDone) {
  if (!batchId) return;
  let proceed = false;
  try {
    proceed = await showModalConfirm(
      'Διαγραφή μαζικής κατάστασης',
      'Διαγραφή αυτής της μαζικής κατάστασης;',
      'Διαγραφή', 'Άκυρο',
    );
  } catch (_) { proceed = false; }
  if (!proceed) return;

  // Second question: keep or also cascade-delete the per-company results
  // this batch pointed to. Either answer proceeds with the batch deletion —
  // "Ναι" additionally removes the per-company entries too.
  let deleteIndividual = false;
  try {
    deleteIndividual = await showModalConfirm(
      'Αποτελέσματα ανά εταιρία',
      'Διαγραφή και των αποτελεσμάτων ανά εταιρία (δεν θα εμφανίζονται πια ούτε στο ιστορικό της κάθε εταιρίας);',
      'Ναι, διαγραφή όλων', 'Όχι, μόνο η κατάσταση',
    );
  } catch (_) { deleteIndividual = false; }

  try {
    const res = await fetch('/api/accounting_result/bulk_runs/' + encodeURIComponent(batchId), {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delete_individual: deleteIndividual }),
    });
    const data = await res.json();
    if (!data.ok) { showArFlash(data.error || 'Αποτυχία διαγραφής.', 'error'); return; }
    showArFlash('Η μαζική κατάσταση διαγράφηκε.', 'success', 3500);
    if (typeof onDone === 'function') onDone();
    // deleteIndividual also removed each company's own history entry
    // server-side — refresh the Ατομικός tab's history list for whichever
    // client is currently selected there too, so it doesn't keep showing a
    // now-deleted result until the page is reloaded.
    if (deleteIndividual) {
      const singleSel = document.getElementById('arSingleCredential');
      if (singleSel && singleSel.value) loadSingleHistory(singleSel.value);
    }
  } catch (e) {
    showArFlash(String(e), 'error');
  }
}

async function runBulk() {
  const from = document.getElementById('arBulkFrom').value;
  const to = document.getElementById('arBulkTo').value;
  const names = arTableCheckedValues('.ar-bulk-table', '.ar-bulk-cb');
  const statusEl = document.getElementById('arBulkStatus');
  document.getElementById('arBulkPdfBtn').disabled = true;
  document.getElementById('arBulkConsolidatedPdfBtn').disabled = true;

  if (!from || !to || !names.length) {
    statusEl.textContent = 'Επιλέξτε περίοδο και τουλάχιστον μία εταιρία.';
    return;
  }

  setBulkTableLocked(true);
  AR_BULK_RUNNING = true;
  // One job id for the whole run: the cross-page progress flash (with
  // «Διακοπή») stays up on every page from the first ΑΑΔΕ check to the end.
  const jobId = 'ar-bulk-' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  startBulkCrossPageBanner(jobId, names.length, `ξεκίνησε για ${names.length} εταιρίες…`);
  try {
  const year = yearFromDMY(to);
  // Βήμα 1: ΑΑΔΕ (type/contact/ΦΠΑ/partners) BEFORE any check, so the ΕΦΚΑ
  // check below knows each company's type and partner count.
  const pre = await arBulkAadePrecheck(names, jobId);
  const aadeWarnings = pre.warnings;
  if (pre.aborted) {
    statusEl.textContent = 'Διακόπηκε από τον χρήστη.';
    showArFlash('Λογιστικό Αποτέλεσμα (Μαζικός): διακόπηκε από τον χρήστη.', 'warning', 8000);
    return;
  }
  const step2 = `Βήμα 2/3 — προέλεγχος myDATA (απόθεμα, μισθοδοσία, ενοίκιο, ΕΦΚΑ) για ${names.length} εταιρίες`;
  setBulkCrossPageLabel(step2);
  showArOverlay('Λήψη δεδομένων από myDATA...', step2 + '.');
  const statusResp = await postJson('/api/accounting_result/inventory/bulk_status', { credential_names: names, year, date_from: from, date_to: to });
  hideArOverlay();
  if (!statusResp.ok) {
    statusEl.textContent = 'Σφάλμα: ' + (statusResp.error || '');
    showArFlash('Λογιστικό Αποτέλεσμα (Μαζικός): σφάλμα προελέγχου αποθεμάτων/μισθοδοσίας/ενοικίου/ΕΦΚΑ — ' + (statusResp.error || ''), 'error');
    return;
  }

  const depreciationAmbiguousNames = (statusResp.rows || [])
    .filter((r) => r.depreciation_ambiguous)
    .map((r) => r.name);

  // Every finding in ONE popup, grouped by kind (απόθεμα / μισθοδοσία /
  // ενοίκιο / ΕΦΚΑ Μη-Μισθωτών) with a row per company, then any manual
  // entries one after the other.
  const groups = arBuildCheckGroups(statusResp.rows);
  if (groups.length) {
    setBulkCrossPageLabel('αναμονή για τις επιλογές σου στο popup «Διαφορές προελέγχου»');
    const choices = await showGroupedChecksModal(groups);
    if (!choices) {
      statusEl.textContent = 'Ακυρώθηκε.';
      return;
    }
    const applied = await applyGroupedChecks(choices, statusResp.rows, year, from, to, statusEl);
    if (!applied) {
      statusEl.textContent = 'Ακυρώθηκε.';
      return;
    }
  }

  window.__arBulkPeriod = { from, to };
  statusEl.textContent = '';
  if (await isBulkAbortRequested(jobId)) {
    statusEl.textContent = 'Διακόπηκε από τον χρήστη.';
    return;
  }
  setBulkCrossPageLabel(`Βήμα 3/3 — υπολογισμός ${names.length} εταιριών…`);
  const bulkResp = await postJson('/api/accounting_result/bulk_compute', {
    credential_names: names, date_from: from, date_to: to, job_id: jobId,
  });
  stopBulkCrossPageBanner();
  if (!bulkResp.ok) {
    statusEl.textContent = 'Σφάλμα: ' + (bulkResp.error || '');
    showArFlash('Λογιστικό Αποτέλεσμα (Μαζικός): σφάλμα — ' + (bulkResp.error || ''), 'error');
    return;
  }

  // Bulk mode deliberately does NOT render the full per-company report
  // tables inline (that stays exclusive to Ατομικός) — just a compact
  // one-line-per-company summary with a download button; the actual
  // results are retrieved via the ZIP/Συγκεντρωτικό PDF buttons or the
  // Ατομικός tab's own history.
  const companies = [];
  const errors = [];
  (bulkResp.results || []).forEach((res) => {
    if (!res.ok || res.needs_inventory_input || res.needs_payroll_input || res.needs_rent_input) {
      const reason = res.error
        || (res.needs_payroll_input ? 'εκκρεμεί μισθοδοσία' : (res.needs_rent_input ? 'εκκρεμεί ενοίκιο' : 'εκκρεμεί απόθεμα λήξης'));
      errors.push(`${res.credential_name}: ${reason}`);
      return;
    }
    companies.push({ name: res.credential_name, vat: res.vat, from, to, report: res.report, notes: res.notes });
  });
  renderBulkCompaniesSummary(companies);
  let statusMsg = bulkResp.aborted
    ? `Διακόπηκε από τον χρήστη μετά από ${(bulkResp.results || []).length} εταιρίες.`
    : (errors.length
      ? ('Ολοκληρώθηκε με σφάλματα: ' + errors.join(' · '))
      : `Ολοκληρώθηκε (${companies.length} εταιρίες).`);
  if (depreciationAmbiguousNames.length) {
    statusMsg += ` Σημείωση: βρέθηκαν πολλαπλές εγγραφές αποσβέσεων και αθροίστηκαν αυτόματα (χωρίς επιλογή) για: ${depreciationAmbiguousNames.join(', ')}.`;
  }
  // Each company's first-ever computation runs its own ΦΠΑ auto-check
  // before anything else (see vatAutoCheckFlashMessage) — aggregated
  // here rather than one flash per company in a batch of many.
  const vatChecks = (bulkResp.results || []).map((r) => r.vat_auto_check).filter(Boolean);
  if (vatChecks.length) {
    const vatOk = vatChecks.filter((c) => c.ok).length;
    const vatFail = vatChecks.length - vatOk;
    statusMsg += ` Αυτόματος έλεγχος ΦΠΑ (πρώτη φορά): ${vatOk} επιτυχείς${vatFail ? `, ${vatFail} απέτυχαν` : ''}.`;
  }
  // Same 150.000€ turnover trigger as the Ατομικός flow (see
  // inventoryObligationFlashMessage) — aggregated per-company here rather
  // than one flash per company in a batch of many.
  let hasWarningAdvisory = false;
  const newlyObligated = (bulkResp.results || [])
    .filter((r) => r.inventory_obligation && r.inventory_obligation.reason === 'turnover_threshold')
    .map((r) => r.credential_name);
  if (newlyObligated.length) {
    hasWarningAdvisory = true;
    statusMsg += ` Νέα υποχρέωση απογραφής λήξης (υπέρβαση 150.000€ φέτος): ${newlyObligated.join(', ')}.`;
  }
  const booksCategoryMismatches = (bulkResp.results || [])
    .map((r) => r.books_category_mismatch)
    .filter(Boolean);
  if (booksCategoryMismatches.length) {
    hasWarningAdvisory = true;
    const names = booksCategoryMismatches.map((m) => `${m.credential_name} (ΑΑΔΕ: ${m.aade_category}, εμείς: ${m.our_category})`);
    statusMsg += ` Ασυμφωνία κατηγορίας βιβλίων με το Μητρώο ΑΑΔΕ — διόρθωσε από τη σελίδα Credentials: ${names.join(', ')}.`;
  }
  // Same μισθοδοσία/ΕΦΚΑ Μη-Μισθωτών/αχαρακτήριστα notes as the Ατομικός
  // flow (see renderReportNotesHtml) — popped here too, per the user's own
  // request that these behave like the other checks in both Ατομικός and
  // Μαζικός, aggregated by company rather than one flash per company.
  const payrollShortfallNames = (bulkResp.results || [])
    .filter((r) => (r.notes || []).some((n) => n.type === 'payroll_shortfall'))
    .map((r) => r.credential_name);
  if (payrollShortfallNames.length) {
    hasWarningAdvisory = true;
    statusMsg += ` Μισθοδοσία με λιγότερες μηνιαίες εγγραφές από τους μήνες της περιόδου: ${payrollShortfallNames.join(', ')}.`;
  }
  const rentShortfallNames = (bulkResp.results || [])
    .filter((r) => (r.notes || []).some((n) => n.type === 'rent_shortfall'))
    .map((r) => r.credential_name);
  if (rentShortfallNames.length) {
    hasWarningAdvisory = true;
    statusMsg += ` Ενοίκιο με λιγότερες μηνιαίες εγγραφές από τους μήνες της περιόδου: ${rentShortfallNames.join(', ')}.`;
  }
  const efkaShortfallNames = (bulkResp.results || [])
    .filter((r) => (r.notes || []).some((n) => n.type === 'efka_self_employed_shortfall'))
    .map((r) => r.credential_name);
  if (efkaShortfallNames.length) {
    hasWarningAdvisory = true;
    statusMsg += ` ΕΦΚΑ Μη-Μισθωτών με πιθανή οφειλή (έλεγξε ΚΕΑΟ): ${efkaShortfallNames.join(', ')}.`;
  }
  const uncharacterizedNames = (bulkResp.results || [])
    .filter((r) => (r.notes || []).some((n) => n.type === 'uncharacterized_last_quarter'))
    .map((r) => r.credential_name);
  if (uncharacterizedNames.length) {
    hasWarningAdvisory = true;
    statusMsg += ` Αχαρακτήριστα παραστατικά >25% στο τελευταίο τρίμηνο: ${uncharacterizedNames.join(', ')}.`;
  }
  if (aadeWarnings.length) {
    hasWarningAdvisory = true;
    statusMsg += ` Έλεγχος ΑΑΔΕ (τύπος/επικοινωνία/ΦΠΑ) χωρίς επιτυχία: ${aadeWarnings.join(' · ')}.`;
  }
  statusEl.textContent = statusMsg;
  const hasNotes = errors.length > 0 || (bulkResp.results || []).some((r) => (r.notes || []).length);
  const needsAttention = !!(bulkResp.aborted || errors.length || hasWarningAdvisory);
  showArResultsFlash(
    'Λογιστικό Αποτέλεσμα (Μαζικός): ' + statusMsg,
    needsAttention ? 'warning' : 'success',
    {
      zip: companies.length ? () => document.getElementById('arBulkPdfBtn').click() : null,
      consolidated: companies.length ? () => document.getElementById('arBulkConsolidatedPdfBtn').click() : null,
      notes: hasNotes ? () => showBulkNotesByTypeModal(bulkResp.results) : null,
      sticky: needsAttention,
    },
  );
  if (hasNotes) showBulkNotesByTypeModal(bulkResp.results);
  } catch (err) {
    // Without this, any unexpected exception anywhere above (e.g. a
    // network hiccup mid-flow) unwinds as a silent unhandled promise
    // rejection — the overlay/flash from the in-progress step stays
    // stuck or vanishes with no popup and no error shown, which looks
    // exactly like "the check got lost" with no explanation.
    hideArOverlay();
    statusEl.textContent = 'Σφάλμα: ' + (err && err.message ? err.message : String(err));
    showArFlash('Λογιστικό Αποτέλεσμα (Μαζικός): μη αναμενόμενο σφάλμα — ' + (err && err.message ? err.message : String(err)), 'error');
  } finally {
    stopBulkCrossPageBanner();
    AR_BULK_RUNNING = false;
    setBulkTableLocked(false);
  }
}

// ---------------- Saved clients ----------------
// Shows the SAME group-wide store as the Έλεγχος Ε3 page's own «Αποθηκευμένα»
// tab (/api/e3/brain/credentials_store), so a client added on either page
// shows up on both — rather than keeping a second, page-local list. Adding
// clients reuses that same page's Excel-import endpoint/format. Because that
// store is a different, richer record (address/ΑΜΚΑ/TAXISnet/company+members)
// than this page's own credentials.json (which is what /compute actually
// needs), a row only gets a «Υπολογισμός» shortcut when its ΑΦΜ also matches
// one of this page's regular credentials — otherwise full myDATA API creds
// for it still need to be added via the Credentials page first.

function afmToCredentialName(afm) {
  const match = (window.AR_CREDENTIALS || []).find((c) => c.vat === afm);
  return match ? match.name : null;
}

function renderSavedTable(companies) {
  if (!companies || !companies.length) return '<div class="p-4 text-sm text-gray-500">Δεν υπάρχουν αποθηκευμένα credentials. Χρησιμοποιήστε «Εισαγωγή από Excel» για να προσθέσετε.</div>';
  const trs = companies.map((entry) => {
    const c = entry.company || {};
    const afm = String(c.afm || '').replace(/['"<>&]/g, '');
    // Same three-state resolution as the edit modal's pill: no members AND
    // no stored legal_type doesn't mean individual, it usually means «🔍
    // Λήψη» has just never run for this company — show unresolved instead
    // of guessing.
    const storedTypeTbl = c.legal_type || '';
    const hasMembersTbl = Array.isArray(entry.members) && entry.members.length > 0;
    const typeResolutionTbl = storedTypeTbl
      ? (storedTypeTbl.toLowerCase().indexOf('ατομικ') >= 0 ? 'individual' : 'company')
      : (hasMembersTbl ? 'company' : 'unknown');
    const typeBadge = typeResolutionTbl === 'individual'
      ? '<span style="background:#dbeafe;color:#1d4ed8;border:1px solid #93c5fd;border-radius:9999px;padding:0.1rem 0.5rem;font-size:0.7rem;font-weight:700;">Ατομική</span>'
      : typeResolutionTbl === 'company'
      ? '<span style="background:#f0fdf4;color:#166534;border:1px solid #86efac;border-radius:9999px;padding:0.1rem 0.5rem;font-size:0.7rem;font-weight:700;">Εταιρία</span>'
      : '<span style="background:#f1f5f9;color:#64748b;border:1px solid #cbd5e1;border-radius:9999px;padding:0.1rem 0.5rem;font-size:0.7rem;font-weight:700;">—</span>';
    const gemiWarnBadge = c.members_gemi_warning
      ? ` <span title="${escapeHtml(c.members_gemi_warning)}" style="cursor:help;">⚠</span>`
      : '';
    const credName = afmToCredentialName(afm);
    const actionCell = credName
      ? `<button type="button" class="text-xs px-2 py-1 rounded border hover:bg-gray-50 ar-saved-compute-btn" data-name="${escapeHtml(credName)}">Υπολογισμός</button>`
      : `<button type="button" class="ar-saved-nomydata-btn" style="background:#fef3c7;color:#b45309;border:1px solid #fcd34d;border-radius:9999px;width:1.35rem;height:1.35rem;font-size:0.75rem;font-weight:800;cursor:pointer;line-height:1;" title="Δεν υπάρχουν κωδικοί myDATA για αυτή την εταιρία — δεν μπορεί να υπολογιστεί λογιστικό αποτέλεσμα. Πάτησε για να φιλτράρεις τον πίνακα μόνο σε τέτοιες εταιρίες (ξανά για καθαρισμό).">i</button>`;
    // One ΑΑΔΕ lookup for everything the registry page gives us — type,
    // address, email/κινητό/σταθερό AND ΦΠΑ/κατηγορία βιβλίων (same login,
    // see api_accounting_result_company_info). 🔍 while the type is unknown,
    // 🔄 (re-check) once it's on file.
    const typeDetectBtn = `<button type="button" class="ar-saved-aade-btn text-xs px-1 py-0.5 rounded border hover:bg-gray-50 ml-1" data-afm="${afm}" title="Λήψη από ΑΑΔΕ (ένα login): τύπος εταιρίας, διεύθυνση, email/κινητό/σταθερό και ΦΠΑ/κατηγορία βιβλίων — χρειάζεται αποθηκευμένους κωδικούς TAXISnet (✏️)">${typeResolutionTbl === 'unknown' ? '🔍' : '🔄'}</button>`;
    return `<tr${credName ? '' : ' data-no-mydata="1"'}>
      <td><input type="checkbox" class="ar-saved-cb" value="${afm}"></td>
      <td class="ar-mono">${afm}</td>
      <td>${escapeHtml(c.name || '')}</td>
      <td>${typeBadge}${gemiWarnBadge}${typeDetectBtn}</td>
      <td class="ar-saved-vat text-xs" data-afm="${afm}">
        <span class="ar-saved-vat-label text-gray-400">…</span>
      </td>
      <td class="ar-saved-hist text-xs text-gray-500" data-afm="${afm}">…</td>
      <td style="white-space:nowrap;">
        ${actionCell}
        <button type="button" class="text-xs px-2 py-1 rounded border hover:bg-gray-50 ar-saved-edit-btn" data-afm="${afm}" title="Επεξεργασία στοιχείων">✏️</button>
      </td>
    </tr>`;
  }).join('');
  return `<table class="ar-saved-table"><thead><tr>
    <th><input type="checkbox" disabled></th><th>ΑΦΜ</th><th>Επωνυμία</th><th>Τύπος</th><th>ΦΠΑ</th><th>Τελευταίος υπολογισμός</th><th>Ενέργειες</th>
  </tr></thead><tbody>${trs}</tbody></table>`;
}

function vatProfileLabel(profile) {
  if (!profile || profile.vat_subject === undefined || profile.vat_subject === null) return '— άγνωστο';
  if (profile.vat_subject === false) return '❌ Όχι ΦΠΑ';
  const period = profile.vat_period_type === 'monthly' ? 'μηνιαίο' : (profile.vat_period_type === 'quarterly' ? '3μηνο' : '');
  return '✓ ΦΠΑ' + (period ? ' (' + period + ')' : '');
}

// A compute response's `vat_auto_check` is non-null exactly once — the
// first time a company is ever computed, /compute and /bulk_compute run
// the ΦΠΑ ΑΑΔΕ-Μητρώο lookup themselves before anything else (see
// _ar_ensure_vat_profile_checked in app.py) instead of silently falling
// back to the ΦΠΑ-applicable/monthly defaults for a company nobody ever
// checked. Every later response for that company gets `null` here (a
// profile already exists) — this only ever fires once per company.
function vatAutoCheckFlashMessage(check, label) {
  if (!check) return null;
  if (check.ok) {
    return `ΦΠΑ (${label}): πρώτος αυτόματος έλεγχος — ${vatProfileLabel({ vat_subject: check.vat_subject })}.`;
  }
  return `ΦΠΑ (${label}): ο αυτόματος έλεγχος απέτυχε — ${check.error || 'σφάλμα'} (κάνε τον χειροκίνητα από τα Αποθηκευμένα).`;
}

// `inventory_obligation.reason === 'turnover_threshold'` means THIS
// computation is the one that discovered the obligation: a Β/Γ-κατηγορίας
// company whose Πωλήσεις Εμπορευμάτων+Προϊόντων crossed 150.000€ within the
// period, even though it never declared a closing stock before (see
// determine_inventory_obligation in accounting_result/engine.py) — worth a
// flash precisely because last year's report may not have required one.
function inventoryObligationFlashMessage(obligation, label) {
  if (!obligation || obligation.reason !== 'turnover_threshold') return null;
  return `Απογραφή λήξης (${label}): οι πωλήσεις εμπορευμάτων/προϊόντων έφτασαν ${fmtMoney(obligation.goods_products_revenue)}€ εντός της χρήσης, πάνω από το όριο των ${fmtMoney(obligation.threshold)}€ — η εταιρεία είναι πλέον υποχρεωμένη σε απογραφή λήξης, ακόμη κι αν πέρυσι δεν ήταν.`;
}

// `books_category_mismatch` is non-null when the ΑΑΔΕ Μητρώο's own
// κατηγορία βιβλίων disagrees with what's configured on this company's
// credentials.json entry — see _ar_check_books_category_mismatch in app.py.
function booksCategoryMismatchFlashMessage(mismatch, label) {
  if (!mismatch) return null;
  return `Κατηγορία βιβλίων (${label}): το Μητρώο ΑΑΔΕ δείχνει "${mismatch.aade_category_raw}" (${mismatch.aade_category}) ενώ στα Credentials έχουμε καταχωρημένη ${mismatch.our_category} — έλεγξε/διόρθωσε τη ρύθμιση από τη σελίδα Credentials.`;
}

// Both async-filled columns below write into cells via raw textContent
// AFTER initSavedDataTable() has already captured the initial "…"
// placeholder as that cell's sort/search data — DataTables never notices
// a plain DOM mutation on its own, so sorting/searching kept acting on the
// stale placeholder forever. redrawSavedDataTable() re-reads every cell's
// live DOM content (which is exactly what DataTables' own `data-order`
// attribute convention is for — see fillSavedHistoryCells) into its cache
// once the fill is done.
// «Χωρίς κωδικούς myDATA» filter, toggled by the yellow ⓘ on such rows. A
// DataTables custom search (registered once) so it composes with the normal
// search box, works across pages and survives the table being rebuilt.
window.__arNoMydataFilter = false;
function arInstallNoMydataFilter() {
  const $ = window.jQuery;
  if (!$ || !$.fn || !$.fn.dataTable || window.__arNoMydataFilterInstalled) return;
  window.__arNoMydataFilterInstalled = true;
  $.fn.dataTable.ext.search.push((settings, data, dataIndex) => {
    if (!window.__arNoMydataFilter) return true;
    if (!settings.nTable || !settings.nTable.classList.contains('ar-saved-table')) return true;
    const tr = settings.aoData[dataIndex] && settings.aoData[dataIndex].nTr;
    return !!(tr && tr.getAttribute('data-no-mydata') === '1');
  });
}
function arToggleNoMydataFilter() {
  window.__arNoMydataFilter = !window.__arNoMydataFilter;
  const $ = window.jQuery;
  if ($ && $.fn.DataTable.isDataTable('.ar-saved-table')) $('.ar-saved-table').DataTable().draw();
  const n = document.querySelectorAll('.ar-saved-table [data-no-mydata]').length;
  const t = window.jQuery && window.jQuery.fn.DataTable.isDataTable('.ar-saved-table')
    ? window.jQuery('.ar-saved-table').DataTable().rows().nodes().to$().filter('[data-no-mydata]').length : n;
  showArFlash(window.__arNoMydataFilter
    ? `Φίλτρο: εμφανίζονται μόνο οι ${t} εταιρίες χωρίς κωδικούς myDATA (δεν μπορεί να υπολογιστεί λογιστικό αποτέλεσμα). Πάτησε ξανά το κίτρινο i για καθαρισμό.`
    : 'Φίλτρο myDATA: καθαρίστηκε.', 'info', 6000);
}

function redrawSavedDataTable() {
  const $ = window.jQuery;
  if (!$ || !$.fn || !$.fn.DataTable || !$.fn.DataTable.isDataTable('.ar-saved-table')) return;
  $('.ar-saved-table').DataTable().rows().invalidate('dom').draw(false);
}

// Same ΓΕΜΗ+ΑΑΔΕ cross-check as e3_check.html's single-company "🔍
// Ανάκτηση διεύθυνσης" flow (/api/e3/brain/company_members), triggered
// per-row from the Τύπος column's 🔍 button instead — this store entry
// already carries its own TAXISnet creds (filled via ✏️ or Excel import),
// so there's no separate credentials form to fill in here first. Runs a
// real Playwright/ΓΕΜΗ round-trip (~30-60s), so it's an explicit per-row
// action, never automatic — see runBulk()'s pre-check gate below for why
// a missing type blocks a compute instead of silently auto-fetching it.
async function fetchCompanyInfoForSavedRow(afm, btn, opts) {
  const prevText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⌛';
  showArFlash(`Ανάκτηση στοιχείων από ΑΑΔΕ για ΑΦΜ ${afm}… (νομική μορφή, διεύθυνση, email/κινητό, ΦΠΑ, μέλη)`, 'info', 5000);
  const r = await arFetchCompanyInfoCore(afm, opts);
  if (r.ok) {
    const warn = r.membersError || r.noContact || /⚠/.test(r.summary || '');
    showArFlash(`ΑΦΜ ${afm}: ${r.summary}`, warn ? 'warning' : 'success', warn ? 12000 : 5000);
    loadSavedClients();
  } else {
    showArFlash(`Σφάλμα ανάκτησης ΑΑΔΕ (ΑΦΜ ${afm}): ${r.error}`, 'error', 7000);
    btn.disabled = false;
    btn.textContent = prevText;
  }
}

async function fillSavedVatCells(container) {
  const cells = Array.from(container.querySelectorAll('.ar-saved-vat'));
  await Promise.all(cells.map(async (cell) => {
    const afm = cell.dataset.afm;
    const label = cell.querySelector('.ar-saved-vat-label');
    if (!afm || !label) return;
    try {
      const res = await fetch('/api/accounting_result/vat_profile?vat=' + encodeURIComponent(afm));
      const data = await res.json();
      label.textContent = data.ok ? vatProfileLabel(data.profile) : '—';
    } catch (e) {
      label.textContent = '—';
    }
  }));
  redrawSavedDataTable();
}

async function fillSavedHistoryCells(container) {
  const cells = Array.from(container.querySelectorAll('.ar-saved-hist'));
  await Promise.all(cells.map(async (cell) => {
    const afm = cell.dataset.afm;
    // "—" (never computed) sorts first with an empty data-order — keep
    // every branch of this cell consistently keyed on data-order rather
    // than mixing "sort by ISO timestamp" here with "sort by literal
    // dash text" there.
    if (!afm) { cell.textContent = '—'; cell.setAttribute('data-order', ''); return; }
    try {
      const res = await fetch('/api/accounting_result/history?vat=' + encodeURIComponent(afm));
      const data = await res.json();
      const latest = data.ok && data.history && data.history[0];
      if (!latest) { cell.textContent = '—'; cell.setAttribute('data-order', ''); return; }
      const ts = new Date(latest.timestamp);
      const tsStr = String(ts.getDate()).padStart(2, '0') + '/' + String(ts.getMonth() + 1).padStart(2, '0') + '/' + ts.getFullYear();
      cell.textContent = tsStr + (latest.computed_by ? ' (' + latest.computed_by + ')' : '');
      // dd/mm/yyyy display text does NOT sort chronologically as a plain
      // string (e.g. "01/02/2027" < "15/01/2026" alphabetically) — this
      // `data-order` attribute is DataTables' own built-in convention for
      // "sort by THIS instead of the cell's text"; the ISO timestamp sorts
      // correctly as a plain string too, no custom sort-type needed.
      cell.setAttribute('data-order', latest.timestamp || '');
    } catch (e) {
      cell.textContent = '—';
      cell.setAttribute('data-order', '');
    }
  }));
  redrawSavedDataTable();
}

async function bulkDeleteSavedClients() {
  const afms = arTableCheckedValues('.ar-saved-table', '.ar-saved-cb');
  if (!afms.length) {
    showArFlash('Δεν έχεις επιλέξει καμία εταιρία.', 'warning', 4000);
    return;
  }
  let ok = false;
  try {
    ok = await showModalConfirm('Διαγραφή επιλεγμένων', `Διαγραφή credentials για ${afms.length} εταιρίες;`, 'Διαγραφή', 'Άκυρο');
  } catch (_) { ok = false; }
  if (!ok) return;
  const resp = await postJson('/api/e3/brain/credentials_store/bulk_delete', { afms });
  if (!resp.ok) {
    showArFlash('Σφάλμα διαγραφής: ' + (resp.error || ''), 'error');
    return;
  }
  const protectedList = resp.protected || [];
  // window.AR_CREDENTIALS is a page-render-time snapshot that already
  // includes every store company having myDATA creds (see
  // _load_credentials_with_excel_store_merge). loadSavedClients() below
  // re-creates a minimal store record for any AR_CREDENTIALS company missing
  // from the store, so without dropping the just-deleted ones from that
  // snapshot a delete was silently undone (and the record's data wiped) on
  // the very next refresh.
  const protectedAfms = new Set(protectedList.map((p) => String(p.afm)));
  const deletedAfms = new Set(afms.filter((a) => !protectedAfms.has(String(a))));
  window.AR_CREDENTIALS = (window.AR_CREDENTIALS || []).filter((c) => !deletedAfms.has(String(c.vat)));
  let msg = `Διαγράφηκαν ${resp.deleted || 0} εταιρίες.`;
  if (protectedList.length) {
    const names = protectedList.map((p) => p.name || p.afm).join(', ');
    msg += ` Δεν διαγράφηκαν ${protectedList.length} (βρίσκονται στα credentials μας): ${names}.`;
  }
  showArFlash(msg, protectedList.length ? 'warning' : 'success', protectedList.length ? 9000 : 5000);
  loadSavedClients();
}

// Same sticky flash+progress+«Διακοπή» pattern as the Μαζικός υπολογισμός
// job above (startBulkCrossPageBanner/stopBulkCrossPageBanner + the
// cross-page banner in base_01.js) — reused as-is rather than duplicated,
// since job_registry is generic (keyed only by job_id, no notion of "which
// kind of bulk job") and /api/accounting_result/bulk_progress|bulk_abort
// already just forward to it.
// Core of the per-row Τύπος 🔍 (no UI side effects) so the bulk menu below
// can reuse it. Resolves {ok, error}.
async function arFetchCompanyInfoCore(afm, opts) {
  // Server does the ΑΑΔΕ-only retrieval AND the store write (see
  // api_accounting_result_company_info in app.py) — TAXISnet creds never
  // travel to the browser and back.
  const resp = await postJson('/api/accounting_result/company_info', { afm, apply_vat: !!(opts && opts.applyVat) });
  if (!resp.ok) return { ok: false, error: resp.error || 'σφάλμα' };
  const parts = [resp.legal_type, resp.address, resp.email, resp.mobile, resp.phone].filter(Boolean);
  let summary = 'Ανακτήθηκαν: ' + (parts.join(' · ') || '—') + '.';
  if (resp.members_count) summary += ` Μέλη: ${resp.members_count} (ενεργά ανά έτος αποθηκεύτηκαν).`;
  if (resp.members_pending) summary += ' Τα μέλη (ανά έτος) ανακτώνται στο παρασκήνιο και θα αποθηκευτούν σε 1-2 λεπτά.';
  if (resp.gemi_warning) summary += ' ⚠ ' + resp.gemi_warning;
  if (resp.members_error) summary += ` Τα μέλη δεν ανακτήθηκαν: ${resp.members_error}`;
  if (resp.vat_updated === true) summary += ' ΦΠΑ/κατηγορία βιβλίων ενημερώθηκαν (ίδιο login).';
  else if (resp.vat_updated === false) summary += ' Το ΦΠΑ δεν ενημερώθηκε.';
  const noContact = resp.contact_found === false;
  if (noContact) {
    const tags = resp.ldap_debug && resp.ldap_debug.filled_tags ? resp.ldap_debug.filled_tags.join(', ') : '';
    summary += ' ⚠ Δεν βρέθηκαν στοιχεία επικοινωνίας στο myAADE για αυτό το ΑΦΜ (πιθανόν δεν έχουν δηλωθεί εκεί)' + (tags ? ' — πεδία που επέστρεψε η ΑΑΔΕ: ' + tags : '') + '. Μπορείς να τα συμπληρώσεις από το ✏️.';
  }
  return { ok: true, summary, membersError: resp.members_error || null, noContact };
}

// «🔍 Μαζική αναζήτηση»: one ΑΑΔΕ login per company fetches type, address,
// contact details AND ΦΠΑ/κατηγορία βιβλίων together (same registry page),
// for the selected companies (or all, if none is selected).
async function runBulkDetectFromMenu() {
  const mode = await showModalChoice(
    '🔍 Μαζική αναζήτηση ΑΑΔΕ',
    'Τύπος εταιρίας, διεύθυνση, email/κινητό/σταθερό και ΦΠΑ/κατηγορία βιβλίων — με ένα login ανά εταιρία, για τις επιλεγμένες εταιρίες (ή όλες, αν καμία δεν είναι επιλεγμένη). Χρειάζονται αποθηκευμένους κωδικούς TAXISnet.',
    [
      { key: 'missing', label: 'Μόνο όσες δεν έχουν ήδη τα στοιχεία' },
      { key: 'all', label: 'Επανέλεγχος όλων' },
    ]
  );
  if (!mode) return;
  const statusEl = document.getElementById('arSavedBulkStatus');
  const selected = arTableCheckedValues('.ar-saved-table', '.ar-saved-cb');
  const afms = selected.length
    ? selected
    : (window.__arSavedCompanies || []).map((e) => String((e.company || {}).afm || '')).filter(Boolean);
  if (!afms.length) {
    if (statusEl) statusEl.textContent = 'Δεν υπάρχουν αποθηκευμένες εταιρίες.';
    return;
  }
  showArFlash(`Μαζική αναζήτηση ΑΑΔΕ: ξεκίνησε για ${afms.length} εταιρίες…`, 'info', 5000);
  let okCount = 0;
  let skipped = 0;
  const failed = [];
  for (let i = 0; i < afms.length; i++) {
    const afm = afms[i];
    if (statusEl) statusEl.textContent = `ΑΑΔΕ: ${i + 1}/${afms.length} (ΑΦΜ ${afm})…`;
    const resp = await postJson('/api/accounting_result/company_info', { afm, apply_vat: true, only_if_missing: mode === 'missing' });
    if (resp.ok && resp.skipped) skipped++;
    else if (resp.ok) okCount++;
    else failed.push(`${afm} (${resp.error || 'σφάλμα'})`);
  }
  const msg = `Ολοκληρώθηκε: ${okCount} ενημερώθηκαν${skipped ? `, ${skipped} είχαν ήδη στοιχεία` : ''}${failed.length ? `, ${failed.length} σφάλματα: ${failed.join(', ')}` : ''}.`;
  if (statusEl) statusEl.textContent = msg;
  showArFlash('Μαζική αναζήτηση ΑΑΔΕ: ' + msg, failed.length ? 'warning' : 'success', failed.length ? 12000 : 6000);
  loadSavedClients();
}

async function loadSavedClients() {
  const container = document.getElementById('arSavedListContainer');
  let resp;
  try {
    resp = await (await fetch('/api/e3/brain/credentials_store')).json();
  } catch (e) {
    resp = { ok: false, error: String(e) };
  }
  const fromE3Store = resp.ok ? (resp.companies || []) : [];
  if (!resp.ok) {
    console.warn('Αποθηκευμένα: credentials_store fetch failed, falling back to Μαζικός list only:', resp.error);
  }

  // Every company selectable in Μαζικός (= every myDATA credential on this
  // page) should also show up here, even if it was never imported into the
  // separate E3-brain roster. Rather than just merging a client-side-only
  // row for those (which vanished again on refresh, and couldn't be edited
  // or ΦΠΑ-detected since it didn't really exist in the store), persist a
  // minimal record for each into the shared store the first time it's seen.
  const seenAfms = new Set(fromE3Store.map((e) => String((e.company || {}).afm || '').trim()));
  const missing = (window.AR_CREDENTIALS || []).filter((c) => c.vat && !seenAfms.has(c.vat));
  if (missing.length) {
    // Sequential, not Promise.all — concurrent writers here is exactly what
    // corrupted the real e3_company_credentials_store.json in production
    // (each request did its own read-modify-write against the same file;
    // two overlapping writes interleaved their output into invalid JSON).
    // The backend write is atomic now too (_write_credentials_store_atomic),
    // but there's no reason to fire N racing requests when one at a time
    // is simple, safe, and this only ever runs for the handful of AFMs
    // missing from the store.
    for (const c of missing) {
      await postJson('/api/e3/brain/credentials_store/update', {
        snapshot: { company: { afm: c.vat, name: c.name }, members: [] },
      }).catch(() => null);
    }
    try {
      const resp2 = await (await fetch('/api/e3/brain/credentials_store')).json();
      if (resp2.ok) resp = resp2;
    } catch (_) { /* keep the pre-sync resp — next refresh will retry */ }
  }
  const merged = resp.ok ? (resp.companies || []) : fromE3Store;

  window.__arSavedCompanies = merged.slice().sort((a, b) =>
    String((a.company || {}).name || '').localeCompare(String((b.company || {}).name || ''), 'el'));
  container.innerHTML = renderSavedTable(window.__arSavedCompanies);

  container.querySelectorAll('.ar-saved-compute-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      showArTab('single');
      const sel = document.getElementById('arSingleCredential');
      sel.value = btn.dataset.name;
      sel.dispatchEvent(new Event('change'));
    });
  });
  container.querySelectorAll('.ar-saved-edit-btn').forEach((btn) => {
    btn.addEventListener('click', () => openSavedEditModal(btn.dataset.afm));
  });
  container.querySelectorAll('.ar-saved-nomydata-btn').forEach((btn) => {
    btn.addEventListener('click', arToggleNoMydataFilter);
  });
  // Toolbar copy of the same yellow i (shown only while such companies exist).
  const topNoMydata = document.getElementById('arSavedNoMydataBtn');
  if (topNoMydata) {
    topNoMydata.classList.toggle('hidden', !container.querySelector('[data-no-mydata]'));
    topNoMydata.onclick = arToggleNoMydataFilter;
  }
  container.querySelectorAll('.ar-saved-aade-btn').forEach((btn) => {
    btn.addEventListener('click', () => fetchCompanyInfoForSavedRow(btn.dataset.afm, btn, { applyVat: true }));
  });
  fillSavedHistoryCells(container);
  fillSavedVatCells(container);
  initSavedDataTable();
  arLoadContactCache();
}

// Same field set as e3_check.html's own credentials edit modal (company
// info + TAXISnet + ΑΜΚΑ + Ι.Κ.Α. Εργοδότη + myDATA + per-member inline
// editing) — both pages manage the SAME shared store, so an accountant
// editing a company's TAXISnet login here shouldn't have to go find it on
// the other page instead.
function openSavedEditModal(afm) {
  moveModalsToBody(); // see showArOverlay for why this defensive call is needed
  const entry = (window.__arSavedCompanies || []).find((e) => String((e.company || {}).afm || '') === String(afm));
  if (!entry) return;
  const c = entry.company || {};
  document.getElementById('arEditAfm').value = c.afm || '';
  document.getElementById('arEditName').value = c.name || '';
  document.getElementById('arEditAddress').value = c.address || '';
  document.getElementById('arEditTaxisUser').value = c.taxisnet_username || '';
  // Every stored credential field is shown in full, never force-blanked —
  // this is the group's own private vault, not a login form, and hiding
  // a value here just makes every import/edit look like it silently lost
  // that field (that's exactly what happened with mydata_key before this
  // was made consistent across all of them).
  document.getElementById('arEditTaxisPass').value = c.taxisnet_password || '';
  document.getElementById('arEditAmka').value = c.amka || '';
  document.getElementById('arEditLegalType').value = c.legal_type || '';
  document.getElementById('arEditMydataUser').value = c.mydata_user || '';
  document.getElementById('arEditMydataKey').value = c.mydata_key || '';
  document.getElementById('arEditEmail').value = c.email || '';
  document.getElementById('arEditMobile').value = c.mobile || '';
  document.getElementById('arEditPhone').value = c.phone || '';
  document.getElementById('arEditIkaEmpUser').value = c.ika_employer_username || '';
  document.getElementById('arEditIkaEmpPass').value = c.ika_employer_password || '';

  // Νομική μορφή pill — three states, not two: a company with no legal_type
  // AND no members isn't necessarily an individual, it may simply never
  // have had an address/members check ("🔍 Λήψη") run at all — defaulting
  // that to "Ατομική" was actively misleading. Only "Εταιρία" (real members
  // on file) and an explicit stored legal_type resolve confidently; anything
  // else stays "—" (άγνωστο) rather than guessing.
  const members = Array.isArray(entry.members) ? entry.members : [];
  const hasMembers = members.length > 0;
  const storedType = c.legal_type || '';
  const resolution = storedType
    ? (storedType.toLowerCase().indexOf('ατομικ') >= 0 ? 'individual' : 'company')
    : (hasMembers ? 'company' : 'unknown');
  const pillLabel = resolution === 'individual' ? 'Ατομική' : (resolution === 'company' ? 'Εταιρία' : '—');
  const pill = document.getElementById('arEditLegalTypePill');
  pill.textContent = pillLabel;
  if (resolution === 'individual') {
    pill.style.background = '#dbeafe'; pill.style.color = '#1d4ed8'; pill.style.borderColor = '#93c5fd';
  } else if (resolution === 'company') {
    pill.style.background = '#f0fdf4'; pill.style.color = '#166534'; pill.style.borderColor = '#86efac';
  } else {
    pill.style.background = '#f1f5f9'; pill.style.color = '#64748b'; pill.style.borderColor = '#cbd5e1';
  }
  document.getElementById('arEditLegalType').value = storedType;

  // Show the ΑΜΚΑ field unless we're confident this is a company (real
  // members on file) — an unknown-type row still gets the field, since it
  // might turn out to be an individual once checked.
  document.getElementById('arEditAmkaWrap').classList.toggle('hidden', resolution === 'company');
  const membersWrap = document.getElementById('arEditMembersWrap');
  const membersList = document.getElementById('arEditMembersList');
  membersWrap.style.display = hasMembers ? '' : 'none';
  membersList.innerHTML = hasMembers ? members.map((m, i) => {
    const mname = escapeHtml(String(m.full_name || m.name || '').trim() || '—');
    const mafm = escapeHtml(String(m.afm || '').trim() || '—');
    const mrole = escapeHtml(String(m.role || '').trim());
    return `<div class="ar-edit-member-row" data-member-row="${i}">
      <div style="display:flex;justify-content:space-between;font-size:.72rem;color:#475569;margin-bottom:.3rem;">
        <span><strong>${mname}</strong> · ΑΦΜ ${mafm}</span>
        <span style="opacity:.75;">${mrole}</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.4rem;">
        <input data-member-i="${i}" data-field="taxisnet_username" value="${escapeHtml(m.taxisnet_username || '')}" placeholder="TAXIS user">
        <input data-member-i="${i}" data-field="taxisnet_password" value="${escapeHtml(m.taxisnet_password || '')}" placeholder="TAXIS pass">
        <input data-member-i="${i}" data-field="amka" value="${escapeHtml(m.amka || '')}" placeholder="ΑΜΚΑ">
      </div>
    </div>`;
  }).join('') : '';

  window.__arSavedEditEntry = entry;
  // style.display, not classList('hidden') — .brain-cred-modal-overlay's own
  // display:flex is a plain CSS rule (not a Tailwind utility), and it loads
  // after tailwind.css in the cascade, so at equal (single-class) specificity
  // it silently wins over .hidden's display:none, leaving the modal visible
  // even with the 'hidden' class present. Inline style always wins regardless.
  document.getElementById('arSavedEditModal').style.display = 'flex';
}

async function saveSavedEdit() {
  const entry = window.__arSavedEditEntry;
  if (!entry) return;
  const c = entry.company || (entry.company = {});
  c.name = document.getElementById('arEditName').value;
  c.address = document.getElementById('arEditAddress').value;
  c.taxisnet_username = document.getElementById('arEditTaxisUser').value;
  // Every field here now starts pre-filled with its real stored value (see
  // openSavedEditModal) — none of them are "keep existing unless typed"
  // write-only secrets anymore, so save each one as-is; clearing a field
  // here is a deliberate removal.
  c.taxisnet_password = document.getElementById('arEditTaxisPass').value;
  c.amka = document.getElementById('arEditAmka').value;
  c.legal_type = document.getElementById('arEditLegalType').value;
  c.mydata_user = document.getElementById('arEditMydataUser').value;
  c.mydata_key = document.getElementById('arEditMydataKey').value;
  c.email = document.getElementById('arEditEmail').value.trim();
  c.mobile = document.getElementById('arEditMobile').value.trim();
  c.phone = document.getElementById('arEditPhone').value.trim();
  c.ika_employer_username = document.getElementById('arEditIkaEmpUser').value;
  c.ika_employer_password = document.getElementById('arEditIkaEmpPass').value;
  delete entry._synthetic;

  if (Array.isArray(entry.members) && entry.members.length) {
    entry.members.forEach((m, i) => {
      document.querySelectorAll('input[data-member-i="' + i + '"]').forEach((el) => {
        m[el.dataset.field] = String(el.value || '').trim();
      });
    });
  }

  const saveBtn = document.getElementById('arSavedEditSave');
  saveBtn.disabled = true;
  try {
    const resp = await postJson('/api/e3/brain/credentials_store/update', { snapshot: entry });
    if (!resp.ok) { showArFlash(resp.error || 'Σφάλμα αποθήκευσης.', 'error'); return; }
    document.getElementById('arSavedEditModal').style.display = 'none';
    showArFlash('Τα στοιχεία αποθηκεύτηκαν.', 'success', 3500);
    loadSavedClients();
  } finally {
    saveBtn.disabled = false;
  }
}

// Clicking the darkened backdrop (not the white panel itself) closes the
// modal — same "click outside to dismiss" pattern modal_utils.js's own
// showModalConfirm/showModalChoice already use (checking e.target === modal
// so clicks that merely bubble up from something inside the panel don't
// falsely trigger it).
//
// `useInlineStyle` picks which mechanism this modal actually needs:
//  - arBulkRunsModal relies on Tailwind's hidden/flex utilities, toggled
//    purely via classList — that's already sufficient and correct there.
//  - arSavedEditModal's display:flex comes from this page's own
//    .brain-cred-modal-overlay rule (not a Tailwind utility); it loads
//    after tailwind.css so at equal specificity it silently wins over
//    .hidden, so THIS one needs style.display instead.
// Setting BOTH unconditionally was tried and is wrong: once a backdrop
// click sets inline style:none on arBulkRunsModal, showBulkRunsModal()'s
// classList.remove('hidden') alone can no longer undo it (inline always
// wins), so the button silently stops reopening it on the very next click —
// exactly the "works once, not the second time" bug this was meant to fix.
//
// This local handler is not actually what was corrupting arBulkRunsModal
// (or arManualInvModal / arDepPickModal, which never had a backdrop
// handler here at all): modal_utils.js installs its OWN global backdrop/
// Escape dismiss listener on `document` in the CAPTURE phase, which runs
// BEFORE this one. It looks for a recognized close control inside the
// modal (`[data-modal-close]` etc.) and, when it can't find one — which
// was the case for every close/cancel button on this page — falls back to
// forcibly hiding the element itself: `el.style.display = 'none'` AND
// `el.classList.remove('flex')`. That second part permanently strips the
// Tailwind class these modals rely on for centering, and the inline style
// it sets can never be undone by a later `classList.remove('hidden')`
// here — so the very next time the modal is needed (the next company in a
// bulk loop, or the next click of the button) it either renders pinned to
// the top-left (flex gone) or doesn't render at all (inline display:none
// still winning), i.e. it looks "lost". The actual fix is on the
// close/cancel buttons themselves (`data-modal-close` in
// templates/accounting_result.html) so the global handler finds and
// clicks the real close control — running this file's own cleanup (and,
// for arManualInvModal/arDepPickModal, resolving the pending Promise with
// null instead of leaving the caller awaiting forever) — instead of ever
// reaching its destructive fallback.
function _arBindBackdropClose(modalId, useInlineStyle) {
  const modal = document.getElementById(modalId);
  if (!modal) return;
  modal.addEventListener('mousedown', (e) => {
    if (e.target !== modal) return;
    if (useInlineStyle) {
      modal.style.display = 'none';
    } else {
      modal.classList.add('hidden');
    }
  });
}

function arInitSavedTabHandlers() {
  document.getElementById('arSavedRefreshBtn').addEventListener('click', loadSavedClients);
  document.getElementById('arSavedSelectAllBtn').addEventListener('click', () => {
    arTableCheckboxes('.ar-saved-table', '.ar-saved-cb').forEach((cb) => { cb.checked = true; });
    updateArSavedSelectedCount();
  });
  document.getElementById('arSavedSelectNoneBtn').addEventListener('click', () => {
    arTableCheckboxes('.ar-saved-table', '.ar-saved-cb').forEach((cb) => { cb.checked = false; });
    updateArSavedSelectedCount();
  });
  // Delegated: .ar-saved-cb checkboxes are recreated on every
  // loadSavedClients() re-render, so a direct listener per-checkbox would
  // need rebinding each time — same reasoning as arBulkCredentialList's own
  // delegated 'change' listener for updateArBulkSelectedCount().
  document.getElementById('arSavedListContainer').addEventListener('change', (e) => {
    if (e.target && e.target.classList.contains('ar-saved-cb')) updateArSavedSelectedCount();
  });
  document.getElementById('arSavedBulkDeleteBtn').addEventListener('click', bulkDeleteSavedClients);
  document.getElementById('arSavedVatBulkDetectBtn').addEventListener('click', runBulkDetectFromMenu);
  document.getElementById('arSavedEditClose').addEventListener('click', () => { document.getElementById('arSavedEditModal').style.display = 'none'; });
  document.getElementById('arSavedEditCancel').addEventListener('click', () => { document.getElementById('arSavedEditModal').style.display = 'none'; });
  document.getElementById('arSavedEditForm').addEventListener('submit', (e) => { e.preventDefault(); saveSavedEdit(); });
  _arBindBackdropClose('arSavedEditModal', true);
  _arBindBackdropClose('arBulkRunsModal', false);

  document.getElementById('arSavedImportExcelInput').addEventListener('change', async (e) => {
    const inp = e.target;
    if (!inp.files || !inp.files.length) return;
    const file = inp.files[0];
    const replaceEl = document.getElementById('arSavedImportExcelReplace');
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      if (replaceEl && replaceEl.checked) fd.append('replace', '1');
      const resp = await fetch('/api/e3/brain/credentials_store/import_excel', { method: 'POST', body: fd });
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
      showArFlash(`Εισαγωγή Excel: ${data.imported || 0} νέοι, ${data.updated || 0} ενημερωμένοι, ${data.skipped || 0} χωρίς ΑΦΜ.`, 'success', 6500);
      loadSavedClients();
    } catch (err) {
      showArFlash('Αποτυχία Excel import: ' + (err.message || err), 'error', 7000);
    } finally {
      inp.value = '';
    }
  });

  document.getElementById('arSavedBulkBtn').addEventListener('click', () => {
    const afms = new Set((window.__arSavedCompanies || []).map((e) => String((e.company || {}).afm || '')));
    const names = new Set((window.AR_CREDENTIALS || []).filter((c) => afms.has(c.vat)).map((c) => c.name));
    showArTab('bulk');
    arTableCheckboxes('.ar-bulk-table', '.ar-bulk-cb').forEach((cb) => { cb.checked = names.has(cb.value); });
    updateArBulkSelectedCount();
  });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', arInitSavedTabHandlers);
} else {
  arInitSavedTabHandlers();
}

// ---------------- Wiring ----------------

function showArTab(which) {
  document.getElementById('arTabSingle').classList.toggle('hidden', which !== 'single');
  document.getElementById('arTabBulk').classList.toggle('hidden', which !== 'bulk');
  document.getElementById('arTabSaved').classList.toggle('hidden', which !== 'saved');
  document.getElementById('arTabSingleBtn').classList.toggle('active', which === 'single');
  document.getElementById('arTabBulkBtn').classList.toggle('active', which === 'bulk');
  document.getElementById('arTabSavedBtn').classList.toggle('active', which === 'saved');
  if (which === 'saved') loadSavedClients();
}

// Defensive fix for a page-wide stacking bug: #appShell can end up holding a
// non-`none` `transform` (see static/app.css .page-entering comment), which
// per spec makes it the containing block for every `position:fixed`
// descendant — so a modal declared inside {% block content %} renders
// relative to #appShell's box instead of the real viewport, and can end up
// looking like it doesn't cover the sticky header/navmenu. Moving each
// modal to be a direct child of <body> sidesteps this unconditionally
// (mirrors the `appendTo: document.body` fix already used for flatpickr).
function moveModalsToBody() {
  // arBulkRunsModal/arSavedEditModal added here for the same reason as the
  // others: they're position:fixed but live inside {% block content %}
  // (i.e. inside #appShell). Historically this was ALSO this file's fix for
  // #appShell's page-entering transition making #appShell a containing
  // block for position:fixed descendants during partial-nav — that part is
  // now fixed at the source (the transform was dropped from the
  // #appShell.page-entering keyframe in app.css), so position:fixed on
  // these modals resolves against the true viewport regardless of DOM
  // nesting. This function now exists only for DOM tidiness (avoid
  // accumulating duplicate-id copies of these modals in memory across many
  // partial-nav visits — the server re-renders a fresh copy of each of
  // these ids inside #appShell's content on every visit), not for
  // positioning correctness, so it no longer needs to be airtight about
  // exactly when it runs.
  ['arExcelHintModal', 'arSavedExcelHintModal', 'arManualInvModal', 'arDepPickModal', 'waitOverlay', 'arBulkRunsModal', 'arSavedEditModal'].forEach((id) => {
    const matches = document.querySelectorAll('#' + id);
    if (matches.length > 1) {
      // Keep only the element document.getElementById(id) would itself
      // return (matches[0], first in document order) — every caller in
      // this file that looks this id up uses getElementById, so that's the
      // instance actually in use; remove only the extra duplicates left
      // behind by earlier partial-nav visits. Never remove matches[0] here
      // — doing so previously produced a worse bug than the duplicates
      // themselves (the modal vanishing from the DOM entirely on some
      // navigations).
      for (let i = 1; i < matches.length; i++) matches[i].remove();
    }
    const el = document.getElementById(id);
    if (el && el.parentElement !== document.body) document.body.appendChild(el);
  });
}

// Belt-and-braces: call it synchronously right now (the modal HTML is
// already in the DOM by the time this script tag itself runs, whether on a
// real first load or during partial-nav — runPageScripts() always sets
// #appShell's innerHTML before executing any script), AND install a
// MutationObserver below as the actually-reliable mechanism. Relying only
// on document.addEventListener('DOMContentLoaded', …) turned out to be
// flaky on partial-nav (confirmed live, repeatedly: the queued callback
// sometimes silently never fires), and even this synchronous call alone
// was ALSO observed to sometimes not take effect — the exact reason wasn't
// pinned down, but a MutationObserver reacting to the actual DOM insertion
// (same pattern base_01.js already uses for .flash-banner) has no
// script-execution-order dependency to get wrong in the first place.
moveModalsToBody();

if (!window.__arModalObserverInstalled) {
  window.__arModalObserverInstalled = true;
  const AR_MOVE_MODAL_IDS = ['arExcelHintModal', 'arSavedExcelHintModal', 'arManualInvModal', 'arManualPayrollModal', 'arDepPickModal', 'waitOverlay', 'arBulkRunsModal', 'arSavedEditModal'];
  const arModalObserver = new MutationObserver((muts) => {
    for (const m of muts) {
      if (!m.addedNodes || !m.addedNodes.length) continue;
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        const isMatch = n.id && AR_MOVE_MODAL_IDS.indexOf(n.id) !== -1;
        const hasMatch = !isMatch && n.querySelector && AR_MOVE_MODAL_IDS.some((id) => n.querySelector('#' + id));
        if (isMatch || hasMatch) {
          if (typeof moveModalsToBody === 'function') moveModalsToBody();
          return;
        }
      }
    }
  });
  arModalObserver.observe(document.body, { childList: true, subtree: true });
}

function arInitPageHandlers() {
  moveModalsToBody();
  arLoadContactCache();
  initBulkDataTable();
  // Double-click a company's name or ΑΦΜ to toggle its checkbox — delegated
  // on the wrapping container so it keeps working across DataTables redraws
  // (DataTables reuses the same <tr>/<td> nodes, just repositions them).
  document.getElementById('arBulkCredentialList').addEventListener('dblclick', (e) => {
    const cell = e.target.closest('.ar-bulk-name, .ar-bulk-vat');
    if (!cell) return;
    const cb = cell.closest('tr') && cell.closest('tr').querySelector('.ar-bulk-cb');
    if (cb) cb.checked = !cb.checked;
    updateArBulkSelectedCount();
  });
  // Same delegated-container trick, for the selected-count display: any
  // individual checkbox click/keyboard toggle bubbles a 'change' event up
  // to this container regardless of which DataTables page it's on.
  document.getElementById('arBulkCredentialList').addEventListener('change', (e) => {
    if (e.target.classList && e.target.classList.contains('ar-bulk-cb')) updateArBulkSelectedCount();
  });
  document.getElementById('arTabSingleBtn').addEventListener('click', () => showArTab('single'));
  document.getElementById('arTabBulkBtn').addEventListener('click', () => showArTab('bulk'));
  document.getElementById('arTabSavedBtn').addEventListener('click', () => showArTab('saved'));

  document.getElementById('arSingleComputeBtn').addEventListener('click', computeSingle);
  document.getElementById('arSingleExcelFile').addEventListener('change', (e) => uploadSingleExcel(e.target.files[0]));
  document.getElementById('arSingleCredential').addEventListener('change', (e) => {
    // Switching company: the previous company's report (already safely
    // logged to its own history the moment it was computed) shouldn't
    // linger on screen and get mistaken for the newly-selected company's
    // numbers, so close/clear it here.
    document.getElementById('arSingleReportContainer').innerHTML = '';
    document.getElementById('arSingleStatus').textContent = '';
    document.getElementById('arSinglePdfBtn').classList.add('hidden');
    window.__arSingleLastSection = null;
    loadSingleHistory(e.target.value);
  });
  loadSingleHistory(document.getElementById('arSingleCredential').value);
  document.getElementById('arSinglePdfBtn').addEventListener('click', () => {
    const s = window.__arSingleLastSection;
    if (!s) return;
    exportHtmlAsPdf(buildReportSectionHtml(s.name, s.vat, s.from, s.to, s.report), 'Λογιστικό_Αποτέλεσμα_' + s.name + '_' + periodSuffix(s.from, s.to), 'portrait', true);
  });

  document.getElementById('arExcelHintBtn').addEventListener('click', () => { moveModalsToBody(); document.getElementById('arExcelHintModal').classList.remove('hidden'); });
  document.getElementById('arExcelHintClose').addEventListener('click', () => document.getElementById('arExcelHintModal').classList.add('hidden'));

  document.getElementById('arSavedExcelHintBtn').addEventListener('click', () => { moveModalsToBody(); document.getElementById('arSavedExcelHintModal').classList.remove('hidden'); });
  document.getElementById('arSavedExcelHintClose').addEventListener('click', () => document.getElementById('arSavedExcelHintModal').classList.add('hidden'));

  document.getElementById('arBulkRunBtn').addEventListener('click', runBulk);
  document.getElementById('arBulkPdfBtn').addEventListener('click', () => {
    if (window.__arBulkCompanies && window.__arBulkCompanies.length) {
      const p = window.__arBulkPeriod || {};
      const suffix = periodSuffix(p.from, p.to);
      exportZipOfIndividualPdfs(
        window.__arBulkCompanies,
        'Λογιστικό_Αποτέλεσμα_Μαζικό' + (suffix ? '_' + suffix : ''),
        document.getElementById('arBulkStatus'),
      );
    }
  });
  document.getElementById('arBulkConsolidatedPdfBtn').addEventListener('click', () => {
    if (window.__arBulkCompanies && window.__arBulkCompanies.length) {
      const p = window.__arBulkPeriod || {};
      const suffix = periodSuffix(p.from, p.to);
      exportHtmlAsPdf(
        buildConsolidatedTableHtml(window.__arBulkCompanies),
        'Λογιστικό_Αποτέλεσμα_Συγκεντρωτική' + (suffix ? '_' + suffix : ''),
        'landscape',
      );
    }
  });
  document.getElementById('arBulkSelectAllBtn').addEventListener('click', () => {
    arTableCheckboxes('.ar-bulk-table', '.ar-bulk-cb').forEach((cb) => { cb.checked = true; });
    updateArBulkSelectedCount();
  });
  document.getElementById('arBulkSelectNoneBtn').addEventListener('click', () => {
    arTableCheckboxes('.ar-bulk-table', '.ar-bulk-cb').forEach((cb) => { cb.checked = false; });
    updateArBulkSelectedCount();
  });

  document.getElementById('arBulkRunsBtn').addEventListener('click', showBulkRunsModal);
  document.getElementById('arBulkRunsClose').addEventListener('click', () => document.getElementById('arBulkRunsModal').classList.add('hidden'));
  document.getElementById('arBulkRunsBackBtn').addEventListener('click', showBulkRunsListView);
  document.getElementById('arBulkRunsDetailZipBtn').addEventListener('click', () => {
    if (AR_OPEN_BATCH && AR_OPEN_BATCH.companies.length) {
      const b = AR_OPEN_BATCH.batch;
      const suffix = periodSuffix(b.date_from, b.date_to);
      exportZipOfIndividualPdfs(
        AR_OPEN_BATCH.companies,
        'Λογιστικό_Αποτέλεσμα_Μαζικό' + (suffix ? '_' + suffix : ''),
        document.getElementById('arBulkRunsDetailStatus'),
      );
    }
  });
  document.getElementById('arBulkRunsDetailConsolidatedBtn').addEventListener('click', () => {
    if (AR_OPEN_BATCH && AR_OPEN_BATCH.companies.length) {
      const b = AR_OPEN_BATCH.batch;
      const suffix = periodSuffix(b.date_from, b.date_to);
      exportHtmlAsPdf(
        buildConsolidatedTableHtml(AR_OPEN_BATCH.companies),
        'Λογιστικό_Αποτέλεσμα_Συγκεντρωτική' + (suffix ? '_' + suffix : ''),
        'landscape',
      );
    }
  });
  document.getElementById('arBulkRunsDetailDeleteBtn').addEventListener('click', () => {
    if (AR_OPEN_BATCH) deleteBulkRun(AR_OPEN_BATCH.batch.id, showBulkRunsModal);
  });
}
// Same "run now if the DOM is already here, otherwise wait for
// DOMContentLoaded" idiom as moveModalsToBody() above and
// arInitSavedTabHandlers() — DOMContentLoaded fires exactly ONCE per real
// document load. On a partial-nav visit to this page (the app swaps
// #appShell's innerHTML and re-executes this script without a real
// navigation), the event has already long since fired and never will
// again, so a plain `document.addEventListener('DOMContentLoaded', …)`
// here silently never ran — every tab button, the bulk-run button, and the
// Αποθηκευμένα delete button all went dead the moment someone reached this
// page via partial nav instead of a full reload. readyState is already
// 'complete'/'interactive' by the time that happens, so this branch runs
// the init immediately instead.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', arInitPageHandlers);
} else {
  arInitPageHandlers();
}
