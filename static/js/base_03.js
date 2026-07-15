    (function(){
      // ====== WAIT OVERLAY (disabled) ======
      // Global wait overlay was making the app feel slow (every fetch/XHR/submit
      // would either show a modal spinner or pay setTimeout cost). The element
      // and API stubs are kept so existing callers don't crash, but show/hide
      // are no-ops. No fetch/XHR/submit patches.
      const waitEl = document.getElementById('globalWait');
      if (waitEl) {
        waitEl.style.display = 'none';
        waitEl.setAttribute('aria-hidden', 'true');
      }
      function noop(){}
      window.WaitOverlay = { show: noop, hide: noop, suspend: noop, forceHide: noop };

          
    // ====== WARNING MODALS INTEGRATION (Reload after ANY warning modal ack) ======
    (function(){
      const WARN_HOST_SELECTORS = [
        '#afmWarningModal', '#receiptWarn', '#receiptWarningModal', '#receiptWarning',
        '[id$="WarningModal"]', '.warning-modal', '[data-role="modal-warning"]'
      ];

      function isVisible(el){
        if(!el) return false;
        const st = window.getComputedStyle(el);
        return st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0';
      }
      function toggleWaitByWarnings(){
        try {
          const anyOpen = WARN_HOST_SELECTORS.some(sel => {
            const node = document.querySelector(sel);
            return node && isVisible(node);
          });
          if (window.WaitOverlay && typeof window.WaitOverlay.suspend === 'function') {
            window.WaitOverlay.suspend(anyOpen);
          }
        } catch(_) {}
      }
      const moWarn = new MutationObserver(toggleWaitByWarnings);
      moWarn.observe(document.documentElement, { attributes:true, childList:true, subtree:true });
      toggleWaitByWarnings();

      function closeModalHost(host){
        try {
          if (typeof bootstrap !== 'undefined' && host) {
            const inst = bootstrap.Modal.getOrCreateInstance(host);
            inst.hide();
            return;
          }
        } catch(_) {}
        try { host.style.display = 'none'; } catch(_){}
      }

      function isAckButton(el){
        if (!el) return false;
        if (el.id === 'afmModalConfirm' || el.id === 'receiptWarnOk') return true;
        if (el.matches && el.matches('[data-ack="1"], [data-role="warning-ack"], .js-warning-ack')) return true;
        const txt = (el.textContent || '').trim().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
        return (txt === 'το καταλαβα' || txt === 'καταλαβα' || txt === 'ενταξει' || txt === 'ok');
      }

      function findWarningHost(el){
        if (!el || !el.closest) return null;
        for (const sel of WARN_HOST_SELECTORS){
          const host = el.closest(sel);
          if (host) return host;
        }
        return null;
      }

      document.addEventListener('click', function(ev){
        const btn = ev.target && ev.target.closest && ev.target.closest('button, [role="button"], a');
        if (!btn) return;
        if (!isAckButton(btn)) return;
        const host = findWarningHost(btn);
        if (!host) return;

        ev.preventDefault();
        try { btn.type = 'button'; } catch(_){}
        try { closeModalHost(host); } catch(_){}
        setTimeout(function(){
          if (window.WaitOverlay && typeof window.WaitOverlay.suspend === 'function') {
            window.WaitOverlay.suspend(true);
            setTimeout(function(){ window.WaitOverlay.suspend(false); }, 250);
          }
          if (typeof window._wipeAndReload === 'function') {
            window._wipeAndReload();
          } else {
            location.reload();
          }
        }, 60);
      }, true);

      const moBtns = new MutationObserver(function(muts){
        muts.forEach(function(m){
          m.addedNodes && m.addedNodes.forEach(function(n){
            if (n.nodeType !== 1) return;
            const btns = n.matches && isAckButton(n) ? [n] : (n.querySelectorAll ? Array.from(n.querySelectorAll('button, [role="button"], a')) : []);
            btns.forEach(function(b){
              if (!isAckButton(b)) return;
              const host = findWarningHost(b);
              if (!host) return;
              b.type = 'button';
              b.onclick = function(e){ e.preventDefault(); closeModalHost(host); setTimeout(function(){ window._wipeAndReload(); }, 40); return false; };
            });
          });
        });
      });
      moBtns.observe(document.body, { childList:true, subtree:true });
    })();
    // Close the OUTER IIFE that started at the top of this block. Missing
    // this `})();` raised "Uncaught SyntaxError: Unexpected end of input"
    // and aborted parsing — so `window.fetch` / XHR / submit patches and
    // every later script (including search.html's partial reload pipeline)
    // never ran.
  })();
  
