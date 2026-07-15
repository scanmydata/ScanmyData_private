    (function(){
      const SELECTORS = ['[data-flash]', '.flash', '.alert', '[role="alert"]'];
      const AUTO_HIDE_MS = 0; // βάλε 6000 αν θέλεις αυτομάτως να κλείνουν

      function makeDismissable(el){
        if (!el || el.dataset.dismissable === '1') return;
        el.dataset.dismissable = '1';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('aria-label','Close');
        btn.textContent = '×';
        btn.style.cssText =
          'position:absolute;top:.4rem;right:.5rem;line-height:1;font-size:20px;' +
          'opacity:.75;cursor:pointer;background:transparent;border:0;padding:0;z-index:1';

        const cs = getComputedStyle(el);
        if (cs.position === 'static') el.style.position = 'relative';
        if (!el.style.paddingRight) {
          const pr = parseFloat(cs.paddingRight || '0');
          if (pr < 22) el.style.paddingRight = '28px';
        }
        btn.addEventListener('click', ()=> {
          try {
            const sig = String(el.dataset.fetchFlashSignature || '').trim();
            if (sig && typeof window.__dismissFetchFlashSignature === 'function') {
              window.__dismissFetchFlashSignature(sig);
            }
          } catch(_) {}
          try{ el.remove(); }catch(_){ }
        });
        el.appendChild(btn);

        if (AUTO_HIDE_MS > 0) setTimeout(()=>{ try{ el.remove(); }catch(_){ } }, AUTO_HIDE_MS);
      }

      function scan(root=document){
        root.querySelectorAll(SELECTORS.join(',')).forEach(makeDismissable);
      }

      if (document.readyState === 'loading'){
        document.addEventListener('DOMContentLoaded', ()=>scan(), { once:true });
      } else {
        scan();
      }

      const mo = new MutationObserver(muts=>{
        for (const m of muts) m.addedNodes && m.addedNodes.forEach(n=>{
          if (n.nodeType===1) scan(n);
        });
        });
      mo.observe(document.body, { childList:true, subtree:true });

      document.addEventListener('click', (e)=>{
        const b = e.target.closest && e.target.closest('[data-dismiss="flash"], .btn-close, .close');
        if (!b) return;
        const host = b.closest(SELECTORS.join(',')) || b.parentElement;
        if (host) {
          e.preventDefault();
          try {
            const sig = String(host.dataset.fetchFlashSignature || '').trim();
            if (sig && typeof window.__dismissFetchFlashSignature === 'function') {
              window.__dismissFetchFlashSignature(sig);
            }
          } catch(_) {}
          try{ host.remove(); }catch(_){ }
        }
      }, true);

      try { window.__scanDismissableFlashes = scan; } catch (_) {}
    })();

    // Side menu toggle functionality
    function initSideMenuAndShell(){
      const sideMenu = document.getElementById('sideMenu');
      const sideMenuOverlay = document.getElementById('sideMenuOverlay');
      const sideMenuToggle = document.getElementById('sideMenuToggle');
      const sideMenuClose = document.getElementById('sideMenuClose');
      const groupsList = document.getElementById('groupsList');

      if (!sideMenu || !sideMenuToggle) return;

      function openMenu() {
        sideMenu.classList.remove('hidden');
        sideMenuOverlay && sideMenuOverlay.classList.remove('hidden');
        requestAnimationFrame(function() {
          sideMenu.classList.remove('translate-x-full');
          sideMenuOverlay && sideMenuOverlay.classList.remove('opacity-0', 'invisible');
        });
      }

      function closeMenu() {
        sideMenu.classList.add('translate-x-full');
        if (sideMenuOverlay) {
          sideMenuOverlay.classList.add('opacity-0', 'invisible');
        }
        setTimeout(function() {
          if (sideMenu.classList.contains('translate-x-full')) {
            sideMenu.classList.add('hidden');
          }
          if (sideMenuOverlay && sideMenuOverlay.classList.contains('opacity-0') && sideMenuOverlay.classList.contains('invisible')) {
            sideMenuOverlay.classList.add('hidden');
          }
        }, 320);
      }

      // Toggle side menu open/close (right-side menu)
      function toggleMenu() {
        if (sideMenu.classList.contains('translate-x-full') || sideMenu.classList.contains('hidden')) {
          openMenu();
        } else {
          closeMenu();
        }
      }

      if (sideMenuToggle && sideMenuToggle.dataset.menuBound !== '1') {
        sideMenuToggle.dataset.menuBound = '1';
        sideMenuToggle.addEventListener('click', toggleMenu);
      }

      if (sideMenuClose && sideMenuClose.dataset.menuBound !== '1') {
        sideMenuClose.dataset.menuBound = '1';
        sideMenuClose.addEventListener('click', toggleMenu);
      }

      if (sideMenuOverlay && sideMenuOverlay.dataset.menuBound !== '1') {
        sideMenuOverlay.dataset.menuBound = '1';
        sideMenuOverlay.addEventListener('click', toggleMenu);
      }

      // Populate groups list from page
      if (groupsList) {
        try {
          fetch('/api/user_groups')
            .then(r => r.json())
            .then(data => {
              if (data.groups && Array.isArray(data.groups)) {
                groupsList.innerHTML = data.groups.map(g => `
                  <button type="button" onclick="selectGroup('${g.name.replace(/'/g,"\\'") }')" 
                          class="w-full text-left px-3 py-2 rounded text-sm hover:bg-sky-100 text-gray-700 border border-gray-200">
                    ${g.name === data.active_group ? '✓ ' : ''}${escapeHtml(g.name)}
                  </button>
                `).join('');
              }
            })
            .catch(err => console.warn('Failed to load groups:', err));
        } catch (e) {
          console.warn('Groups list error:', e);
        }
      }

      // Helper to select a group
      // Select a group without interactive warning
      window.selectGroup = function(groupName) {
        fetch('/groups/select', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({group: groupName})
        })
        .then(async r => {
          const j = await r.json().catch(()=> ({}));
          if (r.ok && j.ok) {
            // close menu and reload
            try { toggleMenu(); } catch(_) {}
            if (j.redirect_url) {
              window.location.href = j.redirect_url;
            } else {
              window.location.reload();
            }
          } else if (j && j.error) {
            showModalAlert('Σφάλμα', j.error);
          } else {
            showModalAlert('Σφάλμα', 'Failed to select group');
          }
        })
        .catch(err => showModalAlert('Σφάλμα', 'Request failed: ' + err));
      };

      // Keep header group switch consistent with side menu fast flow.
      window.openHeaderGroupSwitcher = function(evt) {
        try {
          if (evt && typeof evt.preventDefault === 'function') {
            evt.preventDefault();
          }
          openMenu();
          return false;
        } catch (_) {
          return true;
        }
      };
      
      try {
        const anchors = Array.from(sideMenu.querySelectorAll('a'));
        const path = window.location.pathname.replace(/\/$/, '') || '/';
        anchors.forEach(a => {
          try {
            const href = new URL(a.getAttribute('href') || '', window.location.origin).pathname.replace(/\/$/, '') || '/';
            if (path === href || path.indexOf(href) === 0 || href.indexOf(path) === 0) {
              a.classList.add('side-menu-active');
            } else {
              a.classList.remove('side-menu-active');
            }
          } catch(_){ }
        });
      } catch (e) { console.warn('side menu highlight error', e); }
    }

    window.__initBaseShell = initSideMenuAndShell;
    document.addEventListener('DOMContentLoaded', initSideMenuAndShell);
    
    // Dark mode toggle functionality
    (function(){
      const THEME_KEY = 'theme';

      function readTheme(){
        try {
          return localStorage.getItem(THEME_KEY) || document.documentElement.getAttribute('data-theme') || 'light';
        } catch (_) {
          return document.documentElement.getAttribute('data-theme') || 'light';
        }
      }

      function applyTheme(theme){
        const normalized = theme === 'dark' ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', normalized);
        document.documentElement.style.colorScheme = normalized === 'dark' ? 'dark' : 'light';
        try { localStorage.setItem(THEME_KEY, normalized); } catch (_) {}

        const themeToggle = document.getElementById('themeToggle');
        if (themeToggle) {
          themeToggle.checked = normalized === 'dark';
        }
      }

      function initThemeToggle(){
        const themeToggle = document.getElementById('themeToggle');
        const currentTheme = readTheme();
        applyTheme(currentTheme);
        if (!themeToggle || themeToggle.dataset.themeBound === '1') return;

        themeToggle.dataset.themeBound = '1';
        themeToggle.addEventListener('change', function() {
          applyTheme(this.checked ? 'dark' : 'light');
        });
      }

      window.__applyTheme = applyTheme;
      window.__initThemeToggle = initThemeToggle;

      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initThemeToggle, { once: true });
      } else {
        initThemeToggle();
      }
    })();
    
    (function(){
      const ALLOWED_PATHS = new Set(['/', '/fetch', '/credentials', '/search', '/e3_check', '/terms', '/privacy']);
      let navInFlight = null;
      let pageCaptureTimer = null;

      // ── Navigation progress bar ──────────────────────────────────────
      const _navBar = (function() {
        const el = document.createElement('div');
        el.id = 'navProgressBar';
        document.body ? document.body.prepend(el) : document.addEventListener('DOMContentLoaded', function(){ document.body.prepend(el); }, { once: true });
        let _timer = null;
        let _pct = 0;

        function _setW(w, trans) {
          el.style.transition = trans || 'none';
          el.style.width = w + '%';
        }

        return {
          start: function() {
            clearInterval(_timer);
            _pct = 0;
            el.style.background = '';
            _setW(0, 'none');
            el.style.opacity = '1';
            _timer = setInterval(function() {
              _pct = _pct + (88 - _pct) * 0.12;
              _setW(_pct, 'width 0.35s ease');
            }, 200);
          },
          done: function() {
            clearInterval(_timer);
            _setW(100, 'width 0.18s ease');
            setTimeout(function() {
              el.style.transition = 'opacity 0.35s ease';
              el.style.opacity = '0';
              setTimeout(function() { _setW(0, 'none'); _pct = 0; }, 400);
            }, 200);
          },
          fail: function() {
            clearInterval(_timer);
            el.style.background = '#ef4444';
            _setW(100, 'width 0.15s ease');
            setTimeout(function() {
              el.style.transition = 'opacity 0.3s ease';
              el.style.opacity = '0';
              setTimeout(function() { el.style.background = ''; _setW(0, 'none'); _pct = 0; }, 350);
            }, 300);
          }
        };
      })();

      if (!window.__pageScopedListenerPatchInstalled) {
        window.__pageScopedListenerPatchInstalled = true;
        window.__capturePageScopedListeners = false;
        window.__pageScopedListenerRecords = [];

        const originalAddEventListener = EventTarget.prototype.addEventListener;
        EventTarget.prototype.addEventListener = function(type, listener, options) {
          if (window.__capturePageScopedListeners && listener) {
            try {
              window.__pageScopedListenerRecords.push({
                target: this,
                type: type,
                listener: listener,
                options: options
              });
            } catch (_) {}
          }
          return originalAddEventListener.call(this, type, listener, options);
        };
      }

      function startPageListenerCapture() {
        window.__capturePageScopedListeners = true;
        if (pageCaptureTimer) {
          clearTimeout(pageCaptureTimer);
          pageCaptureTimer = null;
        }
      }

      function stopPageListenerCaptureLater() {
        if (pageCaptureTimer) clearTimeout(pageCaptureTimer);
        pageCaptureTimer = setTimeout(function(){
          window.__capturePageScopedListeners = false;
        }, 1500);
      }

      function cleanupPageScopedListeners() {
        const records = Array.isArray(window.__pageScopedListenerRecords)
          ? window.__pageScopedListenerRecords
          : [];
        for (const rec of records) {
          try {
            if (rec && rec.target && rec.type && rec.listener) {
              rec.target.removeEventListener(rec.type, rec.listener, rec.options);
            }
          } catch (_) {}
        }
        window.__pageScopedListenerRecords = [];
        window.__capturePageScopedListeners = false;
        if (pageCaptureTimer) {
          clearTimeout(pageCaptureTimer);
          pageCaptureTimer = null;
        }
      }

      function normalizePath(pathname) {
        const cleaned = String(pathname || '').replace(/\/+$/, '');
        return cleaned || '/';
      }

      function resetSearchPageGlobals(pathname) {
        if (normalizePath(pathname) !== '/search') return;

        // Search page includes several scripts with one-time guards that are valid
        // for full reload. Keep receipt/autosubmit guards intact on partial swaps
        // to avoid duplicate listeners that cause stale second-receipt state.
        const keysToReset = [
          '__labelsPrimed'
        ];

        for (const k of keysToReset) {
          try { delete window[k]; } catch (_) { try { window[k] = undefined; } catch(_) {} }
        }
      }

      function resetE3CheckPageGlobals(pathname) {
        if (normalizePath(pathname) !== '/e3_check') return;
        // Reset one-time guard flags so e3_check.html re-registers its handlers on each partial nav
        const keysToReset = ['__e3CheckTabBindingsInstalled', '__e3CheckFlatpickrReadyPromise'];
        for (const k of keysToReset) {
          try { delete window[k]; } catch (_) { try { window[k] = undefined; } catch(_) {} }
        }
        // Ensure any stale wait overlay is hidden when re-entering the page
        try {
          const ov = document.getElementById('waitOverlay');
          if (ov) ov.setAttribute('aria-hidden', 'true');
        } catch(_) {}
      }

      function initSearchRepeatProfileHintSync(pathname) {
        if (normalizePath(pathname) !== '/search') return;

        const hint = document.getElementById('activeRepeatProfileHint');
        const repeatSw = document.getElementById('repeatEntrySwitch');
        if (!hint || !repeatSw) return;

        const receiptsSw = document.getElementById('useReceiptsSwitch');
        const profileSel = document.getElementById('charProfileSelect');

        if (!window.__LAST_REPEAT_PROFILE_BY_SCOPE) {
          window.__LAST_REPEAT_PROFILE_BY_SCOPE = {};
        }

        function currentModeKey() {
          try {
            if (receiptsSw) return receiptsSw.checked ? 'receipts' : 'invoices';
            return (String(localStorage.getItem('UI:useReceipts') || '0') === '1') ? 'receipts' : 'invoices';
          } catch (_) {
            return 'invoices';
          }
        }

        function currentVat() {
          try {
            if (typeof window.rcResolveActiveVat === 'function') {
              const liveVat = String(window.rcResolveActiveVat() || '').trim();
              if (liveVat) return liveVat;
            }
          } catch (_) {}
          try {
            const dsVat = String(document.body?.dataset?.activeVat || '').trim();
            if (dsVat) return dsVat;
          } catch (_) {}
          try {
            const modalVat = String(window._repeatModalVAT || '').trim();
            if (modalVat) return modalVat;
          } catch (_) {}
          try {
            const vat = String(window.APP_CONFIG.activeCredentialVat || '').trim();
            if (vat) return vat;
          } catch (_) {}
          return '';
        }

        function readRememberedProfileByMode() {
          try {
            const vat = currentVat();
            if (!vat) return '';
            const mode = currentModeKey();
            const key = 'repeat:selected_profile:' + mode + ':' + vat;
            return String(localStorage.getItem(key) || '').trim();
          } catch (_) {
            return '';
          }
        }

        function readCurrentProfileName() {
          const remembered = readRememberedProfileByMode();
          if (remembered) return remembered;
          try {
            const mode = currentModeKey();
            const vat = currentVat();
            const scopedKey = (vat || 'default') + ':' + mode;
            const cachedByScope = String((window.__LAST_REPEAT_PROFILE_BY_SCOPE || {})[scopedKey] || '').trim();
            if (cachedByScope) return cachedByScope;
          } catch (_) {}
          return '';
        }

        function renderHint() {
          const profileName = readCurrentProfileName();
          const mode = currentModeKey();
          const modeLabel = mode === 'receipts' ? 'Αποδείξεις' : 'Τιμολόγια';
          let allowReceiptsHint = true;
          if (mode === 'receipts') {
            try {
              if (typeof window.isReceiptModeAnalysisStrict === 'function') {
                allowReceiptsHint = !!window.isReceiptModeAnalysisStrict();
              } else {
                const bodyMode = String(document.body?.dataset?.receiptMode || '').toLowerCase();
                if (bodyMode) allowReceiptsHint = (bodyMode === 'analysis');
                else {
                  const storedMode = String(localStorage.getItem('rc:receiptMode') || '').toLowerCase();
                  allowReceiptsHint = (storedMode === 'analysis');
                }
              }
            } catch (_) {
              allowReceiptsHint = false;
            }
          }

          const shouldShow = !!(repeatSw && repeatSw.checked) && (mode !== 'receipts' || allowReceiptsHint);
          if (shouldShow) {
            hint.style.display = '';
            hint.textContent = 'Ενεργό προφίλ επαναληψιμης (' + modeLabel + '): ' + (profileName || 'Γενικό');
          } else {
            hint.style.display = 'none';
            hint.textContent = '';
          }
        }

        if (repeatSw.dataset.hintSyncBound !== '1') {
          repeatSw.dataset.hintSyncBound = '1';
          repeatSw.addEventListener('change', renderHint);
        }
        if (receiptsSw && receiptsSw.dataset.hintSyncBound !== '1') {
          receiptsSw.dataset.hintSyncBound = '1';
          receiptsSw.addEventListener('change', renderHint);
        }
        if (profileSel && profileSel.dataset.hintSyncBound !== '1') {
          profileSel.dataset.hintSyncBound = '1';
          profileSel.addEventListener('change', function() {
            try {
              const opt = profileSel.selectedOptions ? profileSel.selectedOptions[0] : null;
              const raw = (opt && (opt.dataset.profileName || opt.textContent || opt.value)) || '';
              const mode = currentModeKey();
              const vat = currentVat();
              const scopedKey = (vat || 'default') + ':' + mode;
              window.__LAST_REPEAT_PROFILE_BY_SCOPE[scopedKey] = String(raw || '').trim();
            } catch (_) {}
            renderHint();
          });
        }

        window.__searchRepeatHintRender = renderHint;

        if (!window.__searchHintModeButtonsBound) {
          window.__searchHintModeButtonsBound = true;
          document.addEventListener('click', function(ev) {
            const modeBtn = ev.target && ev.target.closest ? ev.target.closest('[data-receipt-mode]') : null;
            if (!modeBtn) return;
            setTimeout(function(){
              try {
                if (typeof window.__searchRepeatHintRender === 'function') {
                  window.__searchRepeatHintRender();
                }
              } catch (_) {}
            }, 0);
          }, true);

          window.addEventListener('useReceiptsChange', function(){
            try {
              if (typeof window.__searchRepeatHintRender === 'function') {
                window.__searchRepeatHintRender();
              }
            } catch (_) {}
          });

          window.addEventListener('repeatStateChange', function(){
            try {
              if (typeof window.__searchRepeatHintRender === 'function') {
                window.__searchRepeatHintRender();
              }
            } catch (_) {}
          });
        }

        renderHint();
        setTimeout(renderHint, 120);
      }

      function initSearchReceiptsOutlineSync(pathname) {
        if (normalizePath(pathname) !== '/search') return;

        const receiptsSw = document.getElementById('useReceiptsSwitch');
        if (!receiptsSw) return;

        function isReceiptsMode() {
          try {
            if (receiptsSw) return !!receiptsSw.checked;
          } catch (_) {}
          try {
            return String(localStorage.getItem('UI:useReceipts') || '0') === '1';
          } catch (_) {
            return false;
          }
        }

        function setOutlineStyle(el, on) {
          if (!el) return;
          if (on) {
            // Use !important to win against late style writes from page scripts.
            el.style.setProperty('outline', '2px solid orange', 'important');
            el.style.setProperty('outline-offset', '0px', 'important');
          } else {
            el.style.removeProperty('outline');
            el.style.removeProperty('outline-offset');
          }
        }

        function applyOutline() {
          const on = isReceiptsMode();
          const urlInput = document.getElementById('scrapeUrlInput');
          const markInput = document.getElementById('markInput');

          // In receipts mode, highlight URL input if present; fallback to mark input
          // during the short window before scrapeUrlInput is injected.
          if (on) {
            if (urlInput) {
              setOutlineStyle(urlInput, true);
              setOutlineStyle(markInput, false);
            } else {
              setOutlineStyle(markInput, true);
            }
            return;
          }

          // In invoices mode clear any residual highlights.
          setOutlineStyle(urlInput, false);
          setOutlineStyle(markInput, false);
        }

        if (receiptsSw.dataset.outlineSyncBound !== '1') {
          receiptsSw.dataset.outlineSyncBound = '1';
          receiptsSw.addEventListener('change', function() {
            setTimeout(applyOutline, 0);
            setTimeout(applyOutline, 60);
            setTimeout(applyOutline, 180);
          });
        }

        if (!window.__searchOutlineUseReceiptsBound) {
          window.__searchOutlineUseReceiptsBound = true;
          window.addEventListener('useReceiptsChange', function() {
            setTimeout(applyOutline, 0);
            setTimeout(applyOutline, 60);
          });
        }

        // scrapeUrlInput may be created later by receipts scripts.
        if (!window.__searchOutlineObserver) {
          try {
            window.__searchOutlineObserver = new MutationObserver(function(){ applyOutline(); });
            window.__searchOutlineObserver.observe(document.body, { childList: true, subtree: true });
          } catch (_) {}
        }

        applyOutline();
        requestAnimationFrame(applyOutline);
        setTimeout(applyOutline, 120);
        setTimeout(applyOutline, 300);
      }

      function initSearchRepeatSwitchStateSync(pathname) {
        if (normalizePath(pathname) !== '/search') return;

        const repeatSw = document.getElementById('repeatEntrySwitch');
        if (!repeatSw) return;

        function readStored() {
          try {
            const v = localStorage.getItem('REPEAT:enabled');
            if (v === '1' || v === 'true') return true;
            if (v === '0' || v === 'false') return false;
          } catch (_) {}
          return null;
        }

        function persistCurrent() {
          try {
            localStorage.setItem('REPEAT:enabled', repeatSw.checked ? '1' : '0');
          } catch (_) {}
        }

        function applyStoredState() {
          const stored = readStored();
          if (stored === null) {
            // First visit fallback: seed storage from current DOM/server state.
            persistCurrent();
            return;
          }

          if (!!repeatSw.checked !== !!stored) {
            repeatSw.checked = !!stored;
            try {
              repeatSw.dispatchEvent(new Event('change', { bubbles: true }));
            } catch (_) {}
          }
        }

        if (repeatSw.dataset.repeatPersistBound !== '1') {
          repeatSw.dataset.repeatPersistBound = '1';
          repeatSw.addEventListener('change', persistCurrent);
        }

        applyStoredState();
        setTimeout(applyStoredState, 80);
      }

      function initFetchCredentialLockFallback(pathname) {
        if (normalizePath(pathname) !== '/fetch') return;

        const select = document.getElementById('use_credential');
        const vatInput = document.getElementById('vat_input');
        if (!select) return;

        function lockUiIfActiveCredential() {
          const hasActive = !!String(select.value || '').trim();
          if (!hasActive) return;

          select.disabled = true;
          select.setAttribute('title', 'Κλειδωμένο στον ενεργό πελάτη');
          select.style.cursor = 'not-allowed';
          select.style.opacity = '0.7';

          if (vatInput) {
            vatInput.disabled = true;
            vatInput.setAttribute('title', 'Κλειδωμένο στον ενεργό πελάτη');
            vatInput.style.cursor = 'not-allowed';
            vatInput.style.opacity = '0.7';
          }
        }

        lockUiIfActiveCredential();
        setTimeout(lockUiIfActiveCredential, 80);
        setTimeout(lockUiIfActiveCredential, 240);
      }

      function initFetchDateInputsFallback(pathname) {
        if (normalizePath(pathname) !== '/fetch') return;

        const fetchDateInputs = ['date_from', 'date_to', 'bulk_date_from', 'bulk_date_to']
          .map(function(id) { return document.getElementById(id); })
          .filter(Boolean);
        if (!fetchDateInputs.length) return;

        function formatDateInputWithSlashes(raw) {
          const digits = String(raw || '').replace(/\D/g, '').slice(0, 8);
          const dd = digits.slice(0, 2);
          const mm = digits.slice(2, 4);
          const yyyy = digits.slice(4, 8);
          if (digits.length <= 2) return dd;
          if (digits.length <= 4) return dd + '/' + mm;
          return dd + '/' + mm + '/' + yyyy;
        }

        function bindManualDateMask(input) {
          if (!input || input.dataset.fetchMaskBound === '1') return;
          input.dataset.fetchMaskBound = '1';
          input.addEventListener('input', function() {
            const next = formatDateInputWithSlashes(input.value);
            if (input.value !== next) input.value = next;
          });
        }

        fetchDateInputs.forEach(bindManualDateMask);

        function loadScriptOnce(src) {
          return new Promise((resolve, reject) => {
            const existing = document.querySelector('script[src="' + src + '"]');
            if (existing) {
              resolve();
              return;
            }
            const s = document.createElement('script');
            s.src = src;
            s.async = false;
            s.onload = () => resolve();
            s.onerror = () => reject(new Error('Failed loading ' + src));
            document.head.appendChild(s);
          });
        }

        function ensureFlatpickrCss() {
          const href = 'https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css';
          const existing = document.querySelector('link[rel="stylesheet"][href="' + href + '"]');
          if (existing) return;
          const link = document.createElement('link');
          link.rel = 'stylesheet';
          link.href = href;
          document.head.appendChild(link);
        }

        async function ensureFlatpickrReady() {
          if (typeof window.flatpickr === 'function') return;
          if (!window.__fetchFallbackFlatpickrReady) {
            window.__fetchFallbackFlatpickrReady = (async function() {
              ensureFlatpickrCss();
              await loadScriptOnce('https://cdn.jsdelivr.net/npm/flatpickr');
              await loadScriptOnce('https://cdn.jsdelivr.net/npm/flatpickr/dist/l10n/el.js');
            })();
          }
          try {
            await window.__fetchFallbackFlatpickrReady;
          } catch (e) {
            console.warn('Fetch fallback could not load flatpickr assets', e);
          }
        }

        function initFlatpickr() {
          if (typeof window.flatpickr !== 'function') return;
          try {
            fetchDateInputs.forEach(function(input) {
              if (input && input._flatpickr) input._flatpickr.destroy();
            });
          } catch (_) {}

          try {
            const _useEl = !!(window.flatpickr && window.flatpickr.l10ns && window.flatpickr.l10ns.el);
            fetchDateInputs.forEach(function(input) {
              window.flatpickr(input, Object.assign({ dateFormat: 'd/m/Y', allowInput: true }, _useEl ? { locale: 'el' } : {}));
            });
          } catch (e) {
            console.warn('Fetch fallback flatpickr init failed', e);
          }
        }

        ensureFlatpickrReady().then(initFlatpickr);
        setTimeout(initFlatpickr, 80);
        setTimeout(initFlatpickr, 240);
      }

      function initE3CheckDateInputsFallback(pathname) {
        if (normalizePath(pathname) !== '/e3_check') return;

        const e3DateInputs = ['e3DateFrom', 'e3DateTo', 'brainDateFrom', 'brainDateTo']
          .map(function(id) { return document.getElementById(id); })
          .filter(Boolean);
        if (!e3DateInputs.length) return;

        function formatDateInputWithSlashes(raw) {
          const digits = String(raw || '').replace(/\D/g, '').slice(0, 8);
          const dd = digits.slice(0, 2);
          const mm = digits.slice(2, 4);
          const yyyy = digits.slice(4, 8);
          if (digits.length <= 2) return dd;
          if (digits.length <= 4) return dd + '/' + mm;
          return dd + '/' + mm + '/' + yyyy;
        }

        function bindManualDateMask(input) {
          if (!input || input.dataset.e3MaskBound === '1') return;
          input.dataset.e3MaskBound = '1';
          input.addEventListener('input', function() {
            const next = formatDateInputWithSlashes(input.value);
            if (input.value !== next) input.value = next;
          });
        }

        e3DateInputs.forEach(bindManualDateMask);

        function loadScriptOnce(src) {
          return new Promise((resolve, reject) => {
            const existing = document.querySelector('script[src="' + src + '"]');
            if (existing) {
              if (existing.dataset.loaded === '1') {
                resolve();
                return;
              }
              existing.addEventListener('load', () => resolve(), { once: true });
              existing.addEventListener('error', () => reject(new Error('Failed loading ' + src)), { once: true });
              return;
            }
            const s = document.createElement('script');
            s.src = src;
            s.async = false;
            s.onload = () => {
              s.dataset.loaded = '1';
              resolve();
            };
            s.onerror = () => reject(new Error('Failed loading ' + src));
            document.head.appendChild(s);
          });
        }

        function ensureFlatpickrCss() {
          const href = 'https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css';
          const existing = document.querySelector('link[rel="stylesheet"][href="' + href + '"]');
          if (existing) return;
          const link = document.createElement('link');
          link.rel = 'stylesheet';
          link.href = href;
          document.head.appendChild(link);
        }

        async function ensureFlatpickrReady() {
          if (typeof window.flatpickr === 'function') return;
          if (!window.__e3FallbackFlatpickrReady) {
            window.__e3FallbackFlatpickrReady = (async function() {
              ensureFlatpickrCss();
              await loadScriptOnce('https://cdn.jsdelivr.net/npm/flatpickr');
              await loadScriptOnce('https://cdn.jsdelivr.net/npm/flatpickr/dist/l10n/el.js');
            })();
          }
          try {
            await window.__e3FallbackFlatpickrReady;
          } catch (e) {
            console.warn('E3 fallback could not load flatpickr assets', e);
          }
        }

        function initFlatpickr() {
          if (typeof window.flatpickr !== 'function') return;
          try {
            e3DateInputs.forEach(function(input) {
              if (input && input._flatpickr) input._flatpickr.destroy();
            });
          } catch (_) {}

          try {
            const _useEl = !!(window.flatpickr && window.flatpickr.l10ns && window.flatpickr.l10ns.el);
            e3DateInputs.forEach(function(input) {
              window.flatpickr(input, Object.assign({ dateFormat: 'd/m/Y', allowInput: true }, _useEl ? { locale: 'el' } : {}));
            });
          } catch (e) {
            console.warn('E3 fallback flatpickr init failed', e);
          }
        }

        ensureFlatpickrReady().then(initFlatpickr);
        setTimeout(initFlatpickr, 80);
        setTimeout(initFlatpickr, 240);
      }

      function initFetchBulkControlsFallback(pathname) {
        if (normalizePath(pathname) !== '/fetch') return;

        const searchInput = document.getElementById('bulkCustomerSearch');
        const list = document.getElementById('bulkFetchCustomerList');
        const selectAllBtn = document.getElementById('bulkSelectAllBtn');
        const deselectAllBtn = document.getElementById('bulkDeselectAllBtn');
        if (!list) return;

        function applyFilter() {
          if (!searchInput) return;
          const q = String(searchInput.value || '').trim().toLowerCase();
          const rows = Array.from(list.querySelectorAll('.bulk-customer-row'));
          rows.forEach(function(row) {
            const txt = String((row.dataset && row.dataset.bulkSearch) || row.textContent || '').toLowerCase();
            row.style.display = (!q || txt.includes(q)) ? '' : 'none';
          });
        }

        function getVisibleBoxes() {
          const rows = Array.from(list.querySelectorAll('.bulk-customer-row'));
          const visibleRows = rows.filter(function(row) { return row.style.display !== 'none'; });
          const sourceRows = visibleRows.length ? visibleRows : rows;
          return sourceRows
            .map(function(row) { return row.querySelector('.bulkCustomerCheckbox'); })
            .filter(Boolean);
        }

        if (searchInput) {
          searchInput.oninput = applyFilter;
          searchInput.onkeyup = applyFilter;
          searchInput.onsearch = applyFilter;
        }
        if (selectAllBtn) {
          selectAllBtn.onclick = function() {
            getVisibleBoxes().forEach(function(cb) { cb.checked = true; });
          };
        }
        if (deselectAllBtn) {
          deselectAllBtn.onclick = function() {
            getVisibleBoxes().forEach(function(cb) { cb.checked = false; });
          };
        }

        applyFilter();
        setTimeout(applyFilter, 80);
        setTimeout(applyFilter, 240);
      }

      function initFetchModeTabsFallback(pathname) {
        if (normalizePath(pathname) !== '/fetch') return;

        const singleTabBtn = document.getElementById('singleFetchTabBtn');
        const bulkTabBtn = document.getElementById('bulkFetchTabBtn');
        const singlePanel = document.getElementById('singleFetchPanel');
        const bulkPanel = document.getElementById('bulkFetchPanel');
        if (!singleTabBtn || !bulkTabBtn || !singlePanel || !bulkPanel) return;

        function applyMode(mode) {
          const next = String(mode || 'single').toLowerCase() === 'bulk' ? 'bulk' : 'single';
          singlePanel.classList.toggle('hidden', next !== 'single');
          bulkPanel.classList.toggle('hidden', next !== 'bulk');
          singleTabBtn.classList.toggle('is-active', next === 'single');
          bulkTabBtn.classList.toggle('is-active', next === 'bulk');
          singleTabBtn.setAttribute('aria-selected', next === 'single' ? 'true' : 'false');
          bulkTabBtn.setAttribute('aria-selected', next === 'bulk' ? 'true' : 'false');
          try { localStorage.setItem('scanmydata.fetchAdminTab', next); } catch (_) {}
        }

        singleTabBtn.onclick = function() { applyMode('single'); };
        bulkTabBtn.onclick = function() { applyMode('bulk'); };

        let stored = 'single';
        try {
          stored = String(localStorage.getItem('scanmydata.fetchAdminTab') || 'single').trim().toLowerCase();
        } catch (_) {}

        applyMode(stored);
        setTimeout(function() { applyMode(stored); }, 80);
        setTimeout(function() { applyMode(stored); }, 240);
      }

      function initFetchBulkActionsFallback(pathname) {
        if (normalizePath(pathname) !== '/fetch') {
          try {
            if (window.__fetchBulkFallbackTimer) clearInterval(window.__fetchBulkFallbackTimer);
            window.__fetchBulkFallbackTimer = null;
          } catch (_) {}
          return;
        }

        const startBtn = document.getElementById('bulkFetchStartBtn');
        const stopBtn = document.getElementById('bulkFetchStopBtn');
        const statusEl = document.getElementById('bulkFetchStatus');
        const fromInput = document.getElementById('bulk_date_from');
        const toInput = document.getElementById('bulk_date_to');
        const list = document.getElementById('bulkFetchCustomerList');
        const JOB_KEY = 'scanmydata.bulkFetchJobId';
        if (!startBtn || !stopBtn || !statusEl || !fromInput || !toInput || !list) return;

        function saveJobId(jobId) {
          try {
            const normalized = String(jobId || '').trim();
            if (normalized) localStorage.setItem(JOB_KEY, normalized);
            else localStorage.removeItem(JOB_KEY);
          } catch (_) {}
        }

        function loadJobId() {
          try {
            return String(localStorage.getItem(JOB_KEY) || '').trim();
          } catch (_) {
            return '';
          }
        }

        function setStatus(msg) {
          statusEl.textContent = String(msg || '');
        }

        function setButtons(state) {
          const s = String(state || 'idle').toLowerCase();
          const runningLike = (s === 'running' || s === 'stopping');
          startBtn.disabled = runningLike;
          stopBtn.disabled = !runningLike;
          startBtn.textContent = runningLike ? (s === 'stopping' ? 'Γίνεται Διακοπή...' : 'Σε Εξέλιξη...') : 'Εκκίνηση Μαζικής Λήψης';
        }

        function flash(msg, type, options) {
          const opts = options || {};
          try {
            if (typeof window.__fetchUpsertProgressFlash === 'function') {
              window.__fetchUpsertProgressFlash(String(msg || ''), Number(opts.percent || 1), type || 'info', {
                mode: 'bulk',
                canStop: !!opts.canStop,
                state: String(opts.state || '')
              });
              return;
            }
            if (typeof window.__setGlobalBulkFetchSnapshot === 'function') {
              const fallbackStatus = String(
                opts.state || (
                  type === 'error' ? 'error' : (
                    type === 'success' ? 'completed' : 'running'
                  )
                )
              );
              window.__setGlobalBulkFetchSnapshot({
                status: fallbackStatus,
                percent: Number(opts.percent || 1),
                message: String(msg || ''),
                mode: 'bulk'
              });
              return;
            }
          } catch (_) {}
        }

        function getSelectedNames() {
          return Array.from(list.querySelectorAll('.bulkCustomerCheckbox:checked'))
            .map(function(cb) { return String(cb.value || '').trim(); })
            .filter(Boolean);
        }

        async function pollOnce() {
          const jobId = loadJobId();
          if (!jobId) {
            setButtons('idle');
            return { status: 'not_started' };
          }

          const res = await fetch('/api/fetch_bulk/progress?job_id=' + encodeURIComponent(jobId), {
            credentials: 'same-origin'
          });
          const data = await res.json().catch(function() { return {}; });
          if (!res.ok || !data || data.ok !== true) {
            saveJobId('');
            setButtons('idle');
            throw new Error((data && (data.error || data.message)) ? (data.error || data.message) : 'Αποτυχία ανάγνωσης προόδου μαζικής λήψης.');
          }

          const status = String(data.status || 'not_started');
          const message = String(data.message || '');
          const percent = Number(data.percent || 0);
          setButtons(status);
          if (message) setStatus(message);

          if (status === 'running' || status === 'stopping') {
            flash(message || 'Η μαζική λήψη είναι σε εξέλιξη.', 'info', { canStop: true, state: status, percent: percent });
          } else if (status === 'completed') {
            flash(message || 'Η μαζική λήψη ολοκληρώθηκε.', 'success', { percent: 100, state: 'completed' });
          } else if (status === 'stopped') {
            flash(message || 'Η μαζική λήψη διακόπηκε.', 'success', { percent: percent || 1, state: 'stopped' });
          } else if (status === 'error') {
            flash(message || 'Σφάλμα στη μαζική λήψη.', 'error', { percent: percent || 1 });
          }

          if (status !== 'running' && status !== 'stopping') {
            saveJobId('');
          }
          return data;
        }

        function startPolling() {
          try {
            if (window.__fetchBulkFallbackTimer) {
              clearInterval(window.__fetchBulkFallbackTimer);
            }
          } catch (_) {}

          const tick = function() {
            pollOnce().then(function(state) {
              const status = String((state && state.status) || 'not_started');
              if (status !== 'running' && status !== 'stopping') {
                try {
                  if (window.__fetchBulkFallbackTimer) clearInterval(window.__fetchBulkFallbackTimer);
                } catch (_) {}
              }
            }).catch(function(err) {
              setStatus(String((err && err.message) || 'Σφάλμα polling μαζικής λήψης.'));
              setButtons('idle');
            });
          };

          window.__fetchBulkFallbackTimer = setInterval(tick, 1500);
          tick();
        }

        startBtn.onclick = async function() {
          const dateFrom = String(fromInput.value || '').trim();
          const dateTo = String(toInput.value || '').trim();
          const selectedNames = getSelectedNames();

          if (!dateFrom || !dateTo) {
            const msg = 'Συμπλήρωσε τις ημερομηνίες της μαζικής λήψης (Από/Έως) πριν την εκκίνηση.';
            setStatus(msg);
            flash(msg, 'error', { percent: 1 });
            return;
          }
          if (!selectedNames.length) {
            const msg = 'Επίλεξε τουλάχιστον έναν πελάτη.';
            setStatus(msg);
            flash(msg, 'error', { percent: 1 });
            return;
          }

          setButtons('running');
          setStatus('Ξεκινά η μαζική λήψη...');

          try {
            const res = await fetch('/api/fetch_bulk/start', {
              method: 'POST',
              credentials: 'same-origin',
              headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json'
              },
              body: JSON.stringify({
                date_from: dateFrom,
                date_to: dateTo,
                all_customers: false,
                credential_names: selectedNames,
              })
            });
            const data = await res.json().catch(function() { return {}; });
            if (!res.ok || !data || data.ok !== true) {
              throw new Error((data && (data.error || data.message)) ? (data.error || data.message) : 'Αποτυχία εκκίνησης μαζικής λήψης.');
            }
            saveJobId(String(data.job_id || '').trim());
            setStatus(String(data.message || 'Η μαζική λήψη ξεκίνησε.'));
            flash(String(data.message || 'Η μαζική λήψη ξεκίνησε.'), 'info', { canStop: true, state: 'running', percent: 1 });
            startPolling();
          } catch (err) {
            saveJobId('');
            setButtons('idle');
            const msg = String((err && err.message) || 'Αποτυχία εκκίνησης μαζικής λήψης.');
            setStatus(msg);
            flash(msg, 'error', { percent: 1 });
          }
        };

        stopBtn.onclick = async function() {
          const jobId = loadJobId();
          if (!jobId) {
            const msg = 'Δεν βρέθηκε ενεργή μαζική λήψη για διακοπή.';
            setStatus(msg);
            flash(msg, 'error', { percent: 1 });
            setButtons('idle');
            return;
          }

          stopBtn.disabled = true;
          try {
            const res = await fetch('/api/fetch_bulk/stop', {
              method: 'POST',
              credentials: 'same-origin',
              headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json'
              },
              body: JSON.stringify({ job_id: jobId })
            });
            const data = await res.json().catch(function() { return {}; });
            if (!res.ok || !data || data.ok !== true) {
              throw new Error((data && (data.error || data.message)) ? (data.error || data.message) : 'Αποτυχία αίτησης διακοπής.');
            }
            setButtons('stopping');
            setStatus(String(data.message || 'Η διακοπή ζητήθηκε.'));
            flash(String(data.message || 'Η διακοπή ζητήθηκε.'), 'info', { canStop: true, state: 'stopping', percent: 1 });
            startPolling();
          } catch (err) {
            const msg = String((err && err.message) || 'Αποτυχία αίτησης διακοπής.');
            setStatus(msg);
            flash(msg, 'error', { percent: 1 });
            pollOnce().catch(function() {});
          }
        };

        if (loadJobId()) {
          startPolling();
        } else {
          setButtons('idle');
        }
      }

      function cleanupTransientDatepickers() {
        try {
          if (window.__fetchBulkFallbackTimer) clearInterval(window.__fetchBulkFallbackTimer);
          window.__fetchBulkFallbackTimer = null;
        } catch (_) {}
        try {
          document.querySelectorAll('input').forEach(function(input) {
            try {
              if (input && input._flatpickr && typeof input._flatpickr.destroy === 'function') {
                input._flatpickr.destroy();
              }
            } catch (_) {}
          });
        } catch (_) {}

        try {
          document.querySelectorAll('.flatpickr-calendar').forEach(function(node) {
            try { node.remove(); } catch (_) {}
          });
        } catch (_) {}
      }

      function isAllowedUrl(url) {
        if (!url || url.origin !== window.location.origin) return false;
        return ALLOWED_PATHS.has(normalizePath(url.pathname));
      }

      function shouldHandleLink(anchor, event) {
        if (!anchor) return false;
        const isPrimaryNavLink = !!anchor.closest('.layout-header__nav, #sideMenu, .layout-header__active-customer, .layout-header__active-group');
        if (event.defaultPrevented && !isPrimaryNavLink) return false;
        if (event.button !== 0) return false;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
        if (anchor.target && anchor.target !== '_self') return false;
        if (anchor.hasAttribute('download')) return false;
        if (anchor.getAttribute('rel') === 'external') return false;

        const rawHref = anchor.getAttribute('href') || '';
        if (!rawHref || rawHref.startsWith('#') || rawHref.startsWith('mailto:') || rawHref.startsWith('tel:') || rawHref.startsWith('javascript:')) return false;

        let url;
        try {
          url = new URL(rawHref, window.location.href);
        } catch (_) {
          return false;
        }

        if (!isAllowedUrl(url)) return false;
        return true;
      }

      async function runPageScripts(fromShell, fromScriptsHost, targetScriptsHost) {
        if (targetScriptsHost) targetScriptsHost.innerHTML = '';

        // Intercept document.addEventListener('DOMContentLoaded', ...) calls.
        // In partial navigation DOMContentLoaded never fires again, so we queue
        // these callbacks and fire them after all page scripts execute.
        const _dclQueue = [];
        const _origDocAEL = document.addEventListener.bind(document);
        document.addEventListener = function(type, listener, options) {
          if (type === 'DOMContentLoaded' && listener) {
            if (typeof listener === 'function') {
              _dclQueue.push(listener);
              return;
            }
            if (typeof listener.handleEvent === 'function') {
              _dclQueue.push(function(evt) { listener.handleEvent(evt); });
              return;
            }
          }
          return _origDocAEL(type, listener, options);
        };

        async function executeScripts(scripts, mode) {
          try { if (typeof cleanupStraySvgTextNodes === 'function') cleanupStraySvgTextNodes(); } catch (_) {}
          for (const oldScript of scripts) {
            await new Promise((resolve, reject) => {
              const script = document.createElement('script');
              for (const attr of oldScript.attributes) {
                script.setAttribute(attr.name, attr.value);
              }

              if (oldScript.src) {
                script.onload = () => resolve();
                script.onerror = () => reject(new Error('Failed loading script: ' + oldScript.src));
              } else {
                try {
                  const raw = String(oldScript.textContent || '');
                  // Partial-nav script transform. We do TWO things to the
                  // script body so it can be re-executed safely after the
                  // shell DOM was replaced:
                  //
                  //  1. Convert top-level `let`/`const` declarations to
                  //     `var` so re-execution doesn't raise the
                  //     "Identifier '…' has already been declared"
                  //     SyntaxError that kills the whole script (and
                  //     leaves the page half-bound — symptom: tabs,
                  //     dropdowns and credential-edit form on
                  //     e3_check.html stop responding after a partial
                  //     reload).
                  //
                  //  2. NOT strip "stray HTML lines" anymore. The
                  //     previous heuristic tried to detect when we were
                  //     inside a template literal so it could spare the
                  //     HTML lines that legitimately live inside
                  //     backticks (e.g. ``tr.innerHTML = ` <td>…</td>
                  //     `;``). That heuristic mis-handled regex
                  //     literals inside `${ … }` interpolations — e.g.
                  //     ``${String(val).replace(/"/g,'&quot;')}`` — and
                  //     once it saw the `"` inside `/"/g` it flipped
                  //     into a "double-quoted string" state that never
                  //     closed, so every subsequent template-literal
                  //     HTML line got stripped. The result was 100+
                  //     template-literal HTML lines being deleted from
                  //     e3_check.html on partial nav, breaking
                  //     credential rendering, branch inputs, etc. Any
                  //     truly stray HTML in a `<script>` block was
                  //     always a source-page bug and should be fixed
                  //     there.
                  const lines = raw.split('\n');
                  const out = [];
                  for (const line of lines) {
                    out.push(line.replace(/^(\s*)(let|const)(\s+)/, '$1var$3'));
                  }
                  const content = out.join('\n');

                  script.textContent = content;
                } catch (e) {
                  console.warn('[partial-nav] script sanitization failed, falling back to raw content', e);
                  try { script.textContent = String(oldScript.textContent || ''); } catch (_) { script.textContent = ''; }
                }
              }

              // Try to replace; if that fails, append as fallback.
              try {
                if (mode === 'replace' && oldScript.parentNode) {
                  oldScript.parentNode.replaceChild(script, oldScript);
                } else if (targetScriptsHost) {
                  targetScriptsHost.appendChild(script);
                } else {
                  resolve();
                  return;
                }
              } catch (err) {
                console.warn('[partial-nav] replaceChild failed, appending script as fallback', err);
                try {
                  (document.head || document.documentElement).appendChild(script);
                } catch (err2) {
                  console.error('[partial-nav] fallback append also failed', err2);
                }
              }

              if (!oldScript.src) resolve();
            });
          }
        }

        function clonePageAssets(host) {
          if (!host || !targetScriptsHost) return;
          const assets = Array.from(host.querySelectorAll('style, link[rel="stylesheet"]'));
          for (const oldEl of assets) {
            try {
              targetScriptsHost.appendChild(oldEl.cloneNode(true));
            } catch (_) {}
          }
        }

        try {
          if (fromScriptsHost) {
            // Include page-scoped CSS from scripts host (search page keeps toggle CSS there).
            clonePageAssets(fromScriptsHost);
          }
          if (fromShell) {
            const shellScripts = Array.from(fromShell.querySelectorAll('script'));
            await executeScripts(shellScripts, 'replace');
          }
          if (fromScriptsHost) {
            const hostScripts = Array.from(fromScriptsHost.querySelectorAll('script'));
            await executeScripts(hostScripts, 'append');
          }
        } finally {
          document.addEventListener = _origDocAEL;
        }

        if (_dclQueue.length) {
          const dclEvent = new Event('DOMContentLoaded');
          for (const fn of _dclQueue) {
            try { fn(dclEvent); } catch(e) { console.warn('[partial-nav] DCL callback error', e); }
          }
        }
      }

      async function swapPage(url, options) {
        options = options || {};
        if (navInFlight) navInFlight.abort();
        navInFlight = new AbortController();

        const shell = document.getElementById('appShell');
        const scriptsHost = document.getElementById('appPageScripts');
        if (!shell || !scriptsHost) {
          window.location.href = url.toString();
          return;
        }

        // Start visual feedback immediately on click
        _navBar.start();
        shell.classList.add('page-loading');

        let response;
        try {
          response = await fetch(url.toString(), {
            credentials: 'same-origin',
            headers: {
              'X-Requested-With': 'partial-nav',
              'X-Partial-Nav': '1'
            },
            signal: navInFlight.signal
          });
        } catch(fetchErr) {
          _navBar.fail();
          shell.classList.remove('page-loading');
          if (fetchErr && fetchErr.name === 'AbortError') return;
          window.location.href = url.toString();
          return;
        }

        if (!response.ok) {
          _navBar.fail();
          shell.classList.remove('page-loading');
          window.location.href = url.toString();
          return;
        }

        const html = await response.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const nextShell = doc.getElementById('appShell');
        const nextScripts = doc.getElementById('appPageScripts');
        if (!nextShell) {
          _navBar.fail();
          shell.classList.remove('page-loading');
          window.location.href = url.toString();
          return;
        }

        try {
          const nextActiveVat = String(doc.body?.dataset?.activeVat || '').trim();
          if (nextActiveVat && document.body && document.body.dataset) {
            document.body.dataset.activeVat = nextActiveVat;
          } else if (nextActiveVat === '' && document.body && document.body.dataset && 'activeVat' in document.body.dataset) {
            delete document.body.dataset.activeVat;
          }
        } catch (_) {}

        resetSearchPageGlobals(url.pathname);
        resetE3CheckPageGlobals(url.pathname);
        cleanupTransientDatepickers();

        try {
          if (typeof window.__cleanupPageScripts === 'function') {
            window.__cleanupPageScripts();
          }
        } catch (e) {
          console.warn('page cleanup failed', e);
        }
        cleanupPageScopedListeners();
        window.__cleanupPageScripts = null;

        // Swap content and trigger enter animation
        shell.classList.remove('page-loading');
        shell.innerHTML = nextShell.innerHTML;
        document.title = doc.title || document.title;

        // Fade-in new content
        shell.classList.add('page-entering');
        shell.addEventListener('animationend', function _onEnd() {
          shell.classList.remove('page-entering');
          shell.removeEventListener('animationend', _onEnd);
        }, { once: true });

        if (!options.replaceState) {
          history.pushState({ partialNav: true }, '', url.toString());
        }

        try {
          if (typeof window.__applyTheme === 'function') {
            const currentTheme = (function(){ try { return localStorage.getItem('theme') || 'light'; } catch(_) { return 'light'; } })();
            window.__applyTheme(currentTheme);
          }
          if (typeof window.__initThemeToggle === 'function') window.__initThemeToggle();
          if (typeof window.__initBaseShell === 'function') window.__initBaseShell();
          if (typeof window.__initFiscalYearUi === 'function') window.__initFiscalYearUi();
        } catch (e) {
          console.warn('base shell init failed', e);
        }

        startPageListenerCapture();
        try {
          await runPageScripts(shell, nextScripts, scriptsHost);
        } catch (e) {
          console.warn('page scripts execution failed after partial swap', e);
        } finally {
          stopPageListenerCaptureLater();
        }

        initSearchRepeatProfileHintSync(url.pathname);
        initFetchCredentialLockFallback(url.pathname);
        initFetchDateInputsFallback(url.pathname);
        initE3CheckDateInputsFallback(url.pathname);
        initFetchBulkControlsFallback(url.pathname);
        initFetchModeTabsFallback(url.pathname);
        initFetchBulkActionsFallback(url.pathname);
        initSearchReceiptsOutlineSync(url.pathname);
        initSearchRepeatSwitchStateSync(url.pathname);

        // Extra: if we've just swapped to the E3 check page, refresh brain clients
        try {
          const p = normalizePath(url.pathname);
          if (p === '/e3_check') {
            if (typeof fetchE3BrainActiveClients === 'function') {
              fetchE3BrainActiveClients().then(()=>{
                try { if (typeof renderBrainActiveClientSelect === 'function') renderBrainActiveClientSelect(); } catch(_) {}
                try {
                  const sel = document.getElementById('brainActiveClientSelect');
                  if (sel && sel.value) sel.dispatchEvent(new Event('change', { bubbles: true }));
                } catch(_) {}
              }).catch(()=>{});
            }
            if (typeof fetchE3BrainCredentials === 'function') fetchE3BrainCredentials().catch(()=>{});
            if (typeof renderBrainActiveClientSelect === 'function') renderBrainActiveClientSelect();
            if (typeof syncTopCredentialToBrain === 'function') syncTopCredentialToBrain();
            if (typeof rebindE3Controls === 'function') rebindE3Controls();
            if (typeof ensureE3DatePickerReady === 'function') ensureE3DatePickerReady();
            setTimeout(function() {
              try { if (typeof ensureE3DatePickerReady === 'function') ensureE3DatePickerReady(); } catch (_) {}
            }, 120);
            // Restore the saved outer tab (Αναλυτικός / Γρήγορος Έλεγχος).
            // Done here (after script execution + rebinds) to survive any earlier resets.
            try {
              const _savedTab = sessionStorage.getItem('e3ActiveTab');
              if ((_savedTab === 'analyticTab' || _savedTab === 'quickCheckTab') && typeof showE3PageTab === 'function') {
                showE3PageTab(_savedTab);
              }
            } catch (_) {}
          }
        } catch (_) {}

        _navBar.done();

        try {
          window.dispatchEvent(new CustomEvent('app:page-ready', {
            detail: { path: normalizePath(url.pathname), href: url.toString() }
          }));
        } catch (_) {}

        window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
      }

      window.__partialNavigateTo = async function(urlLike, options) {
        const targetUrl = urlLike instanceof URL ? urlLike : new URL(String(urlLike || window.location.href), window.location.href);
        if (!isAllowedUrl(targetUrl)) {
          window.location.href = targetUrl.toString();
          return false;
        }
        await swapPage(targetUrl, options || { replaceState: true });
        return true;
      };

      window.__partialReloadCurrentPage = async function() {
        return window.__partialNavigateTo(window.location.href, { replaceState: true });
      };

      document.addEventListener('click', function(event) {
        const anchor = event.target && event.target.closest ? event.target.closest('a[href]') : null;
        if (!shouldHandleLink(anchor, event)) return;

        event.preventDefault();
        let url;
        try {
          url = new URL(anchor.getAttribute('href'), window.location.href);
        } catch (_) {
          return;
        }

        swapPage(url).catch(err => {
          console.warn('partial navigation failed', err);
          window.location.href = url.toString();
        });
      }, true);

      // Ensure attachments rendered dynamically (help-attachments) open reliably
      // even after partial page swaps which can interfere with normal anchor behavior.
      document.addEventListener('click', function(ev) {
        try {
          const a = ev.target && ev.target.closest ? ev.target.closest('.help-attachments a[href]') : null;
          if (!a) return;
          const href = a.getAttribute('href') || '';
          if (!href) return;
          // open in new tab/window to mimic target="_blank" behavior
          window.open(href, '_blank', 'noopener');
          ev.preventDefault();
        } catch (_) {}
      }, true);

      // Global fallback: ensure E3 page tab buttons toggle panels after partial swaps.
      document.addEventListener('click', function(ev) {
        try {
          const btn = ev.target && ev.target.closest ? ev.target.closest('#quickCheckTabBtn, #analyticTabBtn') : null;
          if (!btn) return;
          const tabId = btn.id === 'analyticTabBtn' ? 'analyticTab' : 'quickCheckTab';
          const tabs = ['quickCheckTab', 'analyticTab'];
          tabs.forEach((id) => {
            const panel = document.getElementById(id);
            if (panel) panel.classList.toggle('hidden', id !== tabId);
          });
          const qbtn = document.getElementById('quickCheckTabBtn');
          const abtn = document.getElementById('analyticTabBtn');
          if (qbtn) qbtn.classList.toggle('active', tabId === 'quickCheckTab');
          if (abtn) abtn.classList.toggle('active', tabId === 'analyticTab');
        } catch (_) {}
      }, true);

      // Global fallback: ensure the "Ρυθμίσεις Γενικών Λογαριασμών" settings-modal
      // tabs (Γενικοί Λογαριασμοί / Συναλλασσόμενοι / Backup) keep working after a
      // partial navigation. Those buttons are wired only inside a DOMContentLoaded
      // handler in credentials_list.html, which does not reliably re-bind to the
      // freshly-swapped buttons after a partial page swap. A delegated handler on
      // document survives every swap. It defers to the page's own switchToTab()
      // (which also re-runs initializeSettingsUi + expand/collapse logic); if that
      // is unavailable it falls back to a minimal panel/button toggle.
      document.addEventListener('click', function(ev) {
        try {
          const btn = ev.target && ev.target.closest ? ev.target.closest('#settingsModal [data-tab]') : null;
          if (!btn) return;
          const tabName = btn.getAttribute('data-tab');
          if (!tabName) return;
          if (typeof window.switchToTab === 'function') {
            window.switchToTab(tabName);
            return;
          }
          // Minimal fallback toggle (mirrors credentials_list.html switchToTab).
          document.querySelectorAll('.settings-tab-panel').forEach(function(p){ p.classList.add('hidden'); });
          const panel = document.getElementById('settings-tab-' + tabName);
          if (panel) panel.classList.remove('hidden');
          document.querySelectorAll('#settingsModal [data-tab]').forEach(function(b){
            const on = b.getAttribute('data-tab') === tabName;
            b.classList.toggle('tab-active', on);
            b.classList.toggle('tab-inactive', !on);
          });
        } catch (_) {}
      }, true);

      window.addEventListener('popstate', function(event) {
        if (!event.state || !event.state.partialNav) return;
        const url = new URL(window.location.href);
        if (!isAllowedUrl(url)) return;
        swapPage(url, { replaceState: true }).catch(err => {
          console.warn('partial navigation popstate failed', err);
          window.location.reload();
        });
      });

      window.__runOnPageReady(function(){
        const url = new URL(window.location.href);
        if (isAllowedUrl(url)) {
          try {
            history.replaceState({ partialNav: true }, '', url.toString());
          } catch (_) {}
        }
        initSearchRepeatProfileHintSync(url.pathname);
        initFetchCredentialLockFallback(url.pathname);
        initFetchDateInputsFallback(url.pathname);
        initE3CheckDateInputsFallback(url.pathname);
        initFetchBulkControlsFallback(url.pathname);
        initFetchModeTabsFallback(url.pathname);
        initFetchBulkActionsFallback(url.pathname);
        initSearchReceiptsOutlineSync(url.pathname);
        initSearchRepeatSwitchStateSync(url.pathname);
      });
    })();
