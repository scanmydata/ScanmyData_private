/**
 * Modal Utility Functions
 * Replaces browser alert/confirm with custom modal dialogs
 */

/**
 * Show an alert modal
 * @param {string} title - Modal title
 * @param {string} message - Modal message body
 * @param {string} buttonText - Button text (default: "OK")
 * @returns {Promise<void>}
 */
async function showModalAlert(title, message, buttonText = 'OK') {
  return new Promise((resolve) => {
    const modal = document.createElement('div');
    modal.className = 'fixed inset-0 flex items-center justify-center bg-black/40 z-[110000]';
    modal.id = 'modalAlert_' + Date.now();
    modal.setAttribute('data-managed', '1');

    modal.innerHTML = `
      <div class="modal-warning-panel max-w-md w-11/12">
        <div class="modal-warning-title">${escapeHtml(title)}</div>
        <div class="modal-warning-body">
          <p>${escapeHtml(message).replace(/\n/g, '<br>')}</p>
        </div>
        <div class="modal-warning-actions">
          <button type="button" class="modal-warning-btn modal-warning-btn--muted modal-alert-ok">
            ${escapeHtml(buttonText)}
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    const okBtn = modal.querySelector('.modal-alert-ok');
    okBtn.focus();

    const done = () => { document.removeEventListener('keydown', handleEscape); modal.remove(); resolve(); };

    okBtn.addEventListener('click', done);

    // Dismiss when clicking outside the panel (on the backdrop).
    modal.addEventListener('mousedown', (e) => { if (e.target === modal) done(); });

    // Close on Escape
    const handleEscape = (e) => { if (e.key === 'Escape') done(); };
    document.addEventListener('keydown', handleEscape);
  });
}

/**
 * Show a confirm modal
 * @param {string} title - Modal title
 * @param {string} message - Modal message body
 * @param {string} confirmText - Confirm button text (default: "Επιβεβαίωση")
 * @param {string} cancelText - Cancel button text (default: "Άκυρο")
 * @returns {Promise<boolean>} - true if confirmed, false if cancelled
 */
async function showModalConfirm(title, message, confirmText = 'Επιβεβαίωση', cancelText = 'Άκυρο') {
  return new Promise((resolve) => {
    const modal = document.createElement('div');
    modal.className = 'fixed inset-0 flex items-center justify-center bg-black/40 z-[110000]';
    modal.id = 'modalConfirm_' + Date.now();
    modal.setAttribute('data-managed', '1');

    modal.innerHTML = `
      <div class="modal-warning-panel max-w-md w-11/12">
        <div class="modal-warning-title">⚠️ ${escapeHtml(title)}</div>
        <div class="modal-warning-body">
          <p>${escapeHtml(message).replace(/\n/g, '<br>')}</p>
        </div>
        <div class="modal-warning-actions">
          <button type="button" class="modal-warning-btn modal-warning-btn--muted modal-confirm-cancel">
            ${escapeHtml(cancelText)}
          </button>
          <button type="button" class="modal-warning-btn modal-warning-btn--danger modal-confirm-ok">
            ${escapeHtml(confirmText)}
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    const confirmBtn = modal.querySelector('.modal-confirm-ok');
    const cancelBtn = modal.querySelector('.modal-confirm-cancel');

    cancelBtn.focus();

    const finish = (val) => { document.removeEventListener('keydown', handleEscape); modal.remove(); resolve(val); };

    confirmBtn.addEventListener('click', () => finish(true));
    cancelBtn.addEventListener('click', () => finish(false));

    // Dismiss (as cancel) when clicking outside the panel (on the backdrop).
    modal.addEventListener('mousedown', (e) => { if (e.target === modal) finish(false); });

    // Close on Escape (as cancel)
    const handleEscape = (e) => { if (e.key === 'Escape') finish(false); };
    document.addEventListener('keydown', handleEscape);
  });
}

/**
 * Escape HTML special characters
 * @param {string} text - Text to escape
 * @returns {string} - Escaped HTML
 */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}


/**
 * Show a choice modal with multiple options
 * @param {string} title
 * @param {string} message
 * @param {Array<{key:string,label:string}>} options - array of {key,label}
 * @returns {Promise<string|null>} - selected key or null if cancelled
 */
async function showModalChoice(title, message, options) {
  return new Promise((resolve) => {
    const modal = document.createElement('div');
    modal.className = 'fixed inset-0 flex items-center justify-center bg-black/40 z-[110000]';
    modal.id = 'modalChoice_' + Date.now();
    modal.setAttribute('data-managed', '1');

    // Make the action buttons prominent: the first option is the primary CTA.
    const primaryStyle = 'margin:6px;font-size:1rem;font-weight:700;padding:12px 20px;border-radius:10px;background-color:#0284c7;color:#fff;box-shadow:0 2px 8px rgba(2,132,199,0.35);border:none;min-width:140px;';
    const secondaryStyle = 'margin:6px;font-size:1rem;font-weight:600;padding:12px 20px;border-radius:10px;background-color:#0f766e;color:#fff;border:none;min-width:120px;';
    const buttonsHtml = (options || []).map((opt, i) => `
      <button type="button" data-key="${escapeHtml(opt.key)}" class="modal-choice-btn modal-warning-btn modal-choice-option" style="${i === 0 ? primaryStyle : secondaryStyle}">
        ${escapeHtml(opt.label)}
      </button>
    `).join('');

    modal.innerHTML = `
      <div class="modal-warning-panel max-w-md w-11/12" style="border:2px solid #0284c7;">
        <div class="modal-warning-title" style="font-size:1.15rem;font-weight:700;">${escapeHtml(title)}</div>
        <div class="modal-warning-body">
          <p>${escapeHtml(message).replace(/\n/g, '<br>')}</p>
        </div>
        <div class="modal-warning-actions" style="display:flex;flex-wrap:wrap;gap:4px;justify-content:center;align-items:center;padding-top:6px;">
          ${buttonsHtml}
          <button type="button" class="modal-warning-btn modal-warning-btn--muted modal-choice-cancel" style="margin:6px;font-size:0.95rem;font-weight:600;padding:12px 18px;border-radius:10px;min-width:100px;">Άκυρο</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    const finish = (val) => { document.removeEventListener('keydown', handleEscape); modal.remove(); resolve(val); };

    const optionBtns = modal.querySelectorAll('.modal-choice-option');
    optionBtns.forEach(btn => {
      btn.addEventListener('click', () => finish(btn.getAttribute('data-key')));
    });

    const cancelBtn = modal.querySelector('.modal-choice-cancel');
    // Focus the primary CTA so keyboard users act on it directly.
    const firstOption = modal.querySelector('.modal-choice-option');
    (firstOption || cancelBtn).focus();
    cancelBtn.addEventListener('click', () => finish(null));

    // Dismiss (as cancel) when clicking outside the panel (on the backdrop).
    modal.addEventListener('mousedown', (e) => { if (e.target === modal) finish(null); });

    const handleEscape = (e) => { if (e.key === 'Escape') finish(null); };
    document.addEventListener('keydown', handleEscape);
  });
}

/**
 * Create a simple wrapper for existing showAlert pattern
 * If a modal with id 'id' and data-role='modal-warning' exists, use it
 * Otherwise create a new one
 */
function showWarningModal(id, title, message, confirmText = 'Επιβεβαίωση', cancelText = 'Άκυρο') {
  const existing = document.getElementById(id);
  if (existing && existing.dataset.role === 'modal-warning') {
    const titleEl = existing.querySelector('.modal-warning-title');
    const bodyEl = existing.querySelector('.modal-warning-body');

    if (titleEl) titleEl.textContent = title;
    if (bodyEl) bodyEl.innerHTML = `<p>${escapeHtml(message).replace(/\n/g, '<br>')}</p>`;

    existing.classList.remove('hidden');
    return existing;
  }

  return null; // Fall back to showModalConfirm if not found
}


/**
 * Global modal dismissal: pressing Escape or clicking on the backdrop (outside
 * the modal panel) closes the top-most open modal overlay.
 *
 * Applies to the app's static overlay modals (repeat mapping, AFM warning,
 * summary, delete, …). The programmatic modals above manage their own
 * Escape/backdrop handling and are marked [data-managed] so they are skipped
 * here. Camera/loading/toast overlays are excluded so they are not closed
 * accidentally.
 */
(function installGlobalModalDismiss(){
  if (window.__globalModalDismissInstalled) return;
  window.__globalModalDismissInstalled = true;

  var SKIP_ID_RE = /camera|wait|loading|spinner|overlay|toast|flash|scanner|qr/i;

  function classString(el){
    try { return (el.className && el.className.toString) ? el.className.toString() : String(el.className || ''); }
    catch(_) { return ''; }
  }

  function isDismissableModal(el){
    if (!el || el.nodeType !== 1) return false;
    if (el.hasAttribute && el.hasAttribute('data-managed')) return false;   // handled by its own promise
    if (el.getAttribute && el.getAttribute('data-no-dismiss') === '1') return false;
    var cls = classString(el);
    if (!/\bfixed\b/.test(cls) || !/\binset-0\b/.test(cls)) return false;
    var id = (el.id || '');
    if (SKIP_ID_RE.test(id) || SKIP_ID_RE.test(cls)) return false;
    // Must look like a modal: id/class mentions "modal", has a modal role, or
    // wraps a recognizable modal panel.
    if (/modal/i.test(id) || /modal/i.test(cls)) return true;
    if (el.getAttribute && (el.getAttribute('data-role') || el.getAttribute('data-modal-type'))) return true;
    try {
      if (el.querySelector && el.querySelector('.modal-warning-panel, .modal-summary-panel, [data-modal-panel]')) return true;
    } catch(_) {}
    return false;
  }

  function isVisible(el){
    try {
      if (!el) return false;
      if (el.classList && el.classList.contains('hidden')) return false;
      var st = getComputedStyle(el);
      if (!st || st.display === 'none' || st.visibility === 'hidden') return false;
      return true;
    } catch(_) { return false; }
  }

  function dismiss(el){
    try {
      // Prefer the modal's own close/cancel control so its cleanup runs.
      var closer = el.querySelector(
        '[data-modal-close], .modal-warning-close, .modal-close, [data-dismiss], ' +
        '#repeatModalCancel, #repeatModalCloseX, #deleteCancel, #deleteModalClose'
      );
      if (closer && isVisible(closer)) { closer.click(); return; }
    } catch(_) {}
    try {
      el.style.display = 'none';
      el.classList.add('hidden');
      el.classList.remove('flex');
    } catch(_) {}
  }

  function topmostOpenModal(){
    var nodes = Array.prototype.slice.call(document.querySelectorAll('div.fixed.inset-0'));
    var open = nodes.filter(function(n){ return isDismissableModal(n) && isVisible(n); });
    if (!open.length) return null;
    // Highest z-index (fallback: last in DOM order).
    open.sort(function(a, b){
      var za = parseInt(getComputedStyle(a).zIndex, 10) || 0;
      var zb = parseInt(getComputedStyle(b).zIndex, 10) || 0;
      return za - zb;
    });
    return open[open.length - 1];
  }

  // Backdrop click: only when the click target is the overlay itself.
  document.addEventListener('mousedown', function(e){
    var t = e.target;
    if (isDismissableModal(t) && isVisible(t)) {
      dismiss(t);
    }
  }, true);

  // Escape closes the top-most open modal.
  document.addEventListener('keydown', function(e){
    if (e.key !== 'Escape' && e.keyCode !== 27) return;
    var modal = topmostOpenModal();
    if (modal) {
      e.stopPropagation();
      dismiss(modal);
    }
  }, true);
})();
