  (function(){
    const overlay = document.getElementById('syncProgressOverlay');
    const panel = document.getElementById('syncProgressPanel');
    const bar = document.getElementById('syncProgressBar');
    const pctText = document.getElementById('syncProgressPercentText');
    const msg = document.getElementById('syncProgressMessage');
    let pollTimer = null;
    // Non-blocking mode: after login we land the user in the app and only show a
    // small progress card in the corner (no full-screen backdrop) so they can
    // keep working while the sync runs.
    let nonBlocking = false;

    function applyNonBlockingStyles(){
      if(!overlay) return;
      // Overlay becomes a transparent, click-through container; the panel is a
      // fixed bottom-right card that still receives interaction.
      overlay.style.background = 'transparent';
      overlay.style.pointerEvents = 'none';
      overlay.style.alignItems = 'flex-end';
      overlay.style.justifyContent = 'flex-end';
      if(panel){
        panel.style.pointerEvents = 'auto';
        panel.style.width = '320px';
        panel.style.maxWidth = '90vw';
        panel.style.margin = '0 18px 18px 0';
      }
    }

    function showOverlay(){ if(overlay) overlay.style.display='flex'; }
    function hideOverlay(){ if(overlay) overlay.style.display='none'; }

    function updateProgress(o){
      try{
        const p = Math.max(0, Math.min(100, parseInt(o.percent||0)));
        if(bar) bar.style.width = p + '%';
        if(pctText) pctText.textContent = p + '%';
        if(msg) msg.textContent = o.message || (o.status||'');
        if(p >= 100 || (o.status||'') === 'done'){
          // finished
          setTimeout(()=>{ hideOverlay(); }, 500);
          return true;
        }
        return false;
      }catch(e){ return false; }
    }

    async function poll(){
      try{
        const r = await fetch('/api/sync_progress', { credentials: 'same-origin' });
        // If we're not authenticated, stop polling to avoid repeated 401 noise.
        if(r.status === 401 || r.status === 403){
          if(pollTimer){ clearInterval(pollTimer); pollTimer = null; }
          hideOverlay();
          return true;
        }
        if(!r.ok) return false;
        const j = await r.json().catch(()=> null);
        if(!j) return false;
        // Treat 'running'/'syncing' as active. 'not_started' is harmless and should not show overlay.
        const running = (j.status && (j.status === 'running' || j.status === 'syncing')) || (j.percent && j.percent < 100 && j.status !== 'disabled' && j.status !== 'done');
        if(running){
          showOverlay();
          const finished = updateProgress(j);
          return finished;
        }
        return false;
      }catch(e){ return false; }
    }

    // Start polling on DOMContentLoaded only on login/sync pages; avoid polling site-wide
    document.addEventListener('DOMContentLoaded', function(){
      // Only enable polling when on a login page or an explicit sync-start page
      // '/auth/login' used to be listed here too, but auth_bp has no url_prefix
      // so that form action never existed -- '/login' is the real one.
      const loginFormPresent = !!document.querySelector('form[action="/firebase-auth/login"], form[action="/login"]');
      const onSyncPage = window.location.pathname && window.location.pathname.indexOf('/firebase-auth/sync') === 0;
      const explicitFlag = document.body && document.body.dataset && document.body.dataset.sync === '1';
      // Post-login: the server appends ?_sync=1 when it kicked off a background
      // pull. Show progress non-blocking (corner card) and drop the flag from the
      // URL so a refresh doesn't keep re-triggering it.
      const syncQuery = /[?&]_sync=1(?:&|$)/.test(window.location.search || '');
      if (syncQuery) {
        nonBlocking = true;
        applyNonBlockingStyles();
        try {
          const u = new URL(window.location.href);
          u.searchParams.delete('_sync');
          history.replaceState(null, '', u.pathname + (u.search ? u.search : '') + u.hash);
        } catch(_){}
      }
      if (!loginFormPresent && !onSyncPage && !explicitFlag && !syncQuery) {
        // Do not start polling globally
        return;
      }

      // quick initial poll and then periodic
      (async function tick(){
        try{
          const done = await poll();
          if(done) return;
        }catch(_){ }
        // poll every 1s while running, otherwise every 5s to detect new runs
        pollTimer = setInterval(async function(){
          try{
            const done = await poll();
            if(done && pollTimer){ clearInterval(pollTimer); pollTimer = null; }
          }catch(_){ }
        }, 1000);
      })();
    });
  })();
