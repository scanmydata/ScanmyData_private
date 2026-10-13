(function(){
  // ---------------------------------------------------------------------------------------------
  // Help / Support chat (δεξιά κάτω). Συνομιλία με το support (Discord thread) + αρχείο tickets.
  //
  // Σχεδιασμός (αντικαθιστά την παλιά έκδοση που ξανασχεδίαζε τα πάντα κάθε 2s):
  //   * ΕΝΑ αίτημα refresh τη φορά (σειριοποιημένο) — τα επικαλυπτόμενα αιτήματα έφερναν παλιές απαντήσεις
  //     και η κατάσταση «διαβάστηκε»/«πληκτρολογεί» άλλαζε πέρα-δώθε.
  //   * Επανασχεδίαση μόνο όταν αλλάζει κάτι (υπογραφή μηνυμάτων/κατάστασης)· η θέση κύλισης διατηρείται όταν
  //     ο χρήστης διαβάζει παλιότερα μηνύματα.
  //   * Κατάσταση παράδοσης μόνο κάτω από το ΤΕΛΕΥΤΑΙΟ μήνυμα του χρήστη (Αποστολή… / Παραλήφθηκε /
  //     Διαβάστηκε ΩΩ:ΛΛ), «πληκτρολογεί» ως φυσαλίδα με τελείες μέσα στη συνομιλία, με τοπική λήξη.
  //   * Ώρες, ημερομηνίες (Σήμερα/Χθες/…) και ετικέτες αποστολέα — και στο αρχείο tickets.
  //   * Χωρίς SSE: κάθε EventSource δέσμευε ένα thread του gunicorn (8 threads) για όσο ήταν ανοιχτή η
  //     σελίδα. Το polling (2s ανοιχτό / 20s κλειστό / 60s κρυφή καρτέλα) είναι αρκετό και ασφαλές.
  // ---------------------------------------------------------------------------------------------
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
  if(!panel || !box) return;

  let open=false;
  let stopped=false;
  let poller=null;
  let inFlight=false;
  let pendingRefresh=false;
  let lastPresence={support_typing:false, typing_remaining_ms:0};
  let typingUntilLocal=0;
  let typingTimer=null;
  let lastRenderSig='';
  let lastMessages=[];
  let lastTicket=null;
  let archivedTickets=[];
  let firstRender=true;
  let forceStickBottom=false;
  let unreadWhileScrolled=0;
  let newPill=null;

  const NAME_KEY='support_display_name';
  const SEEN_KEY='support_last_seen';
  const CLOSED_NOTICE_KEY='support_closed_notice_seen';
  const OPEN_POLL_MS=2000;
  const CLOSED_POLL_MS=20000;
  const HIDDEN_POLL_MS=60000;
  const DELIVERY_GRACE_MS=20000;

  // ------------------------------------------------------------------ helpers
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
  function parseTs(ts){ const v=Date.parse(ts||''); return Number.isFinite(v)?v:0; }

  const TZ='Europe/Athens';
  let _fmtDay=null,_fmtTime=null,_fmtCompact=null,_fmtLong=null;
  try{
    _fmtDay=new Intl.DateTimeFormat('en-CA',{timeZone:TZ,year:'numeric',month:'2-digit',day:'2-digit'});
    _fmtTime=new Intl.DateTimeFormat('el-GR',{timeZone:TZ,hour:'2-digit',minute:'2-digit',hour12:false});
    _fmtCompact=new Intl.DateTimeFormat('el-GR',{timeZone:TZ,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
    _fmtLong=new Intl.DateTimeFormat('el-GR',{timeZone:TZ,weekday:'long',day:'numeric',month:'long',year:'numeric'});
  }catch(_e){}
  function dayKey(iso){ const d=new Date(iso); if(isNaN(d)) return ''; try{ return _fmtDay?_fmtDay.format(d):d.toISOString().slice(0,10);}catch(_e){return d.toISOString().slice(0,10);} }
  function timeLabel(iso){ const d=new Date(iso); if(isNaN(d)) return ''; try{ return _fmtTime?_fmtTime.format(d):`${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;}catch(_e){return '';} }
  function formatGreekCompact(iso){
    if(!iso) return '';
    const d=new Date(iso); if(isNaN(d)) return String(iso);
    try{ return (_fmtCompact?_fmtCompact.format(d):d.toLocaleString('el-GR')).replace(',', ''); }catch(_e){ return String(iso); }
  }
  function dayLabel(iso){
    const k=dayKey(iso); if(!k) return '';
    const today=dayKey(new Date().toISOString());
    const yest=dayKey(new Date(Date.now()-86400000).toISOString());
    if(k===today) return 'Σήμερα';
    if(k===yest) return 'Χθες';
    const d=new Date(iso);
    try{ return _fmtLong?_fmtLong.format(d):d.toLocaleDateString('el-GR'); }catch(_e){ return k; }
  }
  function whoLabel(m, ticket){
    if(m.sender==='support') return 'Support';
    const n = String((ticket && (ticket.display_name||ticket.username)) || '').trim();
    return n ? `${n} (εσύ)` : 'Εσύ';
  }

  // ------------------------------------------------------------------ thread rendering (live + archive)
  function attachmentsHtml(m){
    const a=(m.attachments||[]).map(x=>{
      const href=esc(x.url||'#'); const nm=esc(x.name||'attachment');
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">📎 ${nm}</a>`;
    }).join('');
    return a?`<div class="help-attachments">${a}</div>`:'';
  }
  function statusHtml(m){
    if(!m || m.sender!=='user') return '';
    if(m.read_by_support_at){
      return `<div class="hb-status hb-status--read" title="Διαβάστηκε από το support">✓✓ Διαβάστηκε ${esc(timeLabel(m.read_by_support_at))}</div>`;
    }
    if(m.discord_delivered_at || m.discord_delivered){
      return '<div class="hb-status hb-status--delivered" title="Το μήνυμα έφτασε στο support">✓ Παραλήφθηκε</div>';
    }
    const age = Date.now()-parseTs(m.timestamp);
    if(age>DELIVERY_GRACE_MS){
      return '<div class="hb-status hb-status--failed" title="Το μήνυμα αποθηκεύτηκε αλλά δεν έχει παραδοθεί στο support">⚠ Δεν παραδόθηκε — θα ξαναδοκιμάσει με το επόμενο μήνυμα</div>';
    }
    return '<div class="hb-status hb-status--pending">⏳ Αποστολή…</div>';
  }
  function buildThread(messages, ticket, opts){
    opts=opts||{};
    const msgs=(messages||[]).slice();
    let html='';
    let prevDay='', prevSender='';
    let lastUserIdx=-1;
    msgs.forEach((m,i)=>{ if(m.sender==='user') lastUserIdx=i; });
    msgs.forEach((m,i)=>{
      const dk=dayKey(m.timestamp);
      if(dk && dk!==prevDay){
        html+=`<div class="hb-day"><span>${esc(dayLabel(m.timestamp))}</span></div>`;
        prevDay=dk; prevSender='';
      }
      const side=m.sender==='support'?'support':'user';
      const showWho=(side!==prevSender);
      prevSender=side;
      html+=`<div class="hb-row hb-row--${side}">`
        +(showWho?`<div class="hb-who">${esc(whoLabel(m,ticket))}</div>`:'')
        +`<div class="help-msg ${side}">${esc(m.content||'')}${attachmentsHtml(m)}</div>`
        +`<div class="hb-time">${esc(timeLabel(m.timestamp))}</div>`
        +((!opts.archive && i===lastUserIdx)?statusHtml(m):'')
        +`</div>`;
    });
    if(!msgs.length){
      html='<div class="hb-empty">Δεν υπάρχουν μηνύματα.</div>';
    }
    if(!opts.archive && opts.typing){
      html+='<div class="hb-row hb-row--support hb-row--typing"><div class="hb-who">Support</div>'
        +'<div class="help-msg support hb-typing" aria-label="Ο support πληκτρολογεί"><span></span><span></span><span></span></div></div>';
    }
    return html;
  }

  function isTyping(){
    return !!(lastTicket && String(lastTicket.status||'open').toLowerCase()==='open' && typingUntilLocal>Date.now());
  }
  function scheduleTypingExpiry(){
    try{ if(typingTimer) clearTimeout(typingTimer); }catch(_e){}
    typingTimer=null;
    const left=typingUntilLocal-Date.now();
    if(left>0){
      typingTimer=setTimeout(()=>{ renderLive(); }, left+50);
    }
  }
  function nearBottom(){
    return (box.scrollHeight - box.scrollTop - box.clientHeight) < 70;
  }
  function ensureNewPill(){
    if(newPill) return newPill;
    newPill=document.createElement('button');
    newPill.type='button';
    newPill.className='hb-newpill';
    newPill.style.display='none';
    newPill.textContent='↓ Νέο μήνυμα';
    newPill.addEventListener('click',()=>{ box.scrollTop=box.scrollHeight; unreadWhileScrolled=0; newPill.style.display='none'; });
    if(box.parentNode) box.parentNode.insertBefore(newPill, box.nextSibling);
    return newPill;
  }
  box.addEventListener('scroll',()=>{
    if(newPill && nearBottom()){ unreadWhileScrolled=0; newPill.style.display='none'; }
  });

  function renderLive(){
    const messages=lastTicket?lastMessages:[];
    const typing=isTyping();
    // η υπογραφή περιλαμβάνει ό,τι φαίνεται: μηνύματα, ώρες, παράδοση/ανάγνωση, πληκτρολόγηση, κατάσταση ticket
    const lastUser=[...messages].reverse().find(m=>m.sender==='user');
    const ageBucket=lastUser?Math.min(2,Math.floor((Date.now()-parseTs(lastUser.timestamp))/DELIVERY_GRACE_MS)):0;
    const sig=JSON.stringify([
      messages.map(m=>[m.id,m.timestamp,m.read_by_support_at||0,m.discord_delivered?1:0,(m.attachments||[]).length]),
      typing?1:0, lastTicket?lastTicket.status:'', lastTicket?lastTicket.id:0, ageBucket
    ]);
    if(sig===lastRenderSig) return;
    const prevCount = (()=>{ try{ return JSON.parse(lastRenderSig||'[[]]')[0].length; }catch(_e){ return 0; } })();
    const wasNear = nearBottom() || firstRender || forceStickBottom;
    const prevTop = box.scrollTop;
    lastRenderSig=sig;
    box.innerHTML=buildThread(messages,lastTicket,{typing});
    if(wasNear){
      box.scrollTop=box.scrollHeight;
      unreadWhileScrolled=0;
      if(newPill) newPill.style.display='none';
    } else {
      box.scrollTop=prevTop;
      const incoming = messages.length - prevCount;
      if(incoming>0 && messages[messages.length-1] && messages[messages.length-1].sender==='support'){
        unreadWhileScrolled+=incoming;
        const p=ensureNewPill(); p.textContent = unreadWhileScrolled>1?`↓ ${unreadWhileScrolled} νέα μηνύματα`:'↓ Νέο μήνυμα'; p.style.display='';
      }
    }
    firstRender=false;
    forceStickBottom=false;
    scheduleTypingExpiry();
  }

  // ------------------------------------------------------------------ header / badge / name
  function updateNameFieldState(ticket){
    if(!nameInput) return;
    const hasLockedName=!!(ticket && (ticket.id || ticket.display_name));
    if(hasLockedName){
      const who=String(ticket.display_name || nameInput.value || '').trim();
      if(who) nameInput.value=who;
      nameInput.disabled=true;
      nameInput.style.display='none';
    } else {
      nameInput.disabled=false;
      nameInput.style.display='';
    }
  }
  function updateTitle(ticket){
    if(!titleEl) return;
    try{
      if(endBtn){
        endBtn.style.display = (!ticket || String(ticket.status||'').toLowerCase()!=='open') ? 'none' : '';
      }
    }catch(_e){}
    if(!ticket){
      titleEl.textContent='Νέα συνομιλία';
      if(threadLabel) threadLabel.textContent = archivedTickets.length ? `Αρχειοθετημένα tickets: ${archivedTickets.length}` : 'Συμπλήρωσε όνομα και ξεκίνα συνομιλία';
      return;
    }
    const status=String(ticket.status||'open')==='closed'?'Κλειστό':'Ανοικτό';
    titleEl.textContent=`Ticket #${ticket.id} · ${status}`;
    if(threadLabel){
      const who=String(ticket.display_name || ticket.username || 'Χρήστης');
      threadLabel.textContent=`Συνομιλία: ${who}`;
    }
  }
  function updateBadge(messages){
    if(!badge) return;
    let lastSeenRaw=''; try{ lastSeenRaw=localStorage.getItem(SEEN_KEY) || ''; }catch(_e){}
    const lastSeen=parseTs(lastSeenRaw);
    const incoming=(messages||[]).filter(m=>m.sender==='support' && parseTs(m.timestamp)>lastSeen).length;
    if(incoming>0 && !open){
      badge.style.display='inline-flex';
      badge.textContent=incoming>99?'99+':String(incoming);
      fab && fab.classList.add('has-unread');
      fab && fab.setAttribute('title', `Help / Support (${incoming} νέο${incoming===1?'':'α'} μήνυμα${incoming===1?'':'τα'})`);
    } else {
      badge.style.display='none';
      fab && fab.classList.remove('has-unread');
      fab && fab.setAttribute('title','Help / Support');
    }
  }
  function markSeen(messages){
    const last=(messages||[]).slice().reverse().find(m=>m.sender==='support');
    if(last && last.timestamp){
      try{ localStorage.setItem(SEEN_KEY, String(last.timestamp)); }catch(_e){}
    }
    updateBadge(messages||[]);
  }
  function updateSelectedFiles(){
    if(!fileStateEl) return;
    const f=(fileInput && fileInput.files)?Array.from(fileInput.files):[];
    fileStateEl.textContent = f.length ? `Επισυναπτόμενα: ${f.map(x=>x.name).join(', ')}` : '';
  }

  // ------------------------------------------------------------------ polling
  function pollInterval(){
    if(open) return OPEN_POLL_MS;
    if(document.hidden) return HIDDEN_POLL_MS;
    return CLOSED_POLL_MS;
  }
  function schedulePoll(){
    try{ if(poller) clearTimeout(poller); }catch(_e){}
    if(stopped) return;
    poller=setTimeout(async ()=>{ await refresh(); schedulePoll(); }, pollInterval());
  }
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden){ refresh(); } schedulePoll(); });

  async function refresh(){
    if(inFlight){ pendingRefresh=true; return; }
    inFlight=true;
    try{
      const r=await fetch(`/api/support/ticket/me?mark_read=${open?'1':'0'}`,{credentials:'same-origin'});
      if(r.status===401||r.status===403){
        try{ if(poller) clearTimeout(poller); }catch(_){}
        poller=null;
        stopped=true;
        return;
      }
      if(!r.ok) return;
      const j=await r.json();
      const prevTicket=lastTicket;
      lastTicket=j.ticket||null;
      archivedTickets=Array.isArray(j.archived_tickets)?j.archived_tickets:[];
      lastPresence=j.presence||{support_typing:false, typing_remaining_ms:0};
      const rem=Number(lastPresence.typing_remaining_ms||0);
      typingUntilLocal = (lastPresence.support_typing && rem>0) ? Date.now()+rem : 0;
      lastMessages=lastTicket?(j.messages||[]):[];
      updateTitle(lastTicket);
      updateNameFieldState(lastTicket);
      renderLive();
      updateBadge(lastMessages);

      if(!lastTicket && prevTicket && input){ input.value=''; }

      if(j.closed_notice && open){
        const refTicket=j.latest_closed_ticket||{};
        const noticeKey=`${CLOSED_NOTICE_KEY}:${refTicket.id||'0'}:${refTicket.closed_at||''}`;
        let already=null; try{ already=localStorage.getItem(noticeKey); }catch(_e){}
        if(!already){
          const byRaw=String(refTicket.closed_by||'support');
          const by=(byRaw==='admin'||byRaw==='admin/support')?'admin/support':byRaw;
          try{ await showModalAlert('Ενημέρωση', `Το ticket έκλεισε από ${by}. Η συνομιλία αρχειοθετήθηκε και μπορείς να τη δεις από το Αρχείο.`); }catch(_){}
          try{ localStorage.setItem(noticeKey,'1'); }catch(_e){}
        }
      }
      if(open) markSeen(lastMessages);
    }catch(_e){
    }finally{
      inFlight=false;
      if(pendingRefresh){ pendingRefresh=false; setTimeout(refresh,50); }
    }
  }

  // ------------------------------------------------------------------ send / close
  async function sendMsg(msg){
    const displayName=(nameInput&&nameInput.value||'').trim();
    const files=(fileInput && fileInput.files)?Array.from(fileInput.files):[];
    if(!displayName){ throw new Error('Δήλωσε πρώτα όνομα support.'); }
    if(!msg && !files.length){ throw new Error('Γράψε μήνυμα ή πρόσθεσε αρχείο.'); }
    try{ localStorage.setItem(NAME_KEY, displayName); }catch(_e){}
    const fd=new FormData();
    fd.append('message', msg || '');
    fd.append('display_name', displayName);
    files.forEach(f=>fd.append('files', f));
    const r=await fetch('/api/support/ticket/open',{method:'POST',credentials:'same-origin',body:fd});
    const j=await r.json().catch(()=>({}));
    if(!r.ok || !j.ok) throw new Error((j&&j.error)||'Σφάλμα αποστολής');
    if(j.discord_delivered===false){ throw new Error('Το ticket αποθηκεύτηκε αλλά απέτυχε η αποστολή στο Discord.'); }
    if(fileInput) fileInput.value='';
    updateSelectedFiles();
  }

  // ------------------------------------------------------------------ archive (list + single ticket)
  function closeModal(modal){
    try{ panel && panel.classList.remove('minimized'); }catch(_e){}
    try{ modal.remove(); }catch(_e){}
  }
  function closedByLabel(by){
    const v=String(by||'').toLowerCase();
    if(v==='admin'||v==='support'||v==='admin/support') return 'από το support';
    if(v==='user'||v==='') return 'από εσένα';
    return `από ${by}`;
  }
  function ticketPeriod(t){
    const a=t.created_at?formatGreekCompact(t.created_at):'';
    const b=t.closed_at?formatGreekCompact(t.closed_at):'';
    if(a && b) return `${a} → ${b}`;
    return b||a||'';
  }

  async function openArchivedTicket(ticketId){
    const r=await fetch(`/api/support/ticket/history/${encodeURIComponent(ticketId)}`,{credentials:'same-origin'});
    const j=await r.json().catch(()=>({}));
    if(!r.ok || !j.ok) throw new Error((j&&j.error)||'Αδυναμία φόρτωσης αρχείου ticket');
    try{ panel && panel.classList.add('minimized'); }catch(_e){}
    const t=j.ticket||{};
    const msgs=Array.isArray(j.messages)?j.messages:[];
    const modal=document.createElement('div');
    modal.id='archivedTicketModal';
    modal.className='hb-modal-bg';
    const meta=[
      t.display_name ? esc(t.display_name) : '',
      ticketPeriod(t) ? esc(ticketPeriod(t)) : '',
      t.closed_at ? `Έκλεισε ${esc(closedByLabel(t.closed_by))}` : 'Ανοικτό',
      `${msgs.length} μηνύματα`
    ].filter(Boolean).join(' · ');
    modal.innerHTML=`
      <div class="hb-modal" role="dialog" aria-label="Ticket #${esc(t.id||ticketId)}">
        <div class="hb-modal-head">
          <div>
            <div class="hb-modal-title">Ticket #${esc(t.id||ticketId)}</div>
            <div class="hb-modal-sub">${meta}</div>
          </div>
          <button type="button" class="hb-x" id="closeArchivedTicketModal" aria-label="Κλείσιμο">×</button>
        </div>
        <div class="hb-modal-body hb-thread">${buildThread(msgs,t,{archive:true})}</div>
        <div class="hb-modal-foot">
          <button type="button" class="hb-btn hb-btn--warn" id="reportArchivedTicket">Αναφορά</button>
          <button type="button" class="hb-btn hb-btn--danger" id="deleteArchivedTicket">Διαγραφή</button>
          <button type="button" class="hb-btn" id="closeArchivedTicketModal2">Κλείσιμο</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    const body=modal.querySelector('.hb-modal-body'); if(body) body.scrollTop=body.scrollHeight;
    modal.querySelector('#closeArchivedTicketModal')?.addEventListener('click',()=>closeModal(modal));
    modal.querySelector('#closeArchivedTicketModal2')?.addEventListener('click',()=>closeModal(modal));
    modal.addEventListener('click',(e)=>{ if(e.target===modal) closeModal(modal); });

    modal.querySelector('#reportArchivedTicket')?.addEventListener('click', async ()=>{
      try{
        const excerpt=msgs.map(m=>`${m.sender==='support'?'Support':'Χρήστης'}: ${m.content||''}`).join('\n').slice(0,1200);
        try{ if(nameInput && t.display_name) nameInput.value=t.display_name; }catch(_e){}
        if(input) input.value=`Αναφορά βασισμένη στο αρχειοθετημένο ticket #${ticketId}:\n\n${excerpt}`;
        try{ setOpen(true); panel && panel.classList.remove('minimized'); }catch(_e){}
        input && input.focus();
        modal.remove();
      }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
    });
    modal.querySelector('#deleteArchivedTicket')?.addEventListener('click', async ()=>{
      try{
        if(!await showModalConfirm('Διαγραφή Ticket', `Θες σίγουρα να διαγράψεις το αρχειοθετημένο ticket #${ticketId}; Η ενέργεια δεν μπορεί να αναιρεθεί.`,'Διαγραφή','Άκυρο')) return;
        const r2=await fetch(`/api/support/ticket/delete/${encodeURIComponent(ticketId)}`,{method:'POST',credentials:'same-origin'});
        const j2=await r2.json().catch(()=>({}));
        if(!r2.ok || !j2.ok) throw new Error((j2&&j2.error)||'Αποτυχία διαγραφής');
        archivedTickets=(archivedTickets||[]).filter(x=>String(x.id)!==String(ticketId));
        try{ await showModalAlert('Επιτυχία','Το ticket διαγράφηκε.'); }catch(_){}
        closeModal(modal);
      }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
    });
  }

  async function openArchiveList(){
    if(!archivedTickets.length){
      try{ await showModalAlert('Ενημέρωση','Δεν υπάρχουν αρχειοθετημένα tickets.'); }catch(_){}
      return;
    }
    try{ panel && panel.classList.add('minimized'); }catch(_e){}
    const modal=document.createElement('div');
    modal.id='archiveModal';
    modal.className='hb-modal-bg';
    function rowsHtml(list){
      return list.map(t=>{
        const period=ticketPeriod(t);
        const last=t.last_message||null;
        const prev=last?`${last.sender==='support'?'Support':'Εσύ'}: ${String(last.content||'').slice(0,90)}`:'';
        const by=String(t.closed_by||'').toLowerCase();
        const cls=(by==='admin'||by==='support'||by==='admin/support')?'hb-ticket--support':'hb-ticket--user';
        return `
          <div class="hb-ticket ${cls}" data-open-ticket="${esc(t.id)}" tabindex="0" role="button">
            <div class="hb-ticket-main">
              <div class="hb-ticket-title">Ticket #${esc(t.id)}${t.display_name?` · ${esc(t.display_name)}`:''}</div>
              <div class="hb-ticket-sub">${esc(period)}${period?' · ':''}Έκλεισε ${esc(closedByLabel(t.closed_by))}${t.message_count!=null?` · ${esc(t.message_count)} μηνύματα`:''}</div>
              ${prev?`<div class="hb-ticket-prev">${esc(prev)}</div>`:''}
            </div>
            <div class="hb-ticket-actions">
              <button type="button" class="hb-btn hb-btn--warn hb-btn--sm" data-report-ticket="${esc(t.id)}">Αναφορά</button>
              <button type="button" class="hb-btn hb-btn--danger hb-btn--sm" data-delete-ticket="${esc(t.id)}">Διαγραφή</button>
            </div>
          </div>`;
      }).join('') || '<div class="hb-empty">Κανένα αποτέλεσμα.</div>';
    }
    modal.innerHTML=`
      <div class="hb-modal hb-modal--list" role="dialog" aria-label="Αρχειοθετημένα tickets">
        <div class="hb-modal-head">
          <div>
            <div class="hb-modal-title">Αρχειοθετημένα tickets</div>
            <div class="hb-modal-sub">${archivedTickets.length} tickets</div>
          </div>
          <button type="button" class="hb-x" id="closeArchiveListModal" aria-label="Κλείσιμο">×</button>
        </div>
        ${archivedTickets.length>5?'<div class="hb-modal-search"><input type="search" id="hbArchiveSearch" placeholder="Αναζήτηση σε tickets…"></div>':''}
        <div class="hb-modal-body" id="hbArchiveRows">${rowsHtml(archivedTickets)}</div>
      </div>`;
    document.body.appendChild(modal);
    const rowsEl=modal.querySelector('#hbArchiveRows');
    modal.querySelector('#closeArchiveListModal')?.addEventListener('click',()=>closeModal(modal));
    modal.addEventListener('click',(e)=>{ if(e.target===modal) closeModal(modal); });
    const search=modal.querySelector('#hbArchiveSearch');
    if(search){
      search.addEventListener('input',()=>{
        const q=search.value.trim().toLowerCase();
        const list=!q?archivedTickets:archivedTickets.filter(t=>
          `#${t.id} ${t.display_name||''} ${(t.last_message&&t.last_message.content)||''} ${ticketPeriod(t)}`.toLowerCase().includes(q));
        rowsEl.innerHTML=rowsHtml(list);
      });
    }
    rowsEl.addEventListener('click', async (e)=>{
      const del=e.target.closest('[data-delete-ticket]');
      const rep=e.target.closest('[data-report-ticket]');
      const row=e.target.closest('[data-open-ticket]');
      if(del){
        e.stopPropagation();
        const tid=del.getAttribute('data-delete-ticket');
        try{
          const ok=await showModalConfirm('Διαγραφή Ticket', `Θες σίγουρα να διαγράψεις το αρχειοθετημένο ticket #${tid}; Η ενέργεια δεν μπορεί να αναιρεθεί.`,'Διαγραφή','Άκυρο');
          if(!ok) return;
          const r=await fetch(`/api/support/ticket/delete/${encodeURIComponent(tid)}`,{method:'POST',credentials:'same-origin'});
          const j=await r.json().catch(()=>({}));
          if(!r.ok || !j.ok) throw new Error((j&&j.error)||'Αποτυχία διαγραφής');
          archivedTickets=(archivedTickets||[]).filter(t=>String(t.id)!==String(tid));
          del.closest('[data-open-ticket]')?.remove();
          try{ await showModalAlert('Επιτυχία','Το ticket διαγράφηκε.'); }catch(_){}
        }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
        return;
      }
      if(rep){
        e.stopPropagation();
        const tid=rep.getAttribute('data-report-ticket');
        try{
          const r=await fetch(`/api/support/ticket/history/${encodeURIComponent(tid)}`,{credentials:'same-origin'});
          const j=await r.json().catch(()=>({}));
          if(!r.ok || !j.ok) throw new Error((j&&j.error)||'Αδύναμη φόρτωση ticket');
          const msgs=Array.isArray(j.messages)?j.messages:[];
          const excerpt=msgs.map(m=>`${m.sender==='support'?'Support':'Χρήστης'}: ${m.content||''}`).join('\n').slice(0,1200);
          try{ if(nameInput && (j.ticket && j.ticket.display_name)) nameInput.value=j.ticket.display_name; }catch(_e){}
          if(input) input.value=`Αναφορά βασισμένη στο αρχειοθετημένο ticket #${tid}:\n\n${excerpt}`;
          try{ setOpen(true); panel && panel.classList.remove('minimized'); }catch(_e){}
          input && input.focus();
          modal.remove();
        }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
        return;
      }
      if(row){
        const tid=row.getAttribute('data-open-ticket');
        try{ await openArchivedTicket(tid); }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
      }
    });
    rowsEl.addEventListener('keydown',(e)=>{
      if((e.key==='Enter'||e.key===' ') && e.target.matches && e.target.matches('[data-open-ticket]')){
        e.preventDefault();
        openArchivedTicket(e.target.getAttribute('data-open-ticket')).catch(()=>{});
      }
    });
  }

  // ------------------------------------------------------------------ open/close + events
  function setOpen(v){
    open=!!v;
    if(panel) panel.style.display=open?'flex':'none';
    if(open){
      firstRender=true;
      forceStickBottom=true;
      refresh();
    }
    schedulePoll();
  }

  fab&&fab.addEventListener('click',()=>setOpen(!open));
  fileInput&&fileInput.addEventListener('change',updateSelectedFiles);
  try{
    const savedName=localStorage.getItem(NAME_KEY) || '';
    if(savedName && nameInput) nameInput.value=savedName;
  }catch(_e){}
  updateNameFieldState(null);
  closeBtn&&closeBtn.addEventListener('click',()=>setOpen(false));
  archiveBtn&&archiveBtn.addEventListener('click',()=>openArchiveList());

  refresh();
  schedulePoll();

  endBtn&&endBtn.addEventListener('click', async ()=>{
    if(!await showModalConfirm('Επιβεβαίωση','Να λήξει η συνομιλία;')) return;
    try{
      const r=await fetch('/api/support/ticket/close',{method:'POST',credentials:'same-origin'});
      const j=await r.json().catch(()=>({}));
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
      forceStickBottom=true;
      await refresh();
    }catch(err){ try{ await showModalAlert('Σφάλμα', err.message||String(err)); }catch(_){} }
  });
})();
