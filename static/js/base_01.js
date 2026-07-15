    window.__runOnPageReady = function(fn) {
      if (typeof fn !== 'function') return;
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fn, { once: true });
      } else {
        fn();
      }
    };

    window.__cleanupPageScripts = null;
    window.__registerPageCleanup = function(fn) {
      window.__cleanupPageScripts = typeof fn === 'function' ? fn : null;
    };

    // ============================================================================
    // Global sticky banner for long-running background jobs (e.g. E3 Brain bulk).
    // Lives outside #appShell so partial navigation never clears it.
    //
    // Usage:
    //   window.showStickyBanner({ id:'e3-bulk', text:'…', actionText:'Διακοπή',
    //                             onAction: function(){...} });
    //   window.updateStickyBanner('e3-bulk', { text:'5/12 ολοκληρώθηκαν' });
    //   window.hideStickyBanner('e3-bulk');
    // ============================================================================
    (function () {
      var banners = {};
      function host() { return document.getElementById('globalStickyBannerHost'); }
      function render() {
        var h = host(); if (!h) return;
        var keys = Object.keys(banners);
        if (!keys.length) { h.innerHTML = ''; return; }
        var html = '';
        keys.forEach(function (id) {
          var b = banners[id];
          var color = b.kind === 'error' ? '#b91c1c' : (b.kind === 'success' ? '#047857' : '#1d4ed8');
          html += '<div data-banner-id="' + String(id).replace(/"/g, '&quot;') + '" ' +
                  'style="pointer-events:auto;margin:6px auto;max-width:1100px;background:' + color + ';color:#fff;' +
                  'padding:8px 14px;border-radius:0 0 12px 12px;display:flex;align-items:center;gap:10px;' +
                  'box-shadow:0 6px 14px rgba(0,0,0,0.18);font:500 14px system-ui">' +
                  '<span style="flex:1 1 auto;line-height:1.35">' + String(b.text || '').replace(/</g, '&lt;') + '</span>';
          if (b.actionText) {
            html += '<button type="button" data-banner-action="' + String(id).replace(/"/g, '&quot;') + '" ' +
                    'style="background:#fff;color:#111;padding:4px 12px;border-radius:8px;border:none;font-weight:600;' +
                    'cursor:pointer;">' + String(b.actionText).replace(/</g, '&lt;') + '</button>';
          }
          html += '<button type="button" data-banner-dismiss="' + String(id).replace(/"/g, '&quot;') + '" ' +
                  'style="background:transparent;color:#fff;border:none;cursor:pointer;font-size:18px;line-height:1;padding:0 4px;">×</button>';
          html += '</div>';
        });
        h.innerHTML = html;
        h.querySelectorAll('[data-banner-action]').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var id = btn.getAttribute('data-banner-action');
            var b = banners[id];
            if (b && typeof b.onAction === 'function') { try { b.onAction(); } catch (_) {} }
          });
        });
        h.querySelectorAll('[data-banner-dismiss]').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var id = btn.getAttribute('data-banner-dismiss');
            window.hideStickyBanner(id);
          });
        });
      }
      window.showStickyBanner = function (opts) {
        if (!opts || !opts.id) return;
        banners[opts.id] = Object.assign({}, banners[opts.id] || {}, opts);
        render();
      };
      window.updateStickyBanner = function (id, opts) {
        if (!id || !banners[id]) return;
        banners[id] = Object.assign({}, banners[id], opts || {});
        render();
      };
      window.hideStickyBanner = function (id) {
        if (!id) return;
        delete banners[id];
        render();
      };
      window.__hasStickyBanner = function (id) { return !!banners[id]; };
    })();

    // ============================================================================
    // Cross-page E3 Bulk «Διακοπή» banner — RIGHT-side flash style.
    //
    // Originally rendered into `globalStickyBannerHost` (full-width strip
    // at the top of the viewport) but the user specifically asked for a
    // smaller, right-side flash «οπως το Αποθηκεύτηκαν credentials» that
    // doesn't cover the header menu. We piggy-back on `#flashContainer`
    // (the fixed top-right slot created by `ensureFlashContainer`) and
    // render a custom flash element with the «Διακοπή» button inline.
    // ============================================================================
    (function () {
      var BANNER_ID = 'e3BulkProgressFlash';
      function readActive() {
        try {
          var raw = sessionStorage.getItem('e3BulkActiveJob');
          if (!raw) return null;
          var obj = JSON.parse(raw);
          if (obj && obj.jobId) return obj;
        } catch (_) {}
        return null;
      }
      function clearActive() {
        try { sessionStorage.removeItem('e3BulkActiveJob'); } catch (_) {}
      }
      function removeBanner() {
        var el = document.getElementById(BANNER_ID);
        if (el) try { el.remove(); } catch (_) {}
        // Also clean up any leftover globalStickyBanner from the previous
        // (top-center) implementation so we don't render in two places.
        try { window.hideStickyBanner && window.hideStickyBanner('e3-bulk'); } catch (_) {}
      }
      function renderBanner(active, text) {
        var container = (typeof ensureFlashContainer === 'function') ? ensureFlashContainer() : null;
        if (!container) container = document.getElementById('flashContainer') || document.body;
        var el = document.getElementById(BANNER_ID);
        var firstTime = false;
        if (!el) {
          firstTime = true;
          el = document.createElement('div');
          el.id = BANNER_ID;
          el.className = 'flash-banner flash-info';
          el.setAttribute('data-flash', '');
          el.setAttribute('data-ttl', '0'); // sticky — do not auto-dismiss
          el.style.display = 'flex';
          el.style.alignItems = 'center';
          el.style.gap = '0.5rem';
          el.style.pointerEvents = 'auto';
          container.prepend(el);
        }
        // Re-render contents (so progress updates show without
        // re-binding the abort button).
        el.innerHTML = '';
        var textSpan = document.createElement('span');
        textSpan.style.flex = '1 1 auto';
        textSpan.style.lineHeight = '1.3';
        textSpan.style.fontSize = '13px';
        textSpan.textContent = text || ('E3 Bulk — εκτέλεση σε εξέλιξη' + (active.total ? ' για ' + active.total + ' πελάτες' : '') + '…');
        el.appendChild(textSpan);
        var abortBtn = document.createElement('button');
        abortBtn.type = 'button';
        abortBtn.textContent = 'Διακοπή';
        abortBtn.title = 'Διακοπή μετά τον τρέχοντα πελάτη';
        abortBtn.style.background = '#dc2626';
        abortBtn.style.color = '#fff';
        abortBtn.style.border = 'none';
        abortBtn.style.borderRadius = '6px';
        abortBtn.style.padding = '4px 10px';
        abortBtn.style.fontSize = '12px';
        abortBtn.style.fontWeight = '600';
        abortBtn.style.cursor = 'pointer';
        abortBtn.addEventListener('click', function () {
          textSpan.textContent = 'Αίτημα διακοπής στάλθηκε. Θα ολοκληρωθεί ο τρέχων πελάτης…';
          abortBtn.disabled = true;
          abortBtn.style.opacity = '0.6';
          fetch('/api/e3/brain/abort/' + encodeURIComponent(active.jobId), { method: 'POST' }).catch(function(){});
        });
        el.appendChild(abortBtn);
        return firstTime;
      }
      var pollTimer = null;
      var consecutiveEmpty = 0;
      function poll() {
        var active = readActive();
        if (!active) { removeBanner(); return; }
        renderBanner(active, document.getElementById(BANNER_ID) ? null : ('E3 Bulk — εκτέλεση σε εξέλιξη' + (active.total ? ' για ' + active.total + ' πελάτες' : '') + '…'));
        fetch('/api/e3/brain/progress/' + encodeURIComponent(active.jobId), { cache: 'no-store' })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var p = data && data.progress;
            if (p && p.label) {
              consecutiveEmpty = 0;
              var pct = (typeof p.percent === 'number') ? ' (' + p.percent + '%)' : '';
              renderBanner(active, 'E3 Bulk — ' + p.label + pct);
            } else {
              // After ~5 empty polls (15s) + run started >30s ago, assume
              // brain finished (the originating tab clears sessionStorage
              // on success; this is the fallback for that tab being
              // closed mid-run).
              consecutiveEmpty++;
              if (consecutiveEmpty > 5 && (Date.now() - (active.startedAt || 0)) > 30000) {
                clearActive();
                removeBanner();
              }
            }
          })
          .catch(function () { /* network blip — leave banner up */ });
      }
      function start() {
        if (pollTimer) return;
        pollTimer = setInterval(poll, 3000);
        poll();
      }
      try { start(); } catch (_) {}
    })();

    // ============================================================================
    // COOKIE CONSENT MANAGEMENT (GDPR)
    // ============================================================================
    (function() {
      const COOKIE_CONSENT_KEY = 'cookieConsent';
      const COOKIE_PREFERENCES_KEY = 'cookiePreferences';
      
      function getCookieConsent() {
        try {
          return localStorage.getItem(COOKIE_CONSENT_KEY);
        } catch (e) {
          return null;
        }
      }
      
      function setCookieConsent(value) {
        try {
          localStorage.setItem(COOKIE_CONSENT_KEY, value);
        } catch (e) {
          console.error('Failed to save cookie consent:', e);
        }
      }
      
      function getCookiePreferences() {
        try {
          const prefs = localStorage.getItem(COOKIE_PREFERENCES_KEY);
          return prefs ? JSON.parse(prefs) : { functional: true, analytics: false };
        } catch (e) {
          return { functional: true, analytics: false };
        }
      }
      
      function setCookiePreferences(prefs) {
        try {
          localStorage.setItem(COOKIE_PREFERENCES_KEY, JSON.stringify(prefs));
        } catch (e) {
          console.error('Failed to save cookie preferences:', e);
        }
      }
      
      function showCookieBanner() {
        const banner = document.getElementById('cookieConsent');
        if (banner) banner.style.display = 'block';
      }
      
      function hideCookieBanner() {
        const banner = document.getElementById('cookieConsent');
        if (banner) banner.style.display = 'none';
      }
      
      function showCookieSettings() {
        const modal = document.getElementById('cookieSettingsModal');
        if (modal) {
          modal.style.display = 'flex';
          
          // Load current preferences
          const prefs = getCookiePreferences();
          const functionalCheckbox = document.getElementById('functionalCookies');
          const analyticsCheckbox = document.getElementById('analyticsCookies');
          
          if (functionalCheckbox) functionalCheckbox.checked = prefs.functional !== false;
          if (analyticsCheckbox) analyticsCheckbox.checked = prefs.analytics === true;
        }
      }
      
      function hideCookieSettings() {
        const modal = document.getElementById('cookieSettingsModal');
        if (modal) modal.style.display = 'none';
      }
      
      function acceptAllCookies() {
        setCookieConsent('accepted');
        setCookiePreferences({ functional: true, analytics: true });
        hideCookieBanner();
        hideCookieSettings();
      }
      
      function rejectAllCookies() {
        setCookieConsent('rejected');
        setCookiePreferences({ functional: false, analytics: false });
        hideCookieBanner();
        hideCookieSettings();
      }
      
      function saveCustomSettings() {
        const functionalCheckbox = document.getElementById('functionalCookies');
        const analyticsCheckbox = document.getElementById('analyticsCookies');
        
        const prefs = {
          functional: functionalCheckbox ? functionalCheckbox.checked : true,
          analytics: analyticsCheckbox ? analyticsCheckbox.checked : false
        };
        
        setCookieConsent('custom');
        setCookiePreferences(prefs);
        hideCookieBanner();
        hideCookieSettings();
      }
      
      // Initialize on page load
      document.addEventListener('DOMContentLoaded', function() {
        const consent = getCookieConsent();
        
        // Show banner if no consent given
        if (!consent) {
          // Delay showing banner by 1 second for better UX
          setTimeout(showCookieBanner, 1000);
        }
        
        // Attach event listeners
        const acceptBtn = document.getElementById('cookieAccept');
        const settingsBtn = document.getElementById('cookieSettings');
        const settingsCloseBtn = document.getElementById('cookieSettingsClose');
        const rejectAllBtn = document.getElementById('cookieRejectAll');
        const saveSettingsBtn = document.getElementById('cookieSaveSettings');
        const acceptAllBtn = document.getElementById('cookieAcceptAll');
        
        if (acceptBtn) acceptBtn.addEventListener('click', acceptAllCookies);
        if (settingsBtn) settingsBtn.addEventListener('click', showCookieSettings);
        if (settingsCloseBtn) settingsCloseBtn.addEventListener('click', hideCookieSettings);
        if (rejectAllBtn) rejectAllBtn.addEventListener('click', rejectAllCookies);
        if (saveSettingsBtn) saveSettingsBtn.addEventListener('click', saveCustomSettings);
        if (acceptAllBtn) acceptAllBtn.addEventListener('click', acceptAllCookies);
        
        // Close modal on outside click
        const modal = document.getElementById('cookieSettingsModal');
        if (modal) {
          modal.addEventListener('click', function(e) {
            if (e.target === modal) {
              hideCookieSettings();
            }
          });
        }
      });
      
      // Expose function to check if analytics are enabled (for conditional loading)
      window.areAnalyticsEnabled = function() {
        const consent = getCookieConsent();
        if (!consent) return false;
        if (consent === 'rejected') return false;
        if (consent === 'accepted') return true;
        const prefs = getCookiePreferences();
        return prefs.analytics === true;
      };
    })();
    
    // ============================================================================
    // EXISTING SCRIPTS
    // ============================================================================
    // Dismissable + auto-close (5-6s) ONLY for .flash-banner
    document.addEventListener('DOMContentLoaded', function(){
      const AUTO_TTL = 6000; // ms

      function makeDismissable(el){
        if (!el || el.dataset.dismissable === '1') return;
        el.dataset.dismissable = '1';

        // add small × button
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('aria-label','Close');
        btn.textContent = '×';
        btn.style.cssText = 'position:absolute;top:.4rem;right:.5rem;line-height:1;font-size:20px;opacity:.75;cursor:pointer;background:transparent;border:0;padding:0;z-index:1';

        const cs = getComputedStyle(el);
        if (cs.position === 'static') el.style.position = 'relative';
        if (!el.style.paddingRight) {
          const pr = parseFloat(cs.paddingRight || '0');
          if (pr < 22) el.style.paddingRight = '28px';
        }

        btn.addEventListener('click', ()=> { try{ el.remove(); }catch(_){ } });
        el.appendChild(btn);

        const ttlAttr = parseInt(el.getAttribute('data-ttl') || AUTO_TTL, 10);
        if (ttlAttr > 0) setTimeout(()=>{ try{ el.remove(); }catch(_){ } }, ttlAttr);
      }

      document.querySelectorAll('.flash-banner').forEach(makeDismissable);

      // Also observe for dynamically added flashes
      const mo = new MutationObserver(muts=>{
        muts.forEach(m=> m.addedNodes && m.addedNodes.forEach(n=>{
          if (n.nodeType===1) {
            if (n.matches && n.matches('.flash-banner')) makeDismissable(n);
            n.querySelectorAll && n.querySelectorAll('.flash-banner').forEach(makeDismissable);
          }
        }));
      });
      mo.observe(document.body, { childList:true, subtree:true });

      // Support existing close buttons if present
      document.addEventListener('click', function(e){
        const b = e.target.closest && e.target.closest('[data-dismiss="flash"], .btn-close, .close');
        if (!b) return;
        const host = b.closest('.flash-banner') || b.parentElement;
        if (host) { e.preventDefault(); try{ host.remove(); }catch(_){ } }
      }, true);
    });
  
