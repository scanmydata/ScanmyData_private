    // ============================================================================
    // Κεντρικό σύστημα flash μηνυμάτων (φορτώνεται ΕΚΤΟΣ #appShell, μία φορά).
    //
    //  - window.showFlash(message, type, ttl): ενιαίο API για όλες τις σελίδες
    //    (πριν υπήρχε μόνο σε κάποιες και αλλού τα μηνύματα χάνονταν σιωπηλά).
    //    Ίδιο κείμενο που ήδη φαίνεται δεν στοιβάζεται δεύτερη φορά· ανανεώνεται.
    //  - Το #flashContainer ζει έξω από το #appShell, άρα τα μηνύματα μένουν
    //    ορατά όταν ο χρήστης αλλάζει σελίδα με partial navigation.
    //  - Σε πλήρη μετάβαση/ανανέωση, όσα μηνύματα είναι ακόμη ορατά συνεχίζουν
    //    στην επόμενη σελίδα για τον υπόλοιπο χρόνο τους (sessionStorage).
    //    Στην αποσύνδεση/σελίδα σύνδεσης σβήνονται.
    //  - Flash που έκανε ο server μέσα σε AJAX/JSON αίτημα έρχεται στο header
    //    X-App-Flash και εμφανίζεται αμέσως (αντί να περιμένει στο session και
    //    να βγει αργότερα π.χ. στο logout).
    // ============================================================================
    (function(){
      if (window.__appFlashCenter) return;
      var STORE_KEY = 'scanmydata:pendingFlashes:v1';
      var DEFAULT_TTL = 6000;
      var MAX_REPLAY_AGE_MS = 60000;
      var skipPersist = false;

      function isLoggedIn(){ return window.IS_LOGGED_IN === true; }

      function container(){
        var c = document.getElementById('flashContainer');
        if (c) return c;
        c = document.createElement('div');
        c.id = 'flashContainer';
        c.className = 'space-y-2 mb-4';
        (document.body || document.documentElement).appendChild(c);
        return c;
      }

      function kindClass(type){
        var t = String(type || '').toLowerCase();
        if (t === 'error' || t === 'danger') return 'flash-error';
        if (t === 'warning' || t === 'warn') return 'flash-warning';
        if (t === 'info' || t === 'afm' || t === 'message') return 'flash-info';
        return 'flash-success';
      }

      function typeFromClass(el){
        var cl = (el && el.classList) || { contains: function(){ return false; } };
        if (cl.contains('flash-error')) return 'error';
        if (cl.contains('flash-warning')) return 'warning';
        if (cl.contains('flash-info')) return 'info';
        return 'success';
      }

      function normText(s){
        return String(s == null ? '' : s).replace(/[×✕✖]/g, '').replace(/\s+/g, ' ').trim();
      }

      function htmlToText(html){
        var d = document.createElement('div');
        d.innerHTML = String(html || '');
        return d.textContent || '';
      }

      function bannerText(el){
        if (!el) return '';
        if (el.dataset && el.dataset.flashText) return el.dataset.flashText;
        var clone = el.cloneNode(true);
        try { clone.querySelectorAll('button').forEach(function(b){ b.remove(); }); } catch (_) {}
        return normText(clone.textContent);
      }

      function findDuplicate(text){
        var t = normText(text);
        if (!t) return null;
        var c = document.getElementById('flashContainer');
        if (!c) return null;
        var nodes = c.querySelectorAll('.flash-banner');
        for (var i = 0; i < nodes.length; i++) {
          var n = nodes[i];
          if (n.hasAttribute('data-progress-flash')) continue;
          if (bannerText(n) === t) return n;
        }
        return null;
      }

      // Ένας μόνο timer ανά banner (κοινός με τα makeDismissable των base_01/base_08),
      // ώστε η ανανέωση ενός διπλότυπου να μην κόβεται από παλιό timer.
      function armTtl(el, ttl){
        if (!el) return;
        try { if (el.__appFlashTimer) clearTimeout(el.__appFlashTimer); } catch (_) {}
        el.__appFlashTimer = null;
        var ms = Number(ttl);
        if (!(ms > 0)) ms = 0;
        el.setAttribute('data-ttl', String(ms));
        el.setAttribute('data-shown-at', String(Date.now()));
        if (ms > 0) {
          el.__appFlashTimer = setTimeout(function(){ try { el.remove(); } catch (_) {} }, ms);
        }
      }

      function show(message, type, ttl, opts){
        opts = opts || {};
        var html = opts.html ? String(opts.html) : '';
        var text = html ? normText(htmlToText(html)) : normText(message);
        if (!text) return null;
        var ttlMs = (ttl === undefined || ttl === null || ttl === '') ? DEFAULT_TTL : (Number(ttl) || 0);

        var dup = findDuplicate(text);
        if (dup) {
          dup.classList.remove('flash-success', 'flash-error', 'flash-warning', 'flash-info');
          dup.classList.add(kindClass(type));
          armTtl(dup, ttlMs);
          return dup;
        }

        var el = document.createElement('div');
        el.className = 'flash-banner ' + kindClass(type);
        el.setAttribute('role', 'alert');
        el.setAttribute('data-flash', '');
        el.dataset.flashDynamic = '1';
        el.dataset.flashText = text;
        // Ίδια attributes με τον τοπικό showFlash της Αναζήτησης, ώστε η δική
        // του αφαίρεση διπλοτύπων να «βλέπει» και τα μηνύματα που βγάζει το base.
        el.dataset.message = text;
        el.dataset.type = String(type || 'info');
        if (html) el.innerHTML = html; else el.textContent = String(message);
        container().appendChild(el);
        armTtl(el, ttlMs);
        return el;
      }

      // ---------------- συνέχεια σε πλήρη μετάβαση σελίδας ----------------
      function readStore(){
        try {
          var raw = sessionStorage.getItem(STORE_KEY);
          var list = raw ? JSON.parse(raw) : [];
          return Array.isArray(list) ? list : [];
        } catch (_) { return []; }
      }
      function writeStore(list){
        try {
          if (!list || !list.length) sessionStorage.removeItem(STORE_KEY);
          else sessionStorage.setItem(STORE_KEY, JSON.stringify(list.slice(-8)));
        } catch (_) {}
      }

      function collectVisible(){
        var out = [];
        var c = document.getElementById('flashContainer');
        if (!c) return out;
        var now = Date.now();
        c.querySelectorAll('.flash-banner').forEach(function(n){
          if (n.hasAttribute('data-progress-flash') || n.hasAttribute('data-no-persist')) return;
          if (n.style && n.style.display === 'none') return;
          var ttl = Number(n.getAttribute('data-ttl') || 0);
          if (!(ttl > 0)) return; // τα «μόνιμα» (ttl 0) τα ξαναχτίζει η σελίδα/ο poller τους
          var shown = Number(n.getAttribute('data-shown-at') || 0) || now;
          var remaining = ttl - (now - shown);
          if (remaining < 1200) return;
          var clone = n.cloneNode(true);
          try { clone.querySelectorAll('button').forEach(function(b){ b.remove(); }); } catch (_) {}
          var html = String(clone.innerHTML || '').trim();
          if (!normText(clone.textContent)) return;
          out.push({ html: html, type: typeFromClass(n), ttl: Math.max(2500, Math.round(remaining)), ts: now });
        });
        return out;
      }

      function persistOnLeave(){
        if (skipPersist || !isLoggedIn()) { writeStore([]); return; }
        writeStore(collectVisible());
      }

      function replayStored(){
        var list = readStore();
        writeStore([]);
        if (!isLoggedIn()) return;
        var now = Date.now();
        list.forEach(function(item){
          if (!item || !item.html) return;
          if (now - Number(item.ts || 0) > MAX_REPLAY_AGE_MS) return;
          show(null, item.type, item.ttl, { html: item.html });
        });
      }

      // ---------------- flash από τον server σε AJAX απαντήσεις ----------------
      function handleFlashHeader(value){
        if (!value) return;
        var list;
        try { list = JSON.parse(decodeURIComponent(String(value))); } catch (_) { return; }
        if (!Array.isArray(list)) return;
        list.forEach(function(item){
          if (!item) return;
          var cat = Array.isArray(item) ? item[0] : item.category;
          var msg = Array.isArray(item) ? item[1] : item.message;
          if (!msg) return;
          // Τα server flash αποδίδονται με |safe και στο base.html — ίδια μεταχείριση.
          show(null, cat, DEFAULT_TTL, { html: String(msg) });
        });
      }

      try {
        var origFetch = window.fetch;
        if (typeof origFetch === 'function' && !origFetch.__appFlashWrapped) {
          var wrappedFetch = function(){
            return origFetch.apply(this, arguments).then(function(resp){
              try {
                var h = resp && resp.headers && resp.headers.get && resp.headers.get('X-App-Flash');
                if (h) handleFlashHeader(h);
              } catch (_) {}
              return resp;
            });
          };
          wrappedFetch.__appFlashWrapped = true;
          window.fetch = wrappedFetch;
        }
      } catch (_) {}

      try {
        var XHR = window.XMLHttpRequest;
        if (XHR && XHR.prototype && !XHR.prototype.__appFlashWrapped) {
          var origSend = XHR.prototype.send;
          XHR.prototype.send = function(){
            try {
              this.addEventListener('load', function(){
                try { handleFlashHeader(this.getResponseHeader('X-App-Flash')); } catch (_) {}
              });
            } catch (_) {}
            return origSend.apply(this, arguments);
          };
          XHR.prototype.__appFlashWrapped = true;
        }
      } catch (_) {}

      // ---------------- partial navigation ----------------
      // Η σελίδα που φέρνει το partial nav έχει ήδη «καταναλώσει» τα server flash
      // του session και τα έχει μέσα στο δικό της #flashContainer: τα μεταφέρουμε
      // στο ζωντανό container (αλλιώς χάνονταν).
      function adoptFromDocument(doc){
        if (!doc) return;
        var src = doc.getElementById('flashContainer');
        if (!src) return;
        src.querySelectorAll('.flash-banner').forEach(function(n){
          var ttl = n.getAttribute('data-ttl');
          show(null, typeFromClass(n), ttl === null ? DEFAULT_TTL : Number(ttl), { html: n.innerHTML });
        });
      }

      function stampInitial(){
        var c = document.getElementById('flashContainer');
        if (!c) return;
        c.querySelectorAll('.flash-banner').forEach(function(n){
          if (!n.hasAttribute('data-shown-at')) n.setAttribute('data-shown-at', String(Date.now()));
        });
      }

      window.__appFlashCenter = true;
      window.__appShowFlash = show;
      window.showFlash = show;
      window.__appFlashArmTtl = armTtl;
      window.__appFlashAdoptFrom = adoptFromDocument;
      window.__appFlashHandleHeader = handleFlashHeader;

      // Αποσύνδεση: ό,τι είναι ορατό δεν μεταφέρεται στη σελίδα σύνδεσης.
      document.addEventListener('click', function(ev){
        try {
          var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
          if (!a) return;
          var u = new URL(a.getAttribute('href'), window.location.href);
          if (/\/(api\/)?logout\/?$/.test(u.pathname)) { skipPersist = true; writeStore([]); }
        } catch (_) {}
      }, true);
      window.addEventListener('pagehide', persistOnLeave);
      // Επιστροφή από bfcache: η σελίδα έχει ήδη τα μηνύματά της ζωντανά.
      window.addEventListener('pageshow', function(ev){ if (ev && ev.persisted) writeStore([]); });

      stampInitial();
      if (isLoggedIn()) replayStored(); else writeStore([]);
    })();
