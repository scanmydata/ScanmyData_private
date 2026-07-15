    // Global notifications + global fetch-progress banner (cross-page, partial-reload resilient)
    (function(){
      const FETCH_TARGET_KEY = 'scanmydata:fetchProgressTarget:v1';
      const FETCH_SNAPSHOT_KEY = 'scanmydata:fetchProgressSnapshot:v1';
      const FETCH_FLASH_ID = 'globalFetchProgressFlash';
      const BULK_FETCH_TARGET_KEY = 'scanmydata:bulkFetchProgressTarget:v1';
      const BULK_FETCH_SNAPSHOT_KEY = 'scanmydata:bulkFetchProgressSnapshot:v1';
      const BULK_FETCH_JOB_KEY = 'scanmydata.bulkFetchJobId';
      const FETCH_FLASH_DISMISSED_KEY = 'scanmydata:fetchFlashDismissed:v1';
      let globalFetchFlashHideTimer = null;

      function scheduleGlobalFetchFlashHide(ms){
        try {
          if (globalFetchFlashHideTimer) {
            clearTimeout(globalFetchFlashHideTimer);
            globalFetchFlashHideTimer = null;
          }
        } catch (_) {}
        const delay = Number(ms || 0);
        if (delay > 0) {
          globalFetchFlashHideTimer = setTimeout(function(){
            clearGlobalFetchProgressFlash();
            try {
              const bulkSnap = readBulkFetchSnapshot();
              const singleSnap = readFetchSnapshot();
              if (!bulkSnap || !['running','stopping'].includes(String(bulkSnap.status || ''))) setBulkFetchSnapshot(null);
              if (!singleSnap || !['running','stopping'].includes(String(singleSnap.status || ''))) setFetchSnapshot(null);
            } catch (_) {}
          }, delay);
        }
      }

      function stateShouldRemainVisible(state){
        if (!state) return false;
        const status = String(state.status || '');
        if (['running','stopping'].includes(status)) return true;
        const hideAt = Number(state.hide_after_at || 0);
        return !!hideAt && Date.now() < hideAt;
      }

      function getFetchFlashSignature(state){
        const s = state || {};
        const status = String(s.status || 'not_started').toLowerCase();
        const lifecycle = ['running', 'stopping'].includes(status) ? 'active' : (status || 'terminal');
        const mode = (String(s.mode || '') === 'bulk' || String(s.job_id || '').trim()) ? 'bulk' : 'single';
        const bulkId = String(s.job_id || '').trim();
        const singleId = [String(s.credential || '').trim(), String(s.vat || '').trim()].filter(Boolean).join('|');
        const id = mode === 'bulk' ? bulkId : singleId;
        return id ? [mode, lifecycle, id].join('::') : '';
      }

      function readDismissedFetchFlash(){
        return readJson(FETCH_FLASH_DISMISSED_KEY) || null;
      }

      function clearDismissedFetchFlash(){
        writeJson(FETCH_FLASH_DISMISSED_KEY, null);
      }

      function dismissFetchFlashState(state){
        const signature = getFetchFlashSignature(state);
        if (!signature) return;
        writeJson(FETCH_FLASH_DISMISSED_KEY, {
          signature,
          dismissed_at: new Date().toISOString(),
        });
      }

      function dismissFetchFlashSignature(signature){
        const sig = String(signature || '').trim();
        if (!sig) return;
        writeJson(FETCH_FLASH_DISMISSED_KEY, {
          signature: sig,
          dismissed_at: new Date().toISOString(),
        });
      }

      function isFetchFlashDismissed(state){
        const marker = readDismissedFetchFlash();
        const signature = getFetchFlashSignature(state);
        return !!(marker && signature && String(marker.signature || '') === signature);
      }

      function readJson(key){
        try {
          const raw = localStorage.getItem(key);
          return raw ? (JSON.parse(raw) || null) : null;
        } catch(_) {
          return null;
        }
      }

      function writeJson(key, value){
        try {
          if (!value) {
            localStorage.removeItem(key);
            return;
          }
          localStorage.setItem(key, JSON.stringify(value));
        } catch(_) {}
      }

      function readFetchTarget(){
        const t = readJson(FETCH_TARGET_KEY) || {};
        let credential = String(t.credential || '').trim();
        let vat = String(t.vat || '').trim();
        let updated_at = t.updated_at || null;

        if (!credential && !vat) {
          const snap = readFetchSnapshot() || {};
          const snapStatus = String(snap.status || '').toLowerCase();
          if (['running', 'stopping'].includes(snapStatus)) {
            credential = String(snap.credential || '').trim();
            vat = String(snap.vat || '').trim();
            updated_at = snap.updated_at || updated_at;
          }
        }

        if (!credential && !vat) return null;
        return {
          credential,
          vat,
          updated_at,
        };
      }

      function setFetchTarget(payload){
        const p = payload || {};
        const credential = String(p.credential || '').trim();
        const vat = String(p.vat || '').trim();
        const prev = readJson(FETCH_TARGET_KEY) || {};
        if (!credential && !vat) {
          writeJson(FETCH_TARGET_KEY, null);
          return;
        }
        if (String(prev.credential || '').trim() !== credential || String(prev.vat || '').trim() !== vat) {
          clearDismissedFetchFlash();
        }
        writeJson(FETCH_TARGET_KEY, {
          credential,
          vat,
          updated_at: new Date().toISOString(),
        });
      }

      function clearFetchTarget(){
        writeJson(FETCH_TARGET_KEY, null);
      }

      function setFetchSnapshot(state){
        if (!state) {
          writeJson(FETCH_SNAPSHOT_KEY, null);
          return;
        }
        const status = String(state.status || 'not_started');
        const existing = readFetchSnapshot() || {};
        const isTerminal = ['completed', 'error', 'stopped'].includes(status);
        writeJson(FETCH_SNAPSHOT_KEY, {
          mode: 'single',
          status: status,
          percent: Math.max(0, Math.min(100, Number(state.percent || 0))),
          message: String(state.message || ''),
          credential: String(state.credential || ''),
          vat: String(state.vat || ''),
          updated_at: new Date().toISOString(),
          hide_after_at: isTerminal ? (Date.now() + 5000) : (['running','stopping'].includes(status) ? null : Number(existing.hide_after_at || 0) || null),
        });
      }

      function readFetchSnapshot(){
        return readJson(FETCH_SNAPSHOT_KEY) || null;
      }

      function readBulkFetchTarget(){
        const t = readJson(BULK_FETCH_TARGET_KEY) || {};
        let job_id = String(t.job_id || '').trim();
        let updated_at = t.updated_at || null;

        if (!job_id) {
          try {
            job_id = String(localStorage.getItem(BULK_FETCH_JOB_KEY) || '').trim();
          } catch (_) {}
        }

        if (!job_id) {
          const snap = readBulkFetchSnapshot() || {};
          const snapStatus = String(snap.status || '').toLowerCase();
          if (['running', 'stopping'].includes(snapStatus)) {
            job_id = String(snap.job_id || '').trim();
            updated_at = snap.updated_at || updated_at;
          }
        }

        if (!job_id) return null;
        return {
          job_id,
          updated_at,
        };
      }

      function setBulkFetchTarget(payload){
        const p = payload || {};
        const job_id = String(p.job_id || '').trim();
        const prev = readJson(BULK_FETCH_TARGET_KEY) || {};
        if (!job_id) {
          writeJson(BULK_FETCH_TARGET_KEY, null);
          return;
        }
        if (String(prev.job_id || '').trim() !== job_id) {
          clearDismissedFetchFlash();
        }
        writeJson(BULK_FETCH_TARGET_KEY, {
          job_id,
          updated_at: new Date().toISOString(),
        });
      }

      function clearBulkFetchTarget(){
        writeJson(BULK_FETCH_TARGET_KEY, null);
      }

      function setBulkFetchSnapshot(state){
        if (!state) {
          writeJson(BULK_FETCH_SNAPSHOT_KEY, null);
          return;
        }
        const status = String(state.status || 'not_started');
        const existing = readBulkFetchSnapshot() || {};
        const isTerminal = ['completed', 'error', 'stopped'].includes(status);
        writeJson(BULK_FETCH_SNAPSHOT_KEY, {
          mode: 'bulk',
          status: status,
          percent: Math.max(0, Math.min(100, Number(state.percent || 0))),
          message: String(state.message || ''),
          job_id: String(state.job_id || ''),
          current_customer: String(state.current_customer || ''),
          current_index: Number(state.current_index || 0),
          total_customers: Number(state.total_customers || 0),
          updated_at: new Date().toISOString(),
          hide_after_at: isTerminal ? (Date.now() + 5000) : (['running','stopping'].includes(status) ? null : Number(existing.hide_after_at || 0) || null),
        });
      }

      function readBulkFetchSnapshot(){
        return readJson(BULK_FETCH_SNAPSHOT_KEY) || null;
      }

      async function requestGlobalBulkFetchStop(){
        const target = readBulkFetchTarget() || {};
        const snap = readBulkFetchSnapshot() || {};
        const jobId = String(target.job_id || snap.job_id || '').trim();
        if (!jobId) return;

        const stoppingState = {
          mode: 'bulk',
          status: 'stopping',
          percent: Math.max(1, Number(snap.percent || 1)),
          message: 'Η διακοπή της μαζικής λήψης ζητήθηκε.',
          job_id: jobId,
          current_customer: String(snap.current_customer || ''),
          current_index: Number(snap.current_index || 0),
          total_customers: Number(snap.total_customers || 0),
        };
        setBulkFetchSnapshot(stoppingState);
        upsertGlobalFetchProgressFlash(stoppingState);

        try {
          const res = await fetch('/api/fetch_bulk/stop', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
              'Content-Type': 'application/json',
              'X-Requested-With': 'XMLHttpRequest',
              'Accept': 'application/json'
            },
            body: JSON.stringify({ job_id: jobId, source: 'global_flash' })
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data || data.ok !== true) {
            throw new Error((data && (data.error || data.message)) ? (data.error || data.message) : 'Αποτυχία αίτησης διακοπής.');
          }
          const msg = String(data.message || 'Η διακοπή ζητήθηκε.');
          const updated = Object.assign({}, stoppingState, { message: msg });
          setBulkFetchSnapshot(updated);
          upsertGlobalFetchProgressFlash(updated);
        } catch (err) {
          const errorState = {
            mode: 'bulk',
            status: 'error',
            percent: Math.max(1, Number(snap.percent || 1)),
            message: String((err && err.message) || 'Αποτυχία αίτησης διακοπής.'),
            job_id: jobId,
            current_customer: String(snap.current_customer || ''),
            current_index: Number(snap.current_index || 0),
            total_customers: Number(snap.total_customers || 0),
          };
          setBulkFetchSnapshot(errorState);
          upsertGlobalFetchProgressFlash(errorState);
        }
      }

      function ensureFlashContainer(){
        let container = document.getElementById('flashContainer');
        if (container) return container;
        try {
          container = document.createElement('div');
          container.id = 'flashContainer';
          container.className = 'space-y-2 mb-4';
          container.style.position = 'fixed';
          container.style.top = 'calc(env(safe-area-inset-top, 0px) + 8.75rem)';
          container.style.right = '1rem';
          container.style.zIndex = '100120';
          container.style.width = 'min(92vw, 380px)';
          container.style.margin = '0';
          container.style.display = 'flex';
          container.style.flexDirection = 'column';
          container.style.gap = '0.5rem';
          container.style.pointerEvents = 'none';
          document.body.appendChild(container);
          return container;
        } catch (_) {
          return document.body;
        }
      }

      function clearGlobalFetchProgressFlash(){
        try {
          document.querySelectorAll('[data-progress-flash="1"]').forEach(function(node){ node.remove(); });
        } catch (_) {}
        const existing = document.getElementById(FETCH_FLASH_ID);
        if (existing) existing.remove();
      }

      function showStandardFetchFlash(message, type, ttl, flashId, state){
        const container = ensureFlashContainer();
        if (!container || !message) return;

        const kind = String(type || 'success').toLowerCase();
        const cls = kind === 'error' ? 'flash-error' : (kind === 'warning' ? 'flash-warning' : 'flash-success');
        const id = String(flashId || 'fetchFinalFlashMessage');
        const timeoutMs = Number(ttl ?? 5000);

        let el = document.getElementById(id);
        if (!el) {
          el = document.createElement('div');
          el.id = id;
          el.setAttribute('data-flash', '');
          container.prepend(el);
        }

        el.className = 'flash-banner ' + cls;
        el.setAttribute('data-ttl', String(timeoutMs));
        el.style.pointerEvents = 'auto';
        el.dataset.fetchFlashSignature = getFetchFlashSignature(state || {});
        el.style.display = 'flex';
        el.style.alignItems = 'center';
        el.style.justifyContent = 'space-between';
        el.style.gap = '0.75rem';

        const textNode = document.createElement('span');
        textNode.textContent = String(message || '');
        textNode.style.flex = '1 1 auto';

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.setAttribute('aria-label', 'Close');
        closeBtn.textContent = '×';
        closeBtn.style.background = 'transparent';
        closeBtn.style.border = '0';
        closeBtn.style.cursor = 'pointer';
        closeBtn.style.fontSize = '20px';
        closeBtn.style.lineHeight = '1';
        closeBtn.style.opacity = '0.75';
        closeBtn.addEventListener('click', function(){
          try { dismissFetchFlashState(state || {}); } catch(_) {}
          try { clearFetchTarget(); setFetchSnapshot(null); clearBulkFetchTarget(); setBulkFetchSnapshot(null); } catch(_) {}
          try { localStorage.removeItem(BULK_FETCH_JOB_KEY); } catch(_) {}
          try { el.remove(); } catch(_) {}
        });

        el.replaceChildren(textNode, closeBtn);

        scheduleGlobalFetchFlashHide(0);
        try {
          if (el.__hideTimer) clearTimeout(el.__hideTimer);
        } catch (_) {}
        if (Number(timeoutMs) > 0) {
          el.__hideTimer = setTimeout(function(){
            try { el.remove(); } catch(_) {}
          }, timeoutMs);
        } else {
          // ttl === 0 means "persistent" flash: do not auto-remove
          el.__hideTimer = null;
        }
      }

      function upsertGlobalFetchProgressFlash(state){
        const status = String(state.status || 'not_started');
        if (status === 'not_started') {
          clearGlobalFetchProgressFlash();
          return;
        }
        if (isFetchFlashDismissed(state)) {
          clearGlobalFetchProgressFlash();
          return;
        }

        const container = ensureFlashContainer();
        if (!container) return;

        try {
          container.querySelectorAll('[data-progress-flash="1"]').forEach(function(node){
            if (node && node.id !== FETCH_FLASH_ID) node.remove();
          });
        } catch (_) {}

        let el = document.getElementById(FETCH_FLASH_ID);
        if (!el) {
          el = document.createElement('div');
          el.id = FETCH_FLASH_ID;
          el.setAttribute('data-flash', '');
          el.setAttribute('data-progress-flash', '1');
          container.prepend(el);
        } else {
          el.setAttribute('data-progress-flash', '1');
        }
        el.dataset.fetchFlashSignature = getFetchFlashSignature(state || {});

        const pct = Math.max(0, Math.min(100, Number(state.percent || 0)));
        const msg = String(state.message || '').trim();
        const isBulk = String(state.mode || '') === 'bulk' || String(state.job_id || '').trim() !== '';
        const credential = String(state.credential || '').trim();
        const vat = String(state.vat || '').trim();
        const currentCustomer = String(state.current_customer || '').trim();
        const currentIndex = Number(state.current_index || 0);
        const totalCustomers = Number(state.total_customers || 0);
        const who = isBulk
          ? (currentCustomer ? (' [' + currentCustomer + (totalCustomers ? ' (' + currentIndex + '/' + totalCustomers + ')' : '') + ']') : '')
          : ((credential || vat) ? (' [' + [credential, vat].filter(Boolean).join(' - ') + ']') : '');
        const baseText = (isBulk ? 'Μαζική λήψη: ' : 'Πρόοδος λήψης: ') + pct + '%';
        const terminalByMessage = /ολοκληρώθηκε|διακόπηκε|σταμάτησε|σταματησε|επιτυχίες\s*:|αποτυχίες\s*:|σφάλμα/i.test(msg);
        const runningLike = (status === 'running' || status === 'stopping') && !terminalByMessage;
        const fullText = runningLike
          ? (msg ? (baseText + who + ' — ' + msg) : (baseText + who))
          : ((msg || (status === 'error' ? 'Σφάλμα κατά τη λήψη.' : (isBulk ? 'Η μαζική λήψη ολοκληρώθηκε.' : 'Η λήψη ολοκληρώθηκε.'))) + who);

        if (!runningLike) {
          clearGlobalFetchProgressFlash();
          showStandardFetchFlash(fullText, status === 'error' ? 'error' : 'success', 5000, isBulk ? 'bulkFetchFinalFlash' : 'singleFetchFinalFlash', state);
          try {
            if (isBulk) {
              clearBulkFetchTarget();
              setBulkFetchSnapshot(null);
            } else {
              clearFetchTarget();
              setFetchSnapshot(null);
            }
          } catch (_) {}
          return;
        }

        el.className = 'flash-banner flash-info';
        el.setAttribute('data-ttl', '0');
        scheduleGlobalFetchFlashHide(0);

        el.style.display = 'flex';
        el.style.alignItems = 'center';
        el.style.justifyContent = 'space-between';
        el.style.gap = '0.75rem';
        el.style.pointerEvents = 'auto';

        const textNode = document.createElement('span');
        textNode.textContent = fullText;
        textNode.style.flex = '1 1 auto';
        el.replaceChildren(textNode);

        if (isBulk && runningLike) {
          const stopBtn = document.createElement('button');
          stopBtn.type = 'button';
          stopBtn.textContent = 'Διακοπή';
          stopBtn.className = 'px-3 py-1 rounded-md bg-red-600 hover:bg-red-700 text-white font-semibold border-0';
          stopBtn.disabled = status === 'stopping';
          stopBtn.addEventListener('click', function(){
            requestGlobalBulkFetchStop().catch(function(){});
          });
          el.appendChild(stopBtn);
        }
      }

      async function pollGlobalNotifs(){
        try{
          const res = await fetch('/api/global_notifications', {
            credentials:'same-origin',
            headers: {'X-Wait-Overlay': 'skip'}
          });
          if(res.ok){
            const data = await res.json();
            if(data.msgs && data.msgs.length){
              data.msgs.forEach(msg=>{
                const div = document.createElement('div');
                div.className = 'flash-banner flash-success';
                div.textContent = msg;
                div.setAttribute('data-flash','');
                div.setAttribute('data-ttl','6000');
                const container = ensureFlashContainer();
                if(container) container.prepend(div);
              });
            }
          }
        }catch(e){console.warn('notif poll failed', e);}
      }

      async function refreshLastFetchFromServer(target){
        if (!target || (!target.credential && !target.vat)) return;
        const qp = new URLSearchParams();
        if (target.credential) qp.set('credential', target.credential);
        if (target.vat) qp.set('vat', target.vat);
        try {
          const res = await fetch('/api/last_fetch_date?' + qp.toString(), {
            credentials:'same-origin',
            headers: {'X-Wait-Overlay': 'skip'}
          });
          if (!res.ok) return;
          const data = await res.json().catch(() => null);
          if (!data) return;
          const newText = data.last_fetch_date || 'δεν υπάρχει';
          const el = document.getElementById('lastFetchInfo');
          if (el && newText) {
            el.textContent = 'Τελευταία λήψη: ' + newText;
          }
        } catch (_) {}
      }

      async function pollGlobalFetchProgress(){
        const target = readFetchTarget();
        if (target && !readJson(FETCH_TARGET_KEY)) {
          setFetchTarget(target);
        }
        if (!target) {
          const snap = readFetchSnapshot();
          const bulkSnap = readBulkFetchSnapshot();
          if (bulkSnap && stateShouldRemainVisible(bulkSnap)) {
            upsertGlobalFetchProgressFlash(bulkSnap);
            return;
          }
          if (snap && stateShouldRemainVisible(snap)) {
            upsertGlobalFetchProgressFlash(snap);
            return;
          }
          if (snap && !stateShouldRemainVisible(snap)) setFetchSnapshot(null);
          if (bulkSnap && !stateShouldRemainVisible(bulkSnap)) setBulkFetchSnapshot(null);
          clearGlobalFetchProgressFlash();
          return;
        }
        await refreshLastFetchFromServer(target);

        const query = new URLSearchParams();
        if (target.credential) query.set('credential', target.credential);
        if (target.vat) query.set('vat', target.vat);
        if (!query.toString()) {
          clearFetchTarget();
          clearGlobalFetchProgressFlash();
          return;
        }

        try {
          const res = await fetch('/api/fetch_progress?' + query.toString(), {
            credentials:'same-origin',
            headers: {'X-Wait-Overlay': 'skip'}
          });
          if (!res.ok) return;
          const data = await res.json().catch(() => ({}));
          const state = {
            status: String(data.status || 'not_started'),
            percent: Number(data.percent || 0),
            message: String(data.message || ''),
            credential: target.credential,
            vat: target.vat,
          };

          setFetchSnapshot(state);
          upsertGlobalFetchProgressFlash(state);

          if (state.status === 'completed' || state.status === 'error' || state.status === 'not_started') {
            clearFetchTarget();
          }
        } catch (e) {
          console.warn('global fetch progress poll failed', e);
        }
      }

      async function pollGlobalBulkFetchProgress(){
        const target = readBulkFetchTarget();
        if (target && !readJson(BULK_FETCH_TARGET_KEY)) {
          setBulkFetchTarget(target);
        }
        if (!target || !target.job_id) {
          const snap = readBulkFetchSnapshot();
          const regularSnap = readFetchSnapshot();
          if (snap && stateShouldRemainVisible(snap)) {
            upsertGlobalFetchProgressFlash(snap);
            return;
          }
          if (regularSnap && stateShouldRemainVisible(regularSnap)) {
            upsertGlobalFetchProgressFlash(regularSnap);
            return;
          }
          if (snap && !stateShouldRemainVisible(snap)) setBulkFetchSnapshot(null);
          if (regularSnap && !stateShouldRemainVisible(regularSnap)) setFetchSnapshot(null);
          clearGlobalFetchProgressFlash();
          return;
        }

        try {
          const res = await fetch('/api/fetch_bulk/progress?job_id=' + encodeURIComponent(target.job_id), {
            credentials:'same-origin',
            headers: {'X-Wait-Overlay': 'skip'}
          });
          if (!res.ok) return;
          const data = await res.json().catch(() => ({}));
          const state = {
            status: String(data.status || 'not_started'),
            percent: Number(data.percent || 0),
            message: String(data.message || ''),
            mode: 'bulk',
            job_id: target.job_id,
            current_customer: String(data.current_customer || ''),
            current_index: Number(data.current_index || 0),
            total_customers: Number(data.total_customers || 0),
          };

          setBulkFetchSnapshot(state);
          upsertGlobalFetchProgressFlash(state);

          if (!['running', 'stopping'].includes(state.status)) {
            clearBulkFetchTarget();
          }
        } catch (e) {
          console.warn('global bulk fetch progress poll failed', e);
        }
      }

      window.__setGlobalFetchProgressTarget = function(payloadOrCredential, maybeVat){
        if (typeof payloadOrCredential === 'string') {
          setFetchTarget({ credential: payloadOrCredential, vat: maybeVat || '' });
          return;
        }
        setFetchTarget(payloadOrCredential || {});
      };

      window.__setGlobalFetchProgressSnapshot = function(state){
        setFetchSnapshot(state || null);
        if (state) upsertGlobalFetchProgressFlash(state);
      };

      window.__setGlobalBulkFetchTarget = function(payload){
        setBulkFetchTarget(payload || {});
      };

      window.__setGlobalBulkFetchSnapshot = function(state){
        setBulkFetchSnapshot(state || null);
        if (state) upsertGlobalFetchProgressFlash(state);
      };

      window.__clearGlobalBulkFetchTarget = function(){
        clearBulkFetchTarget();
        setBulkFetchSnapshot(null);
        const regularSnap = readFetchSnapshot();
        if (!regularSnap || !['running','stopping'].includes(String(regularSnap.status || ''))) {
          clearGlobalFetchProgressFlash();
        }
      };

      window.__clearGlobalFetchProgressTarget = function(){
        clearFetchTarget();
        setFetchSnapshot(null);
        const bulkSnap = readBulkFetchSnapshot();
        if (!bulkSnap || !['running','stopping'].includes(String(bulkSnap.status || ''))) {
          clearGlobalFetchProgressFlash();
        }
      };

      window.__dismissFetchFlashState = dismissFetchFlashState;
      window.__dismissFetchFlashSignature = dismissFetchFlashSignature;
      window.__isFetchFlashDismissed = isFetchFlashDismissed;
      window.__getFetchFlashSignature = getFetchFlashSignature;
      window.__clearDismissedFetchFlash = clearDismissedFetchFlash;

      ensureFlashContainer();

      const bootSnapshot = readFetchSnapshot();
      const bootBulkSnapshot = readBulkFetchSnapshot();
      if (bootBulkSnapshot && ['running', 'stopping'].includes(String(bootBulkSnapshot.status || ''))) {
        upsertGlobalFetchProgressFlash(bootBulkSnapshot);
      } else if (bootSnapshot && ['running', 'stopping'].includes(String(bootSnapshot.status || ''))) {
        upsertGlobalFetchProgressFlash(bootSnapshot);
      }

      window.addEventListener('storage', function(ev){
        if (!ev || !ev.key) return;
        if (ev.key === FETCH_SNAPSHOT_KEY) {
          const snap = readFetchSnapshot();
          const bulkSnap = readBulkFetchSnapshot();
          if (bulkSnap && ['running', 'stopping'].includes(String(bulkSnap.status || ''))) upsertGlobalFetchProgressFlash(bulkSnap);
          else if (snap) upsertGlobalFetchProgressFlash(snap);
          else clearGlobalFetchProgressFlash();
        }
        if (ev.key === BULK_FETCH_SNAPSHOT_KEY) {
          const bulkSnap = readBulkFetchSnapshot();
          if (bulkSnap) upsertGlobalFetchProgressFlash(bulkSnap);
          else {
            const snap = readFetchSnapshot();
            if (snap) upsertGlobalFetchProgressFlash(snap);
            else clearGlobalFetchProgressFlash();
          }
        }
        if (ev.key === FETCH_TARGET_KEY && !readFetchTarget()) {
          const snap = readFetchSnapshot();
          const bulkSnap = readBulkFetchSnapshot();
          if ((!snap || !['running','stopping'].includes(String(snap.status || ''))) && (!bulkSnap || !['running','stopping'].includes(String(bulkSnap.status || '')))) clearGlobalFetchProgressFlash();
        }
        if (ev.key === BULK_FETCH_TARGET_KEY && !readBulkFetchTarget()) {
          const snap = readFetchSnapshot();
          const bulkSnap = readBulkFetchSnapshot();
          if ((!snap || !['running','stopping'].includes(String(snap.status || ''))) && (!bulkSnap || !['running','stopping'].includes(String(bulkSnap.status || '')))) clearGlobalFetchProgressFlash();
        }
      });

      if (window.IS_LOGGED_IN) {
        pollGlobalNotifs();
        pollGlobalFetchProgress();
        pollGlobalBulkFetchProgress();
        setInterval(pollGlobalNotifs, 5000);
        setInterval(pollGlobalFetchProgress, 1500);
        setInterval(pollGlobalBulkFetchProgress, 1500);
      }
    })();
    
