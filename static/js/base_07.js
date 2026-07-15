    (function(){
      const thisYear = new Date().getFullYear();
      const FY_STORAGE_KEY = 'app:activeFiscalYear';
      const serverInitialFiscalYear = (function(){
        try { return window.APP_CONFIG.activeYear; } catch(_) { return null; }
      })();
      let initialFiscalYear = null;

      function readCachedFiscalYear(){
        try {
          const raw = localStorage.getItem(FY_STORAGE_KEY);
          if (raw === null || raw === '') return null;
          const parsed = Number(raw);
          return Number.isFinite(parsed) ? parsed : null;
        } catch(_) {
          return null;
        }
      }

      function syncFiscalYearState(year){
        const normalized = Number(year);
        if (!Number.isFinite(normalized)) return;
        try { localStorage.setItem(FY_STORAGE_KEY, String(normalized)); } catch(_) {}
        try { window.__ACTIVE_FISCAL_YEAR = normalized; } catch(_) {}
        try { document.body.dataset.activeFiscalYear = String(normalized); } catch(_) {}
      }

      function dispatchFiscalYearChanged(year){
        try {
          window.dispatchEvent(new CustomEvent('app:fiscal-year-changed', {
            detail: { year: Number(year) }
          }));
        } catch(_) {}
      }

      function resolveInitialFiscalYear(){
        if (serverInitialFiscalYear !== null && serverInitialFiscalYear !== undefined) {
          return Number(serverInitialFiscalYear);
        }
        const cached = readCachedFiscalYear();
        if (cached !== null) return cached;
        return thisYear;
      }

      function populateFiscalSelect(selected){
        const sel = document.getElementById('fiscalYearSelect');
        if(!sel) return;
        sel.innerHTML = '';
        for(let y=thisYear-1; y<=thisYear+2; y++){
          const opt = document.createElement('option');
          opt.value = String(y);
          opt.textContent = String(y);
          if(Number(selected) === y) opt.selected = true;
          sel.appendChild(opt);
        }
      }

      function toggleSaveButton(){
        const select = document.getElementById('fiscalYearSelect');
        const saveBtn = document.getElementById('fiscalYearSaveBtn');
        const checkmark = document.getElementById('fiscalYearCheckmark');
        
        if (!select || !saveBtn || !checkmark) {
          console.log('Elements not found:', {select, saveBtn, checkmark});
          return;
        }
        
        const currentValue = Number(select.value);
        const hasChanged = initialFiscalYear !== null && currentValue !== initialFiscalYear;
        
        console.log('Toggle:', {currentValue, initialFiscalYear, hasChanged});
        
        if (hasChanged) {
          // User changed the year - show save button, hide checkmark
          saveBtn.classList.remove('hidden');
          checkmark.classList.add('hidden');
          console.log('Showing save button, hiding checkmark');
        } else {
          // Year is same as initial - hide save button, show checkmark
          saveBtn.classList.add('hidden');
          checkmark.classList.remove('hidden');
          console.log('Hiding save button, showing checkmark');
        }
      }

      async function loadFiscalYearIntoUi(){
        const resolvedInitial = resolveInitialFiscalYear();
        initialFiscalYear = resolvedInitial;
        populateFiscalSelect(resolvedInitial);
        syncFiscalYearState(resolvedInitial);
        toggleSaveButton();

        try{
          const r = await fetch('/get_fiscal_year', { credentials:'same-origin' });
          const j = await r.json();
          initialFiscalYear = Number((j && j.fiscal_year) || resolvedInitial || thisYear);
          populateFiscalSelect(initialFiscalYear);
          syncFiscalYearState(initialFiscalYear);
          toggleSaveButton();
        } catch {
          initialFiscalYear = resolvedInitial;
          populateFiscalSelect(resolvedInitial);
          syncFiscalYearState(resolvedInitial);
          toggleSaveButton();
        }
      }

      async function saveFiscalYear(e){
        e.preventDefault();
        const year = Number(document.getElementById('fiscalYearSelect').value);
        const checkmark = document.getElementById('fiscalYearCheckmark');
        const saveBtn = document.getElementById('fiscalYearSaveBtn');
        try{
          const r = await fetch('/set_fiscal_year', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fiscal_year: year })
          });
          const j = await r.json();
          if (j.success){
            initialFiscalYear = year;
            syncFiscalYearState(year);
            populateFiscalSelect(year);
            if (saveBtn) saveBtn.classList.add('hidden');
            if (checkmark) {
              checkmark.classList.remove('hidden');
            }
            dispatchFiscalYearChanged(year);
            try {
              if (typeof window.__partialReloadCurrentPage === 'function') {
                await window.__partialReloadCurrentPage();
              } else {
                window.location.reload();
              }
            } catch (_) {
              window.location.reload();
            }
          }
        } catch {
          // Keep button visible on error
        }
      }

      function initFiscalYearUi(){
        loadFiscalYearIntoUi();
        const btn = document.getElementById('fiscalYearSaveBtn');
        const select = document.getElementById('fiscalYearSelect');
        if (btn && btn.dataset.fiscalBound !== '1') {
          btn.dataset.fiscalBound = '1';
          btn.addEventListener('click', saveFiscalYear);
        }
        if (select && select.dataset.fiscalBound !== '1') {
          select.dataset.fiscalBound = '1';
          select.addEventListener('change', toggleSaveButton);
        }
      }

      window.__initFiscalYearUi = initFiscalYearUi;
      document.addEventListener('DOMContentLoaded', initFiscalYearUi);
    })();
  
