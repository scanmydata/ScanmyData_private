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
    // Cross-page Λογιστικό Αποτέλεσμα (accounting_result) Μαζικός «Διακοπή»
    // banner — same shape/slot as the E3 Bulk one right above, just pointed
    // at its own sessionStorage key + progress/abort endpoints so the two
    // bulk jobs never collide.
    // ============================================================================
    (function () {
      var BANNER_ID = 'arBulkProgressFlash';
      function readActive() {
        try {
          var raw = sessionStorage.getItem('arBulkActiveJob');
          if (!raw) return null;
          var obj = JSON.parse(raw);
          if (obj && obj.jobId) return obj;
        } catch (_) {}
        return null;
      }
      function clearActive() {
        try { sessionStorage.removeItem('arBulkActiveJob'); } catch (_) {}
      }
      function removeBanner() {
        var el = document.getElementById(BANNER_ID);
        if (el) try { el.remove(); } catch (_) {}
      }
      function renderBanner(active, text) {
        // ΕΝΑ κοινό container (#flashContainer, εκτός #appShell) για ΟΛΑ τα μηνύματα, ώστε να
        // στοιβάζονται σε μία στήλη: το μόνιμο banner προόδου πάει ΤΕΛΕΥΤΑΙΟ (order) και τα
        // υπόλοιπα (dismissable) εμφανίζονται από πάνω του. Το #arFlashContainer μένει μόνο
        // fallback όταν δεν υπάρχει το κοινό.
        var container = document.getElementById('flashContainer');
        if (!container) container = document.getElementById('arFlashContainer');
        if (!container) container = (typeof ensureFlashContainer === 'function') ? ensureFlashContainer() : null;
        if (!container) container = document.body;
        var el = document.getElementById(BANNER_ID);
        if (!el) {
          el = document.createElement('div');
          el.id = BANNER_ID;
          el.className = 'flash-banner flash-info';
          el.setAttribute('data-flash', '');
          el.setAttribute('data-progress-flash', '');
          el.setAttribute('data-ttl', '0'); // sticky — do not auto-dismiss
          el.style.display = 'flex';
          el.style.alignItems = 'center';
          el.style.gap = '0.5rem';
          el.style.pointerEvents = 'auto';
          el.style.order = '100'; // πάντα στο κάτω μέρος της στήλης, κάτω από τα dismissable
          container.appendChild(el);
        }
        el.innerHTML = '';
        var textSpan = document.createElement('span');
        textSpan.style.flex = '1 1 auto';
        textSpan.style.lineHeight = '1.3';
        textSpan.style.fontSize = '13px';
        textSpan.textContent = text || ('Λογιστικό Αποτέλεσμα — εκτέλεση σε εξέλιξη' + (active.total ? ' για ' + active.total + ' εταιρίες' : '') + '…');
        el.appendChild(textSpan);
        var abortBtn = document.createElement('button');
        abortBtn.type = 'button';
        abortBtn.textContent = 'Διακοπή';
        abortBtn.title = 'Διακοπή μετά την τρέχουσα εταιρία';
        abortBtn.style.background = '#dc2626';
        abortBtn.style.color = '#fff';
        abortBtn.style.border = 'none';
        abortBtn.style.borderRadius = '6px';
        abortBtn.style.padding = '4px 10px';
        abortBtn.style.fontSize = '12px';
        abortBtn.style.fontWeight = '600';
        abortBtn.style.cursor = 'pointer';
        abortBtn.addEventListener('click', function () {
          textSpan.textContent = 'Αίτημα διακοπής στάλθηκε. Θα ολοκληρωθεί η τρέχουσα εταιρία…';
          abortBtn.disabled = true;
          abortBtn.style.opacity = '0.6';
          fetch('/api/accounting_result/bulk_abort/' + encodeURIComponent(active.jobId), { method: 'POST' }).catch(function(){});
        });
        el.appendChild(abortBtn);
      }
      var pollTimer = null;
      var consecutiveEmpty = 0;
      var serverFailures = 0;
      // Δείχνει πόση ώρα μένει το ίδιο βήμα, ώστε ο χρήστης να βλέπει ότι δεν έχει κολλήσει
      // (η ετικέτα αλλάζει σε κάθε εταιρία/βήμα· το χρονόμετρο μηδενίζεται σε κάθε αλλαγή).
      var lastText = '', lastChange = 0;
      function withElapsed(text) {
        if (text !== lastText) { lastText = text; lastChange = Date.now(); return text; }
        var secs = Math.round((Date.now() - lastChange) / 1000);
        if (secs < 6) return text;
        return text + ' · ' + (secs >= 120 ? Math.floor(secs / 60) + '′' + (secs % 60) + '″' : secs + '″');
      }
      // ΝΕΟ: το banner οδηγείται από τον server (/api/accounting_result/active_jobs) ώστε
      // κάθε χρήστης της ομάδας να βλέπει τον Μαζικό που τρέχει — και μετά από logout/login.
      // Στη σελίδα σύνδεσης ή μετά από logout/auto-logout δεν δείχνει τίποτα και σβήνει το τοπικό κλειδί.
      function poll() {
        if (window.IS_LOGGED_IN !== true) { clearActive(); removeBanner(); return; }
        fetch('/api/accounting_result/active_jobs', { cache: 'no-store', credentials: 'same-origin' })
          .then(function (r) {
            var ct = r.headers.get('content-type') || '';
            if (!r.ok || ct.indexOf('json') < 0) {
              var e = new Error('active_jobs HTTP ' + r.status);
              // redirect στη σελίδα σύνδεσης (HTML) ή 401/403 = δεν είμαστε πια συνδεδεμένοι
              e.unauth = (r.status === 401 || r.status === 403 || ct.indexOf('json') < 0);
              throw e;
            }
            return r.json();
          })
          .then(function (data) {
            serverFailures = 0;
            var jobs = (data && data.jobs) || [];
            var local = readActive();
            if (jobs.length) {
              var j = jobs[0];
              var label = j.label || '';
              // στο tab που οδηγεί το run, η τοπική ετικέτα είναι πιο φρέσκια από τον server
              if (local && local.jobId === j.job_id && local.label && j.phase !== 'server' && (Date.now() - (local.updatedAt || 0)) < 20000) label = local.label;
              var pct = (typeof j.percent === 'number') ? ' (' + j.percent + '%)' : '';
              var by = j.username ? ' — εντολή: ' + j.username + (j.mine ? ' (εσύ)' : '') : '';
              renderBanner({ jobId: j.job_id, total: j.total }, withElapsed('Λογιστικό Αποτέλεσμα — ' + (label || 'εκτέλεση σε εξέλιξη') + pct + by));
              return;
            }
            // Ο server δεν ξέρει ενεργό run. Μόλις ξεκίνησε εδώ και δεν έφτασε ακόμη ο 1ος heartbeat;
            if (local && local.label && (Date.now() - (local.updatedAt || 0)) < 8000) {
              renderBanner(local, withElapsed('Λογιστικό Αποτέλεσμα — ' + local.label));
              return;
            }
            clearActive();
            removeBanner();
          })
          .catch(function (err) {
            if (err && err.unauth) { clearActive(); removeBanner(); return; }
            serverFailures++;
            if (serverFailures >= 2) legacyPoll(); // ο server δεν απαντά στο νέο endpoint -> παλιά λογική
          });
      }
      function legacyPoll() {
        var active = readActive();
        if (!active) { removeBanner(); return; }
        renderBanner(active, document.getElementById(BANNER_ID) ? null : ('Λογιστικό Αποτέλεσμα — εκτέλεση σε εξέλιξη' + (active.total ? ' για ' + active.total + ' εταιρίες' : '') + '…'));
        fetch('/api/accounting_result/bulk_progress/' + encodeURIComponent(active.jobId), { cache: 'no-store' })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var p = data && data.progress;
            if (p && p.label) {
              consecutiveEmpty = 0;
              var pct = (typeof p.percent === 'number') ? ' (' + p.percent + '%)' : '';
              renderBanner(active, 'Λογιστικό Αποτέλεσμα — ' + p.label + pct);
            } else if (active.label && (Date.now() - (active.updatedAt || 0)) < 20000) {
              // Browser-driven steps (ΑΑΔΕ/myDATA pre-check, choices): the page
              // keeps active.label current and heartbeats updatedAt every 3s.
              consecutiveEmpty = 0;
              renderBanner(active, 'Λογιστικό Αποτέλεσμα — ' + active.label);
            } else if (active.label) {
              // Heartbeat stopped: a full page load / logout ended the run's
              // script, so nothing is running any more — drop the banner now.
              clearActive();
              removeBanner();
            } else {
              // After ~5 empty polls (15s) + run started >30s ago, assume the
              // job finished (the originating tab clears sessionStorage on
              // success; this is the fallback for that tab being closed mid-run).
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
    // Δραστηριότητες ΟΜΑΔΑΣ (/api/group_activity): ό,τι τρέχει τώρα στην ενεργή ομάδα — Λήψη παραστατικών
    // (ατομική/μαζική) και Λογιστικό Αποτέλεσμα (Ατομικός/Μαζικός) — με το ποιος έδωσε την εντολή.
    //  * flash μήνυμα προς ΟΛΟΥΣ τους χρήστες της ομάδας (ο Μαζικός Λογιστικού έχει δικό του banner με Διακοπή
    //    παραπάνω· ο χρήστης που ξεκίνησε μια λήψη βλέπει ήδη το δικό του flash προόδου με Διακοπή),
    //  * κλείδωμα κουμπιών με data-lock-group="ar"|"fetch" όσο τρέχει το αντίστοιχο είδος εργασίας.
    // Ο server επιβάλλει το κλείδωμα επίσης (HTTP 409), άρα και χωρίς αυτό το script δεν ξεκινά δεύτερη εργασία.
    // ============================================================================
    (function () {
      // Λήψη παραστατικών και έλεγχοι Λογιστικού Αποτελέσματος κλειδώνουν ΟΛΑ μεταξύ τους (ο υπολογισμός δεν πρέπει
      // να τρέχει πάνω σε δεδομένα που αλλάζουν και η λήψη δεν ξεκινά όσο υπολογίζεται αποτέλεσμα).
      var LOCK_KINDS = { ar: ['ar_bulk', 'ar_single', 'fetch'], fetch: ['fetch', 'ar_bulk', 'ar_single'] };
      var WHAT = { ar_bulk: 'Μαζικός υπολογισμός Λογιστικού Αποτελέσματος', ar_single: 'Ατομικός υπολογισμός Λογιστικού Αποτελέσματος', fetch: 'Λήψη παραστατικών' };
      var state = [];
      var timer = null;
      var rendered = {};
      var prevMineBulk = {};
      function getContainer() {
        var c = document.getElementById('flashContainer');
        if (!c) c = document.getElementById('arFlashContainer');
        if (!c && typeof ensureFlashContainer === 'function') c = ensureFlashContainer();
        return c || document.body;
      }
      function removeAll() {
        document.querySelectorAll('[data-group-activity]').forEach(function (el) { try { el.remove(); } catch (_) {} });
      }
      function bannerText(a) {
        var label = a.label || WHAT[a.kind] || 'εργασία';
        var pct = (typeof a.percent === 'number') ? ' (' + a.percent + '%)' : '';
        var head = a.kind === 'fetch' ? label : (WHAT[a.kind] + ' — ' + label);
        return head + pct + ' — εντολή: ' + (a.username || '—') + (a.mine ? ' (εσύ)' : '') + ' · κλειδωμένο μέχρι να ολοκληρωθεί';
      }
      function render(acts) {
        var wanted = {};
        var container = getContainer();
        acts.forEach(function (a) {
          // Ο Μαζικός Λογιστικού έχει το δικό του banner· ο Ατομικός φαίνεται ως overlay στον ίδιο τον χρήστη.
          if (a.kind === 'ar_bulk') return;
          if (a.kind === 'ar_single' && a.mine) return;
          // Λήψη που ξεκίνησε εδώ: υπάρχει ήδη το δικό της flash προόδου (με Διακοπή).
          if (a.kind === 'fetch' && a.mine && document.querySelector('[data-progress-flash="1"]')) return;
          var id = 'groupActivity-' + a.id;
          wanted[id] = true;
          var el = document.getElementById(id);
          // Το μήνυμα που έκλεισε ο χρήστης (×) δεν ξαναεμφανίζεται· το κλείδωμα των κουμπιών μένει.
          if (!el && rendered[id]) return;
          if (!el) {
            rendered[id] = true;
            el = document.createElement('div');
            el.id = id;
            el.className = 'flash-banner flash-info';
            el.setAttribute('data-flash', '');
            el.setAttribute('data-group-activity', '1');
            el.setAttribute('data-ttl', '0');
            el.style.display = 'flex';
            el.style.alignItems = 'center';
            el.style.gap = '0.5rem';
            el.style.pointerEvents = 'auto';
            el.style.order = '99'; // πριν από το banner προόδου Μαζικού (order 100), κάτω από τα dismissable
            var span = document.createElement('span');
            span.style.flex = '1 1 auto';
            span.style.lineHeight = '1.3';
            span.style.fontSize = '13px';
            el.appendChild(span);
            container.appendChild(el);
          }
          var t = el.querySelector('span');
          var txt = '🔒 ' + bannerText(a);
          if (t && t.textContent !== txt) t.textContent = txt;
        });
        document.querySelectorAll('[data-group-activity]').forEach(function (el) {
          if (!wanted[el.id]) { try { el.remove(); } catch (_) {} }
        });
        Object.keys(rendered).forEach(function (k) { if (!wanted[k]) delete rendered[k]; });
      }
      function holderFor(group) {
        var kinds = LOCK_KINDS[group] || [];
        for (var i = 0; i < state.length; i++) if (kinds.indexOf(state[i].kind) >= 0) return state[i];
        return null;
      }
      function applyLocks() {
        document.querySelectorAll('[data-lock-group]').forEach(function (el) {
          var holder = holderFor(el.getAttribute('data-lock-group'));
          var marked = el.getAttribute('data-group-locked') === '1';
          if (holder) {
            if (!el.disabled) {
              el.disabled = true;
              if (!marked) {
                el.setAttribute('data-group-locked', '1');
                el.setAttribute('data-orig-title', el.getAttribute('title') || '');
                marked = true;
              }
            }
            if (marked) el.title = 'Κλειδωμένο: τρέχει ' + (WHAT[holder.kind] || 'εργασία') + ' (εντολή: ' + (holder.username || '—') + ')';
          } else if (marked) {
            el.disabled = false;
            var ot = el.getAttribute('data-orig-title') || '';
            if (ot) el.title = ot; else el.removeAttribute('title');
            el.removeAttribute('data-group-locked');
            el.removeAttribute('data-orig-title');
          }
        });
      }
      function poll() {
        if (window.IS_LOGGED_IN !== true) { state = []; removeAll(); applyLocks(); return; }
        fetch('/api/group_activity', { cache: 'no-store', credentials: 'same-origin', headers: { 'X-Wait-Overlay': 'skip' } })
          .then(function (r) {
            var ct = r.headers.get('content-type') || '';
            if (!r.ok || ct.indexOf('json') < 0) { var e = new Error('group_activity HTTP ' + r.status); e.unauth = true; throw e; }
            return r.json();
          })
          .then(function (data) {
            state = (data && data.activities) || [];
            window.__groupActivity = state;
            // Ο Μαζικός μου (που τρέχει στον server) τελείωσε ενώ δεν είμαι στη σελίδα Μαζικού/δεν τον παρακολουθεί
            // κανένα script: ειδοποίηση ότι τα αποτελέσματα και οι έλεγχοι περιμένουν στη σελίδα.
            try {
              var nowMine = {};
              state.forEach(function (a) { if (a.kind === 'ar_bulk' && a.mine) nowMine[a.id] = true; });
              Object.keys(prevMineBulk).forEach(function (id) {
                if (!nowMine[id] && !window.__arBulkAttached && typeof window.showFlash === 'function') {
                  window.showFlash('Ο Μαζικός υπολογισμός Λογιστικού Αποτελέσματος ολοκληρώθηκε — άνοιξε τη σελίδα «Λογιστικό Αποτέλεσμα» για τα αποτελέσματα και τους ελέγχους.', 'success', 15000);
                }
              });
              prevMineBulk = nowMine;
            } catch (_) {}
            render(state);
            applyLocks();
            try { window.dispatchEvent(new CustomEvent('group-activity', { detail: state })); } catch (_) {}
          })
          .catch(function (err) {
            if (err && err.unauth) { state = []; removeAll(); applyLocks(); }
          });
      }
      function start() {
        if (timer) return;
        timer = setInterval(poll, 3000);
        setInterval(applyLocks, 1000); // κουμπιά που ξαναγράφονται/ενεργοποιούνται από τη σελίδα ή μετά από partial-nav
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
        if (ttlAttr > 0 && !el.__appFlashTimer) {
          // Κοινός timer με το flash_center.js (ανανέωση διπλότυπου = νέος χρόνος).
          if (typeof window.__appFlashArmTtl === 'function') window.__appFlashArmTtl(el, ttlAttr);
          else setTimeout(()=>{ try{ el.remove(); }catch(_){ } }, ttlAttr);
        }
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
  
