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
    return await res.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ---------------- Report table rendering ----------------

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
    `</div>`;

  return `
  <div class="ar-report-section">
    <div class="ar-report-header">
      <div class="ar-company-block">${escapeHtml(name)} <span class="ar-vat">(ΑΦΜ: ${escapeHtml(vat || '')})</span></div>
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
  const rowsHtml = companies.map((c, i) => {
    const r = c.report;
    const openingSum = (r.stock_rows || []).reduce((a, s) => a + (s.opening || 0), 0);
    const purchasesSum = (r.stock_rows || []).reduce((a, s) => a + (s.purchases || 0), 0);
    const closingSum = (r.stock_rows || []).reduce((a, s) => a + (s.closing || 0), 0);
    return `<tr>
      <td class="ar-num">${i + 1}</td>
      <td>${escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.vat || '')}</td>
      <td>${todayStr()}</td>
      <td>${ddmmyyyy(c.from)}</td>
      <td>${ddmmyyyy(c.to)}</td>
      <td class="ar-num">${fmtAmountOrBlank(openingSum)}</td>
      <td class="ar-num">${fmtAmountOrBlank(purchasesSum)}</td>
      <td class="ar-num">${fmtAmountOrBlank(closingSum)}</td>
      <td class="ar-num">${fmtAmountOrBlank(r.cogs_total)}</td>
      <td class="ar-num">${fmtAmountOrBlank(r.expenses_total)}</td>
      <td class="ar-num">${fmtAmountOrBlank(r.sales_total)}</td>
      <td class="ar-num"></td>
      <td class="ar-num">${fmtAmountOrBlank(Math.abs(r.unclassified_net || 0))}</td>
      <td class="ar-num">${fmtAmountOrBlank(r.taxable_result)}</td>
      <td class="ar-num"></td>
      <td class="ar-num">${fmtAmountOrBlank(r.vat_period_balance)}</td>
    </tr>`;
  }).join('');

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
    showArOverlay('Λήψη δεδομένων από myDATA...', 'Υπολογισμός λογιστικού αποτελέσματος - η διαδικασία μπορεί να διαρκέσει.');
    let resp = await postJson('/api/accounting_result/compute', body);
    if (!resp.ok) {
      statusEl.textContent = 'Σφάλμα: ' + (resp.error || '');
      showArFlash('Λογιστικό Αποτέλεσμα (' + name + '): σφάλμα — ' + (resp.error || ''), 'error');
      return;
    }

    if (resp.needs_depreciation_input) {
      hideArOverlay();
      const sel = await resolveDepreciationChoice(resp.depreciation_entries);
      if (!sel) {
        statusEl.textContent = 'Ακυρώθηκε.';
        return;
      }
      body.depreciation_selection = sel;
      showArOverlay('Λήψη δεδομένων από myDATA...', 'Υπολογισμός λογιστικού αποτελέσματος - η διαδικασία μπορεί να διαρκέσει.');
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
      showArOverlay('Λήψη δεδομένων από myDATA...', 'Υπολογισμός λογιστικού αποτελέσματος - η διαδικασία μπορεί να διαρκέσει.');
      resp = await postJson('/api/accounting_result/compute', body);
      if (!resp.ok) {
        statusEl.textContent = 'Σφάλμα: ' + (resp.error || '');
        showArFlash('Λογιστικό Αποτέλεσμα (' + name + '): σφάλμα — ' + (resp.error || ''), 'error');
        return;
      }
    }

    const container = document.getElementById('arSingleReportContainer');
    container.innerHTML = buildReportSectionHtml(name, resp.vat, from, to, resp.report);
    window.__arSingleLastSection = { name, vat: resp.vat, from, to, report: resp.report };
    document.getElementById('arSinglePdfBtn').classList.remove('hidden');
    loadSingleHistory(name);
    showArResultsFlash(
      'Λογιστικό Αποτέλεσμα (Ατομικός): ολοκληρώθηκε — ' + name + ' (Φορολογητέα Κέρδη ' + fmtMoney(resp.report.taxable_result) + ').',
      'success',
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
}

function initSavedDataTable() {
  if (typeof window.jQuery === 'undefined' || !window.jQuery.fn.DataTable) return;
  const $ = window.jQuery;
  const $table = $('.ar-saved-table');
  if (!$table.length) return;
  if ($.fn.DataTable.isDataTable($table)) $table.DataTable().destroy();
  $table.DataTable({
    order: [[1, 'asc']],
    pageLength: 25,
    columnDefs: [{ orderable: false, searchable: false, targets: [2, 3, 5] }],
    language: AR_DT_LANG,
  });
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
function startBulkCrossPageBanner(jobId, total) {
  try {
    sessionStorage.setItem('arBulkActiveJob', JSON.stringify({ jobId, total, startedAt: Date.now() }));
  } catch (_) {}
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
  return container;
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

  const statusEl = document.getElementById('arBulkStatus');
  if (statusEl) statusEl.textContent = message;

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

  if (opts && (opts.zip || opts.consolidated || opts.pdf || opts.view)) {
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
    el.appendChild(row2);
  }

  clearTimeout(el.__arFlashTimer);
  el.__arFlashTimer = setTimeout(() => { try { el.remove(); } catch (_) {} }, 15000);
}


// Prevent the selection from changing while a bulk run is in flight.
function setBulkTableLocked(locked) {
  document.querySelectorAll('.ar-bulk-cb').forEach((cb) => { cb.disabled = locked; });
  ['arBulkSelectAllBtn', 'arBulkSelectNoneBtn', 'arBulkRunBtn'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = locked;
  });
}

// Renders the compact per-company summary table + wires window.__arBulkCompanies
// and the ZIP/Συγκεντρωτικό buttons — shared by a fresh runBulk() and by
// reopening a past run from the «φάκελος αποθηκευμένων μαζικών».
function renderBulkCompaniesSummary(companies) {
  const container = document.getElementById('arBulkReportContainer');
  container.innerHTML = '';
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
    const tdName = document.createElement('td'); tdName.textContent = c.name;
    const tdVat = document.createElement('td'); tdVat.className = 'ar-mono'; tdVat.textContent = c.vat || '';
    const tdAmt = document.createElement('td'); tdAmt.className = 'ar-num'; tdAmt.textContent = fmtMoney(c.report.taxable_result);
    const tdBtn = document.createElement('td'); tdBtn.appendChild(dlBtn);
    tr.append(tdName, tdVat, tdAmt, tdBtn);
    tbody.appendChild(tr);
  });
  container.appendChild(summaryTable);

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
  } catch (e) {
    showArFlash(String(e), 'error');
  }
}

async function runBulk() {
  const from = document.getElementById('arBulkFrom').value;
  const to = document.getElementById('arBulkTo').value;
  const names = Array.from(document.querySelectorAll('.ar-bulk-cb:checked')).map((cb) => cb.value);
  const statusEl = document.getElementById('arBulkStatus');
  document.getElementById('arBulkPdfBtn').disabled = true;
  document.getElementById('arBulkConsolidatedPdfBtn').disabled = true;

  if (!from || !to || !names.length) {
    statusEl.textContent = 'Επιλέξτε περίοδο και τουλάχιστον μία εταιρία.';
    return;
  }

  setBulkTableLocked(true);
  AR_BULK_RUNNING = true;
  try {
  const year = yearFromDMY(to);
  showArOverlay('Λήψη δεδομένων από myDATA...', 'Έλεγχος αποθεμάτων λήξης - η διαδικασία μπορεί να διαρκέσει.');
  const statusResp = await postJson('/api/accounting_result/inventory/bulk_status', { credential_names: names, year, date_from: from });
  hideArOverlay();
  if (!statusResp.ok) {
    statusEl.textContent = 'Σφάλμα: ' + (statusResp.error || '');
    showArFlash('Λογιστικό Αποτέλεσμα (Μαζικός): σφάλμα ελέγχου αποθεμάτων λήξης — ' + (statusResp.error || ''), 'error');
    return;
  }

  const depreciationAmbiguousNames = (statusResp.rows || [])
    .filter((r) => r.depreciation_ambiguous)
    .map((r) => r.name);

  // Only companies myDATA shows as actually tracking inventory (a prior-year
  // closing stock was previously declared) are ever asked about it.
  const flagged = (statusResp.rows || []).filter((r) => r.inventory_applicable && !r.closing_inventory_known && !r.error);
  if (flagged.length) {
    const choice = await showModalChoice(
      `Λείπει απόθεμα λήξης (${flagged.length} εταιρίες)`,
      'Πώς θέλετε να συμπληρωθεί το απόθεμα λήξης για τις εταιρίες που δεν το έχουν καταχωρημένο;',
      [
        { key: 'manual', label: 'Χειροκίνητα ανά εταιρία' },
        { key: 'same_as_opening', label: 'Ίσο με έναρξη (όλες)' },
        { key: 'pct10_up', label: '+10% επί έναρξης (όλες)' },
        { key: 'pct10_down', label: '-10% επί έναρξης (όλες)' },
      ]
    );
    if (!choice) {
      statusEl.textContent = 'Ακυρώθηκε.';
      return;
    }

    if (choice === 'manual') {
      for (const row of flagged) {
        statusEl.textContent = `Απόθεμα λήξης — ${row.name}...`;
        const ok = await resolveInventoryForCompany(row.name, row.vat, year, row.opening_inventory, from, to);
        if (!ok) {
          statusEl.textContent = `Ακυρώθηκε στο ${row.name}.`;
          return;
        }
      }
    } else {
      for (const row of flagged) {
        await postJson('/api/accounting_result/inventory/resolve', {
          credential_name: row.name, year, method: choice, date_from: from, date_to: to,
        });
      }
    }
  }

  const jobId = 'ar-bulk-' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  window.__arBulkPeriod = { from, to };
  statusEl.textContent = '';
  startBulkCrossPageBanner(jobId, names.length);
  let bulkResp;
  try {
    bulkResp = await postJson('/api/accounting_result/bulk_compute', {
      credential_names: names, date_from: from, date_to: to, job_id: jobId,
    });
  } finally {
    stopBulkCrossPageBanner();
  }
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
    if (!res.ok || res.needs_inventory_input) {
      errors.push(`${res.credential_name}: ${res.error || 'εκκρεμεί απόθεμα λήξης'}`);
      return;
    }
    companies.push({ name: res.credential_name, vat: res.vat, from, to, report: res.report });
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
  statusEl.textContent = statusMsg;
  showArResultsFlash(
    'Λογιστικό Αποτέλεσμα (Μαζικός): ' + statusMsg,
    bulkResp.aborted ? 'warning' : (errors.length ? 'warning' : 'success'),
    companies.length ? {
      zip: () => document.getElementById('arBulkPdfBtn').click(),
      consolidated: () => document.getElementById('arBulkConsolidatedPdfBtn').click(),
    } : null,
  );
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
    const credName = afmToCredentialName(afm);
    const actionCell = credName
      ? `<button type="button" class="text-xs px-2 py-1 rounded border hover:bg-gray-50 ar-saved-compute-btn" data-name="${escapeHtml(credName)}">Υπολογισμός</button>`
      : `<span class="text-xs text-gray-400" title="Δεν βρέθηκαν πλήρη myDATA credentials για αυτό το ΑΦΜ στη σελίδα Credentials.">— χωρίς myDATA</span>`;
    return `<tr>
      <td class="ar-mono">${afm}</td>
      <td>${escapeHtml(c.name || '')}</td>
      <td>${typeBadge}</td>
      <td class="ar-saved-vat text-xs" data-afm="${afm}">
        <span class="ar-saved-vat-label text-gray-400">…</span>
        <button type="button" class="ar-saved-vat-detect-btn text-xs px-1.5 py-0.5 rounded border hover:bg-gray-50" data-afm="${afm}" title="Ανάκτηση κατηγορίας βιβλίων/υπαγωγής ΦΠΑ από το Μητρώο ΑΑΔΕ (χρειάζεται αποθηκευμένους κωδικούς TAXISnet)">🔍</button>
      </td>
      <td class="ar-saved-hist text-xs text-gray-500" data-afm="${afm}">…</td>
      <td style="white-space:nowrap;">
        ${actionCell}
        <button type="button" class="text-xs px-2 py-1 rounded border hover:bg-gray-50 ar-saved-edit-btn" data-afm="${afm}" title="Επεξεργασία στοιχείων">✏️</button>
      </td>
    </tr>`;
  }).join('');
  return `<table class="ar-saved-table"><thead><tr>
    <th>ΑΦΜ</th><th>Επωνυμία</th><th>Τύπος</th><th>ΦΠΑ</th><th>Τελευταίος υπολογισμός</th><th>Ενέργειες</th>
  </tr></thead><tbody>${trs}</tbody></table>`;
}

function vatProfileLabel(profile) {
  if (!profile || profile.vat_subject === undefined || profile.vat_subject === null) return '— άγνωστο';
  if (profile.vat_subject === false) return '❌ Όχι ΦΠΑ';
  const period = profile.vat_period_type === 'monthly' ? 'μηνιαίο' : (profile.vat_period_type === 'quarterly' ? '3μηνο' : '');
  return '✓ ΦΠΑ' + (period ? ' (' + period + ')' : '');
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
  container.querySelectorAll('.ar-saved-vat-detect-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const afm = btn.dataset.afm;
      const label = btn.parentElement.querySelector('.ar-saved-vat-label');
      btn.disabled = true;
      if (label) label.textContent = '⌛...';
      const resp = await postJson('/api/accounting_result/vat_profile/detect', { vat: afm });
      if (resp.ok) {
        if (label) label.textContent = vatProfileLabel(resp.profile);
      } else {
        if (label) label.textContent = '⚠ ' + (resp.error || 'σφάλμα');
      }
      btn.disabled = false;
    });
  });
}

async function fillSavedHistoryCells(container) {
  const cells = Array.from(container.querySelectorAll('.ar-saved-hist'));
  await Promise.all(cells.map(async (cell) => {
    const afm = cell.dataset.afm;
    if (!afm) { cell.textContent = '—'; return; }
    try {
      const res = await fetch('/api/accounting_result/history?vat=' + encodeURIComponent(afm));
      const data = await res.json();
      const latest = data.ok && data.history && data.history[0];
      if (!latest) { cell.textContent = '—'; return; }
      const ts = new Date(latest.timestamp);
      const tsStr = String(ts.getDate()).padStart(2, '0') + '/' + String(ts.getMonth() + 1).padStart(2, '0') + '/' + ts.getFullYear();
      cell.textContent = tsStr + (latest.computed_by ? ' (' + latest.computed_by + ')' : '');
    } catch (e) {
      cell.textContent = '—';
    }
  }));
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
  fillSavedHistoryCells(container);
  fillSavedVatCells(container);
  initSavedDataTable();
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
  document.getElementById('arEditTaxisPass').value = '';
  document.getElementById('arEditAmka').value = c.amka || '';
  document.getElementById('arEditLegalType').value = c.legal_type || '';
  document.getElementById('arEditMydataUser').value = c.mydata_user || '';
  document.getElementById('arEditMydataKey').value = '';
  document.getElementById('arEditIkaEmpUser').value = c.ika_employer_username || '';
  document.getElementById('arEditIkaEmpPass').value = '';

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
        <input data-member-i="${i}" data-field="taxisnet_password" type="password" placeholder="TAXIS pass (κενό=διατήρηση)">
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
  const newTaxisPass = document.getElementById('arEditTaxisPass').value;
  if (newTaxisPass) c.taxisnet_password = newTaxisPass; // empty = keep existing
  c.amka = document.getElementById('arEditAmka').value;
  c.legal_type = document.getElementById('arEditLegalType').value;
  c.mydata_user = document.getElementById('arEditMydataUser').value;
  const newMydataKey = document.getElementById('arEditMydataKey').value;
  if (newMydataKey) c.mydata_key = newMydataKey; // empty = keep existing
  c.ika_employer_username = document.getElementById('arEditIkaEmpUser').value;
  const newIkaEmpPass = document.getElementById('arEditIkaEmpPass').value;
  if (newIkaEmpPass) c.ika_employer_password = newIkaEmpPass; // empty = keep existing
  delete entry._synthetic;

  if (Array.isArray(entry.members) && entry.members.length) {
    entry.members.forEach((m, i) => {
      document.querySelectorAll('input[data-member-i="' + i + '"]').forEach((el) => {
        const field = el.dataset.field;
        const v = String(el.value || '').trim();
        if (field === 'taxisnet_password') {
          if (v) m.taxisnet_password = v;
          return;
        }
        m[field] = v;
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

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('arSavedRefreshBtn').addEventListener('click', loadSavedClients);
  document.getElementById('arSavedEditClose').addEventListener('click', () => { document.getElementById('arSavedEditModal').style.display = 'none'; });
  document.getElementById('arSavedEditCancel').addEventListener('click', () => { document.getElementById('arSavedEditModal').style.display = 'none'; });
  document.getElementById('arSavedEditForm').addEventListener('submit', (e) => { e.preventDefault(); saveSavedEdit(); });
  _arBindBackdropClose('arSavedEditModal', true);
  _arBindBackdropClose('arBulkRunsModal', false);

  document.getElementById('arSavedImportExcelInput').addEventListener('change', async (e) => {
    const inp = e.target;
    if (!inp.files || !inp.files.length) return;
    const file = inp.files[0];
    const statusEl = document.getElementById('arSavedImportExcelStatus');
    const replaceEl = document.getElementById('arSavedImportExcelReplace');
    statusEl.textContent = '⌛ Ανάγνωση...';
    statusEl.style.color = '#6b7280';
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      if (replaceEl && replaceEl.checked) fd.append('replace', '1');
      const resp = await fetch('/api/e3/brain/credentials_store/import_excel', { method: 'POST', body: fd });
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
      statusEl.textContent = `✓ ${data.imported || 0} νέοι, ${data.updated || 0} ενημερωμένοι, ${data.skipped || 0} χωρίς ΑΦΜ`;
      statusEl.style.color = '#16a34a';
      loadSavedClients();
    } catch (err) {
      statusEl.textContent = '✗ ' + (err.message || err);
      statusEl.style.color = '#dc2626';
    } finally {
      inp.value = '';
    }
  });

  document.getElementById('arSavedBulkBtn').addEventListener('click', () => {
    const afms = new Set((window.__arSavedCompanies || []).map((e) => String((e.company || {}).afm || '')));
    const names = new Set((window.AR_CREDENTIALS || []).filter((c) => afms.has(c.vat)).map((c) => c.name));
    showArTab('bulk');
    document.querySelectorAll('.ar-bulk-cb').forEach((cb) => { cb.checked = names.has(cb.value); });
  });
});

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
  const AR_MOVE_MODAL_IDS = ['arExcelHintModal', 'arSavedExcelHintModal', 'arManualInvModal', 'arDepPickModal', 'waitOverlay', 'arBulkRunsModal', 'arSavedEditModal'];
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

document.addEventListener('DOMContentLoaded', () => {
  moveModalsToBody();
  initBulkDataTable();
  // Double-click a company's name or ΑΦΜ to toggle its checkbox — delegated
  // on the wrapping container so it keeps working across DataTables redraws
  // (DataTables reuses the same <tr>/<td> nodes, just repositions them).
  document.getElementById('arBulkCredentialList').addEventListener('dblclick', (e) => {
    const cell = e.target.closest('.ar-bulk-name, .ar-bulk-vat');
    if (!cell) return;
    const cb = cell.closest('tr') && cell.closest('tr').querySelector('.ar-bulk-cb');
    if (cb) cb.checked = !cb.checked;
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
    document.querySelectorAll('.ar-bulk-cb').forEach((cb) => { cb.checked = true; });
  });
  document.getElementById('arBulkSelectNoneBtn').addEventListener('click', () => {
    document.querySelectorAll('.ar-bulk-cb').forEach((cb) => { cb.checked = false; });
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
});
