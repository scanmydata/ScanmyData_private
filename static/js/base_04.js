(function(){
  const fab=document.getElementById('helpBotFab');
  const panel=document.getElementById('helpBotPanel');
  const closeBtn=document.getElementById('helpBotClose');
  const endBtn=document.getElementById('helpBotEnd');
  const archiveBtn=document.getElementById('helpBotArchive');
  const titleEl=document.getElementById('helpBotTitle');
  const badge=document.getElementById('helpBotBadge');
  const threadLabel=document.getElementById('helpBotThreadLabel');
  const presenceEl=document.getElementById('helpBotPresence');
  const fileStateEl=document.getElementById('helpBotFileState');
  const form=document.getElementById('helpBotForm');
  const input=document.getElementById('helpBotInput');
  const nameInput=document.getElementById('helpBotName');
  const fileInput=document.getElementById('helpBotFile');
  const box=document.getElementById('helpBotMessages');
  let open=false;
  let poller=null; // will be set to a timer that adapts depending on `open`
  let supportSse = null;
  let supportSseConnected = false;
  let supportSseRetry = null;
  let lastPresence={support_typing:false};
  const NAME_KEY='support_display_name';
  const SEEN_KEY='support_last_seen';
  const CLOSED_NOTICE_KEY='support_closed_notice_seen';
  let lastTicket=null;
  let archivedTickets=[];

  // Track the timestamp of the last support message we’ve seen.
  // When the panel is closed we poll slowly; if a new support message arrives,
  // we speed up polling for a short while so the user sees new replies quickly.
  let lastSupportMsgTs = 0;
  let closedFastUntil = 0;
  const CLOSED_POLL_INTERVAL = 30000; // 30s when panel closed and idle
  const FAST_POLL_INTERVAL = 2000; // 2s when panel open or when new message arrived
  const FAST_POLL_AFTER_NEW_MS = 30000; // keep fast polling for N seconds after new incoming message

  // Adjust polling interval depending on whether the panel is open (faster when open).
  // When the panel is closed we avoid regular polling if we can get push events via SSE.
  function _setPollIntervalForOpenState(){
    try{ if(poller) clearInterval(poller); }catch(_e){}

    const now = Date.now();
    const shouldFastPoll = open || now < closedFastUntil;
    if(shouldFastPoll){
      poller = setInterval(refresh, FAST_POLL_INTERVAL);
      return;
    }

    // If we have a working SSE connection, we can avoid polling entirely when closed.
    if(supportSseConnected){
      poller = null;
      return;
    }

    poller = setInterval(refresh, CLOSED_POLL_INTERVAL);
  }

  function initSupportSse(){
    if (!window.EventSource) return;
    try{
      supportSse = new EventSource('/api/support/events');
      supportSse.addEventListener('open', () => {
        supportSseConnected = true;
        _setPollIntervalForOpenState();
      });
      supportSse.addEventListener('error', () => {
        supportSseConnected = false;
        // fallback to polling when SSE cannot stay open
        _setPollIntervalForOpenState();
      });
      supportSse.addEventListener('message', (evt) => {
        try{
          const data = JSON.parse(evt.data || '{}');
          if (data && data.type === 'support_reply') {
            // Only refresh when new support message is available
            if (!open) {
              refresh();
            }
            closedFastUntil = Date.now() + FAST_POLL_AFTER_NEW_MS;
            if (!open) {
              _setPollIntervalForOpenState();
            }
          }
        }catch(_){ }
      });
    }catch(_){ }
  }

  function updateNameFieldState(ticket){
    if(!nameInput) return;
    const hasLockedName = !!(ticket && (ticket.id || ticket.display_name));
    if(hasLockedName){
      const who = String(ticket.display_name || nameInput.value || '').trim();
      if(who) nameInput.value = who;
      nameInput.disabled = true;
      nameInput.style.display = 'none';
      if(endBtn) endBtn.style.display = '';
    } else {
      nameInput.disabled = false;
      nameInput.style.display = '';
      if(endBtn) endBtn.style.display = 'none';
    }
  }

  function updateNameFieldState(ticket){
    if(!nameInput) return;
    const hasLockedName = !!(ticket && (ticket.id || ticket.display_name));
    if(hasLockedName){
      const who = String(ticket.display_name || nameInput.value || '').trim();
      if(who) nameInput.value = who;
      nameInput.disabled = true;
      nameInput.style.display = 'none';
    } else {
      nameInput.disabled = false;
      nameInput.style.display = '';
    }
  }

  function esc(s){return String(s||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');}
  function formatStatus(m){
    if(m.sender!=='user') return '';
    if(m.read_by_support_at) return 'Διαβάστηκε από support';
    if(m.discord_delivered_at || m.discord_delivered) return 'Παραλήφθηκε από Discord';
    return 'Σε αναμονή αποστολής στο Discord';
  }
  function render(messages){
    if(!box) return;
    box.innerHTML = (messages||[]).map(m=>{
      const attachments = (m.attachments||[]).map(a=>{
        const href = esc(a.url||'#');
        const nm = esc(a.name||'attachment');
        return `<a href="${href}" target="_blank" rel="noopener noreferrer">📎 ${nm}</a>`;
      }).join('');
      const meta = formatStatus(m);
      return `<div class="help-msg ${m.sender==='support'?'support':'user'}">${esc(m.content||'')}${attachments?`<div class="help-attachments">${attachments}</div>`:''}${meta?`<div class="help-meta">${esc(meta)}</div>`:''}</div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
  }

  function parseTs(ts){
    const v = Date.parse(ts||'');
    return Number.isFinite(v) ? v : 0;
  }

  // Format ISO timestamp into compact Greek date/time (dd/mm/YYYY HH:MM), using Europe/Athens timezone
  function formatGreekCompact(iso){
    if(!iso) return '';
    try{
      const dt = new Date(iso);
      if (isNaN(dt)) return iso;
      // use Intl to render in Europe/Athens consistently
      try{
        const fmt = new Intl.DateTimeFormat('el-GR', { timeZone: 'Europe/Athens', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
        return fmt.format(dt).replace(',', '');
      }catch(e){
        const pad = n => String(n).padStart(2,'0');
        return `${pad(dt.getDate())}/${pad(dt.getMonth()+1)}/${dt.getFullYear()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
      }
    }catch(e){ return iso; }
  }

  function updatePresence(){
    if(!presenceEl) return;
    if(lastPresence && lastPresence.support_typing){
      presenceEl.textContent = 'Ο support agent πληκτρολογεί…';
    } else {
      presenceEl.textContent = '';
    }
  }

  function updateBadge(messages){
    if(!badge) return;
    const lastSeen = parseTs(localStorage.getItem(SEEN_KEY) || '');
    const incoming = (messages||[]).filter(m => m.sender==='support' && parseTs(m.timestamp) > lastSeen).length;
    if(incoming > 0 && !open){
      badge.style.display='inline-flex';
      badge.textContent = incoming > 99 ? '99+' : String(incoming);
      fab && fab.classList.add('has-unread');
      fab && fab.setAttribute('title', `Help / Support (${incoming} νέο${incoming===1?'':'α'} μήνυμα${incoming===1?'':'τα'})`);
    } else {
      badge.style.display='none';
      fab && fab.classList.remove('has-unread');
      fab && fab.setAttribute('title', 'Help / Support');
    }
  }

  function markSeen(messages){
    const last = (messages||[]).slice().reverse().find(m => m.sender==='support');
    if(last && last.timestamp){
      try{ localStorage.setItem(SEEN_KEY, String(last.timestamp)); }catch(_e){}
    }
    updateBadge(messages||[]);
  }

  function updateTitle(ticket){
    if(!titleEl) return;
    // hide 'Λήξη' when there is no active/open ticket
    try{
      if(endBtn){
        if(!ticket || String(ticket.status||'').toLowerCase() !== 'open') endBtn.style.display = 'none';
        else endBtn.style.display = '';
      }
    }catch(_e){}

    if(!ticket){
      titleEl.textContent = 'Νέα συνομιλία';
      if(threadLabel) threadLabel.textContent = archivedTickets.length ? `Αρχειοθετημένα tickets: ${archivedTickets.length}` : 'Συμπλήρωσε όνομα και ξεκίνα συνομιλία';
      return;
    }
    const status = String(ticket.status||'open') === 'closed' ? 'Κλειστό' : 'Ανοικτό';
    titleEl.textContent = `Ticket #${ticket.id} · ${status}`;
    if(threadLabel){
      const who = String(ticket.display_name || ticket.username || 'Χρήστης');
      threadLabel.textContent = `Συνομιλία: ${who}`;
    }
  }

  function updateSelectedFiles(){
    if(!fileStateEl) return;
    const f = (fileInput && fileInput.files) ? Array.from(fileInput.files) : [];
    if(!f.length){ fileStateEl.textContent=''; return; }
    fileStateEl.textContent = `Επισυναπτόμενα: ${f.map(x=>x.name).join(', ')}`;
  }

  async function refresh(){
    try{
      const r=await fetch(`/api/support/ticket/me?mark_read=${open?'1':'0'}`,{credentials:'same-origin'});
      if(r.status===401||r.status===403){
        try{ if(poller) clearInterval(poller); }catch(_){ }
        if(supportSse){ try{ supportSse.close(); }catch(_){ } }
        supportSseConnected=false;
        supportSse = null;
        return;
      }
      if(!r.ok) return;
      const j=await r.json();
      const prevTicket = lastTicket;                  // preserve previous state
      lastTicket = j.ticket || null;
      archivedTickets = Array.isArray(j.archived_tickets) ? j.archived_tickets : [];
      lastPresence = j.presence || {support_typing:false};
      updateTitle(lastTicket);
      updateNameFieldState(lastTicket);
      const messages = lastTicket ? (j.messages||[]) : [];

      // Detect new support messages (when panel is closed we poll slowly, but
      // if a new message arrives we speed up polling briefly for better UX)
      const supportMsgs = (messages||[]).filter(m => m.sender === 'support');
      if (supportMsgs.length) {
        const maxTs = Math.max(...supportMsgs.map(m => parseTs(m.timestamp) || 0));
        if (maxTs > lastSupportMsgTs) {
          lastSupportMsgTs = maxTs;
          closedFastUntil = Date.now() + FAST_POLL_AFTER_NEW_MS;
          // If panel is closed but we detected a new support message, refresh more often
          if (!open) {
            try{ _setPollIntervalForOpenState(); }catch(_e){}
          }
        }
      }

      render(messages);
      updateBadge(messages);

      // Only clear the input if we *had* an open ticket and it was just closed.
      if (!lastTicket && prevTicket && input) {
        input.value = '';
      }

      if (j.closed_notice && open) {
        const refTicket = j.latest_closed_ticket || {};
        const noticeKey = `${CLOSED_NOTICE_KEY}:${refTicket.id || '0'}:${refTicket.closed_at || ''}`;
        const already = localStorage.getItem(noticeKey);
        if(!already){
          const byRaw = String(refTicket.closed_by || 'support');
          const by = (byRaw === 'admin' || byRaw === 'admin/support') ? 'admin/support' : byRaw;
          try { await showModalAlert('Ενημέρωση', `Το ticket έκλεισε από ${by}. Η συνομιλία αρχειοθετήθηκε και μπορείς να τη δεις από το Αρχείο.`); } catch(_) {}
          localStorage.setItem(noticeKey, '1');
        }
      }
      updatePresence();
      if(open){ markSeen(messages); }
    }catch(_e){}
  }

  async function sendMsg(msg){
    const displayName=(nameInput&&nameInput.value||'').trim();
    const files = (fileInput && fileInput.files) ? Array.from(fileInput.files) : [];
    if(!displayName){ throw new Error('Δήλωσε πρώτα όνομα support.'); }
    if(!msg && !files.length){ throw new Error('Γράψε μήνυμα ή πρόσθεσε αρχείο.'); }
    try{ localStorage.setItem(NAME_KEY, displayName); }catch(_e){}

    const fd = new FormData();
    fd.append('message', msg || '');
    fd.append('display_name', displayName);
    files.forEach(f => fd.append('files', f));

    const r=await fetch('/api/support/ticket/open',{
      method:'POST',
      credentials:'same-origin',
      body:fd
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok || !j.ok) throw new Error((j&&j.error)||'Σφάλμα αποστολής');
    if(j.discord_delivered===false){ throw new Error('Το ticket αποθηκεύτηκε αλλά απέτυχε η αποστολή στο Discord.'); }
    if(fileInput) fileInput.value='';
    updateSelectedFiles();
  }

  async function openArchivedTicket(ticketId){
    const r = await fetch(`/api/support/ticket/history/${encodeURIComponent(ticketId)}`, { credentials:'same-origin' });
    const j = await r.json().catch(()=>({}));
    if(!r.ok || !j.ok) throw new Error((j&&j.error)||'Αδυναμία φόρτωσης αρχείου ticket');

    // minimize the live conversation while viewing archived ticket
    try{ panel && panel.classList.add('minimized'); }catch(_e){}
    const modal = document.createElement('div');
    modal.id = 'archivedTicketModal';
    modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-[100000]';
    const msgs = Array.isArray(j.messages) ? j.messages : [];
    const msgHtml = msgs.map(m => `<div class="help-msg ${m.sender==='support'?'support':'user'}">${esc(m.content||'')}</div>`).join('') || '<div class="text-sm text-gray-500">Δεν υπάρχουν μηνύματα.</div>';
    modal.innerHTML = `
      <div class="bg-white rounded-lg p-4 w-[92vw] max-w-2xl max-h-[80vh] overflow-auto shadow-lg">
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-semibold">Αρχειοθετημένο Ticket #${esc(j.ticket?.id || ticketId)}${j.ticket && j.ticket.closed_at ? ` · ${esc(formatGreekCompact(j.ticket.closed_at))}` : ''}</h3>
          <div class="flex gap-2">
            <button class="px-2 py-1 border rounded bg-yellow-50 text-yellow-800 border-yellow-100 dark:bg-yellow-600 dark:text-yellow-50 dark:border-yellow-600 archived-btn-report" id="reportArchivedTicket">Αναφορά</button>
            <button class="btn btn-sm btn-danger" id="deleteArchivedTicket">Διαγραφή</button>
            <button class="px-2 py-1 border rounded bg-gray-50 border-gray-100 dark:bg-gray-800/20 dark:text-gray-200 dark:border-gray-700" id="closeArchivedTicketModal">Κλείσιμο</button>
          </div>
        </div>
        <div>${msgHtml}</div>
      </div>
    `;
    document.body.appendChild(modal);

    modal.querySelector('#closeArchivedTicketModal')?.addEventListener('click', ()=>{ try{ panel && panel.classList.remove('minimized'); }catch(_e){}; modal.remove(); });
    modal.addEventListener('click', (e)=>{ if(e.target===modal){ try{ panel && panel.classList.remove('minimized'); }catch(_e){}; modal.remove(); } });

    // Report: prefill new message in the live support panel with archived conversation
    modal.querySelector('#reportArchivedTicket')?.addEventListener('click', async ()=>{
      try{
        const excerpt = msgs.map(m => `${m.sender==='support' ? 'Support' : 'User'}: ${m.content || ''}`).join('\n').slice(0,1200);
        try{ if(nameInput && (j.ticket && j.ticket.display_name)) nameInput.value = j.ticket.display_name; }catch(_e){}
        if(input) input.value = `Αναφορά βασισμένη στο αρχειοθετημένο ticket #${ticketId}:\n\n${excerpt}`;
        try{ setOpen(true); panel && panel.classList.remove('minimized'); }catch(_e){}
        input && input.focus();
        modal.remove();
      }catch(err){ try{ await showModalAlert('Σφάλμα', err.message || String(err)); }catch(_){} }
    });

    // Delete: call server API to permanently delete archived ticket
    modal.querySelector('#deleteArchivedTicket')?.addEventListener('click', async ()=>{
      try{
        if(!await showModalConfirm('Διαγραφή Ticket', `Θες σίγουρα να διαγράψεις το αρχειοθετημένο ticket #${ticketId}; Η ενέργεια δεν μπορεί να αναιρεθεί.`,'Διαγραφή','Άκυρο')) return;
        const r = await fetch(`/api/support/ticket/delete/${encodeURIComponent(ticketId)}`, { method: 'POST', credentials: 'same-origin' });
        const j2 = await r.json().catch(()=>({}));
        if(!r.ok || !j2.ok) throw new Error((j2&&j2.error) || 'Αποτυχία διαγραφής');
        archivedTickets = (archivedTickets||[]).filter(t => String(t.id) !== String(ticketId));
        try{ await showModalAlert('Επιτυχία', 'Το ticket διαγράφηκε.'); }catch(_){}
        try{ panel && panel.classList.remove('minimized'); }catch(_e){}
        modal.remove();
      }catch(err){ try{ await showModalAlert('Σφάλμα', err.message || String(err)); }catch(_){} }
    });
  }

  async function openArchiveList(){
    if(!archivedTickets.length){
      try{ await showModalAlert('Ενημέρωση', 'Δεν υπάρχουν αρχειοθετημένα tickets.'); }catch(_){}
      return;
    }
    // minimize the open conversation while the archive modal is visible
    try{ panel && panel.classList.add('minimized'); }catch(_e){}
    const modal = document.createElement('div');
    modal.id = 'archiveModal';
    modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-[100000]';
    const isDarkTheme = (document.documentElement && document.documentElement.getAttribute('data-theme') === 'dark');
    const listHtml = archivedTickets.map(t => {
      const when = t.closed_at ? ` · ${esc(formatGreekCompact(t.closed_at))}` : '';
      const by = String(t.closed_by || '').toLowerCase();
      const cls = (by === 'admin' || by === 'support' || by === 'admin/support') ? 'archived-ticket archived-ticket--support' : 'archived-ticket archived-ticket--user';
      const reportStyle = isDarkTheme ? 'background:#D97706;color:#FFF7ED;border-color:#D97706' : '';
      const deleteStyle = isDarkTheme ? 'background:#DC2626;color:#FFF1F2;border-color:#DC2626' : '';
      return `
        <div class="flex items-center justify-between px-3 py-2 border rounded ${cls} hover:bg-gray-50 dark:hover:bg-gray-900/40">
          <button data-ticket-id="${esc(t.id)}" class="text-left flex-1 text-sm" style="background:transparent;color:inherit;border:0;padding:0">Ticket #${esc(t.id)}${when}</button>
          <div class="ml-3 flex gap-2">
            <button data-report-ticket="${esc(t.id)}" class="px-2 py-1 text-xs border rounded archived-btn-report">Αναφορά</button>
            <button data-delete-ticket="${esc(t.id)}" class="btn btn-sm btn-danger">Διαγραφή</button>
          </div>
        </div>`;
    }).join('');

    const listContainerClass = (archivedTickets.length > 20) ? 'space-y-2 max-h-[60vh] overflow-auto' : 'space-y-2';

    modal.innerHTML = `
      <div class="bg-white rounded-lg p-4 w-[92vw] max-w-lg shadow-lg">
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-semibold">Αρχειοθετημένα Tickets</h3>
          <button class="px-2 py-1 border rounded" id="closeArchiveListModal">Κλείσιμο</button>
        </div>
        <div class="${listContainerClass}">${listHtml}</div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.querySelector('#closeArchiveListModal')?.addEventListener('click', ()=>{ try{ panel && panel.classList.remove('minimized'); }catch(_e){}; modal.remove(); });
    modal.addEventListener('click', (e)=>{ if(e.target===modal){ try{ panel && panel.classList.remove('minimized'); }catch(_e){}; modal.remove(); } });
    modal.querySelectorAll('[data-ticket-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const tid = btn.getAttribute('data-ticket-id');
        try { await openArchivedTicket(tid); } catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
      });
    });

    modal.querySelectorAll('[data-report-ticket]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const tid = btn.getAttribute('data-report-ticket');
        try{
          const r = await fetch(`/api/support/ticket/history/${encodeURIComponent(tid)}`, { credentials:'same-origin' });
          const j = await r.json().catch(()=>({}));
          if(!r.ok || !j.ok) { throw new Error((j&&j.error) || 'Αδύναμη φόρτωση ticket'); }
          const msgs = Array.isArray(j.messages) ? j.messages : [];
          const excerpt = msgs.map(m => `${m.sender==='support' ? 'Support' : 'User'}: ${m.content || ''}`).join('\n').slice(0, 1200);
          try{ if(nameInput && (j.ticket && j.ticket.display_name)) nameInput.value = j.ticket.display_name; }catch(_e){}
          if(input) input.value = `Αναφορά βασισμένη στο αρχειοθετημένο ticket #${tid}:\n\n${excerpt}`;
          try{ setOpen(true); panel && panel.classList.remove('minimized'); }catch(_e){}
          input && input.focus();
          modal.remove();
        }catch(err){ try{ await showModalAlert('Σφάλμα', err.message || String(err)); }catch(_){} }
      });
    });

    modal.querySelectorAll('[data-delete-ticket]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const tid = btn.getAttribute('data-delete-ticket');
        try{
          const ok = await showModalConfirm('Διαγραφή Ticket', `Θες σίγουρα να διαγράψεις το αρχειοθετημένο ticket #${tid}; Η ενέργεια δεν μπορεί να αναιρεθεί.`,'Διαγραφή','Άκυρο');
          if(!ok) return;
          const r = await fetch(`/api/support/ticket/delete/${encodeURIComponent(tid)}`, { method:'POST', credentials:'same-origin' });
          const j = await r.json().catch(()=>({}));
          if(!r.ok || !j.ok) throw new Error((j&&j.error) || 'Αποτυχία διαγραφής');
          // remove from local array + DOM
          archivedTickets = (archivedTickets||[]).filter(t => String(t.id) !== String(tid));
          btn.closest('div')?.remove();
          try{ await showModalAlert('Επιτυχία', 'Το ticket διαγράφηκε.'); }catch(_){}
        }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
      });
    });
  }

  function setOpen(v){
    open = !!v;
    if(panel) panel.style.display = open ? 'flex' : 'none';
    // adapt poller interval for responsiveness when the panel is open
    try{ _setPollIntervalForOpenState(); }catch(_e){}
    if(open){
      refresh();
      setTimeout(()=>markSeen([]), 50);
    }
  }

  fab&&fab.addEventListener('click',()=>setOpen(!open));
  fileInput&&fileInput.addEventListener('change', updateSelectedFiles);
  try{
    const savedName = localStorage.getItem(NAME_KEY) || '';
    if(savedName && nameInput) nameInput.value = savedName;
  }catch(_e){}
  updateNameFieldState(null);
  closeBtn&&closeBtn.addEventListener('click',()=>setOpen(false));
  archiveBtn&&archiveBtn.addEventListener('click', ()=>openArchiveList());

  initSupportSse();
  refresh();
  // start poller (slower when panel closed, faster while open)
  _setPollIntervalForOpenState();
  endBtn&&endBtn.addEventListener('click', async ()=>{
    if(!await showModalConfirm('Επιβεβαίωση', 'Να λήξει η συνομιλία;')) return;
    try{
      const r = await fetch('/api/support/ticket/close', { method:'POST', credentials:'same-origin' });
      const j = await r.json().catch(()=>({}));
      if(!r.ok || !j.ok) throw new Error((j&&j.error)||'Αποτυχία λήξης ticket');
      await refresh();
    }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
  });
  form&&form.addEventListener('submit', async (e)=>{
    e.preventDefault();
    const msg=(input&&input.value||'').trim();
    try{
      await sendMsg(msg);
      if(input) input.value='';
      await refresh();
      markSeen([]);
    }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
  });
})();
