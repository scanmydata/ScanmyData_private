// ============================================================================
// ScanmyData — Ξενάγηση (page tour)
//  - window.SmdTour.start('full' | 'intro' | 'groups' | 'credentials' | 'fetch' | 'search' | 'e3' | 'ar')
//  - Φορτώνεται μία φορά στο base.html (έξω από το #appShell), άρα επιβιώνει του partial navigation.
//  - Η κατάσταση (ποιο βήμα) κρατιέται στο localStorage, ώστε να συνεχίζει και μετά από πλήρη φόρτωση σελίδας.
//  - Αυτόματη εκκίνηση: μία φορά για νέους λογαριασμούς (window.SMD_USER από το base.html)· το «το είδα» κρατιέται στο localStorage.
//  - Βήματα χωρίς ορατό στόχο εμφανίζονται κεντραρισμένα (δεν μπλοκάρει ποτέ η ξενάγηση).
// ============================================================================
(function () {
  'use strict';
  if (window.SmdTour) return;

  var LS_KEY = 'smd_tour_state_v1';
  var MAX_AGE_MS = 6 * 60 * 60 * 1000;
  var PARTIAL_PATHS = ['/', '/fetch', '/credentials', '/search', '/e3_check', '/accounting_result'];

  // ---- Περιεχόμενο ---------------------------------------------------------
  // path: σελίδα στην οποία ανήκει το βήμα (null = οποιαδήποτε).  sel: CSS selector στόχου (ή array εναλλακτικών).
  var SECTIONS = {
    intro: { label: 'Γνωριμία', path: null, steps: [
      { title: 'Καλώς ήρθατε στο ScanmyData 👋',
        body: 'Σε λίγα λεπτά θα δείτε πώς δουλεύει η εφαρμογή: από τη λήψη παραστατικών myDATA μέχρι τον έλεγχο Ε3 και το λογιστικό αποτέλεσμα.<br><br>Μπορείτε να παραλείψετε την ξενάγηση οποιαδήποτε στιγμή και να την ξαναδείτε από <b>☰ Μενού → Οδηγός χρήσης</b>.' },
      { sel: ['.layout-header__nav', '#sideMenuToggle'], place: 'bottom',
        title: 'Οι κύριες ενότητες',
        body: 'Από εδώ μετακινείστε σε όλη την εφαρμογή:<br>📥 <b>Λήψη</b> παραστατικών myDATA · 🔍 <b>Αναζήτηση MARK</b> και χαρακτηρισμός · 📋 <b>Έλεγχος E3</b> · 🧮 <b>Λογιστικό Αποτέλεσμα</b> · 👥 <b>Πελάτες</b>.' },
      { sel: '.layout-header__active-group', place: 'bottom',
        title: 'Ενεργή ομάδα',
        body: 'Η <b>ομάδα</b> είναι ο χώρος εργασίας του γραφείου σας: όσα καταχωρείτε τα βλέπουν και τα μέλη της. Πατήστε εδώ για να αλλάξετε ομάδα.' },
      { sel: '.layout-header__active-customer', place: 'bottom',
        title: 'Ενεργός πελάτης',
        body: 'Όλες οι σελίδες δουλεύουν για τον <b>ενεργό πελάτη</b> (όνομα και ΑΦΜ). Τον αλλάζετε από τη σελίδα «Πελάτες».' },
      { sel: '#fiscalYearSelect', place: 'bottom',
        title: 'Χρήση (έτος)',
        body: 'Επιλέγετε τη διαχειριστική χρήση στην οποία αναφέρονται οι λήψεις, οι λίστες και οι έλεγχοι.' },
      { sel: '.theme-switch', place: 'bottom',
        title: 'Φωτεινό / σκούρο θέμα',
        body: 'Αλλάξτε εμφάνιση με έναν διακόπτη· η επιλογή σας αποθηκεύεται στη συσκευή.' },
      { sel: '#sideMenu a[href="/manual"]', place: 'left',
        menu: true, enter: function () { openSideMenu(); }, leave: function () { closeSideMenu(); },
        title: 'Μενού και Οδηγός χρήσης',
        body: 'Από το <b>☰ Μενού</b> βρίσκετε τις ομάδες, τις ρυθμίσεις λογαριασμού (2FA, κωδικός) και τον <b>Οδηγό χρήσης</b> με αναλυτικές οδηγίες και δυνατότητα να ξαναδείτε οποιαδήποτε ενότητα.' },
      { sel: '#helpBotFab', place: 'left',
        title: 'Υποστήριξη',
        body: 'Χρειάζεστε βοήθεια; Ανοίξτε τη συνομιλία support, στείλτε μήνυμα ή συνημμένο και θα σας απαντήσουμε εδώ.' }
    ] },

    groups: { label: 'Ομάδες', path: '/groups', steps: [
      { sel: '#gp-how', place: 'bottom', title: 'Ομάδες: ο χώρος εργασίας',
        body: 'Τρία βήματα: <b>επιλέγετε ομάδα</b>, <b>επιλέγετε πελάτη</b>, και δουλεύετε μαζί με τους συναδέλφους σας στα ίδια δεδομένα.' },
      { sel: ['[data-select-group]', '#open-create'], place: 'bottom', title: 'Επιλογή ομάδας',
        body: 'Πατήστε «Επιλογή ομάδας» στην κάρτα της ομάδας στην οποία θέλετε να δουλέψετε. Η ενεργή ομάδα φαίνεται πάντα στην κεφαλίδα.' },
      { sel: '#open-create', place: 'bottom', title: 'Νέα ομάδα',
        body: 'Δημιουργήστε δική σας ομάδα· θα είστε ο <b>διαχειριστής</b> της.' },
      { sel: '#open-assign', place: 'bottom', title: 'Προσθήκη μελών και ρόλοι',
        body: 'Ως διαχειριστής προσθέτετε συναδέλφους με username ή email. Ο <b>διαχειριστής</b> διαχειρίζεται μέλη· το <b>μέλος</b> εργάζεται κανονικά στα δεδομένα. Ο χρήστης ειδοποιείται στο επόμενο login του.' }
    ] },

    credentials: { label: 'Πελάτες', path: '/credentials', steps: [
      { sel: '.cred-btn-main', place: 'bottom', title: 'Προσθήκη πελάτη',
        body: 'Καταχωρείτε τον πελάτη με επωνυμία, ΑΦΜ, <b>myDATA user</b> και <b>subscription key</b>, και την κατηγορία βιβλίων (Β΄ ή Γ΄). Το ΑΦΜ ελέγχεται αυτόματα.' },
      { sel: '#credentialSearch', place: 'bottom', title: 'Αναζήτηση',
        body: 'Βρείτε γρήγορα πελάτη με όνομα ή ΑΦΜ (χωρίς να έχουν σημασία οι τόνοι) και φιλτράρετε ανά κατηγορία Β / Γ.' },
      { sel: '#credentialsTable', place: 'top', title: 'Η λίστα πελατών',
        body: 'Πατήστε μια γραμμή για να τον επιλέξετε. Από τα εικονίδια της γραμμής επεξεργάζεστε ή διαγράφετε τον πελάτη.' },
      { sel: '#setActiveBtn', place: 'top', title: 'Ορισμός ως ενεργού',
        body: 'Ο επιλεγμένος πελάτης γίνεται <b>ενεργός</b>: λήψη, αναζήτηση, E3 και λογιστικό αποτέλεσμα δουλεύουν πλέον γι’ αυτόν.' },
      { sel: '#openSettingsBtn', place: 'bottom', title: '⚙ Ρυθμίσεις',
        body: 'Γενικοί λογαριασμοί, συναλλασσόμενοι και <b>αντίγραφα ασφαλείας</b> (Backup / επαναφορά) της ομάδας.' }
    ] },

    fetch: { label: 'Λήψη', path: '/fetch', steps: [
      { sel: '#fetchModeTabs', place: 'bottom', title: 'Ατομική ή μαζική λήψη',
        body: 'Η <b>Ατομική</b> κατεβάζει τα παραστατικά του ενεργού πελάτη. Η <b>Μαζική</b> (διαχειριστές) τρέχει για πολλούς πελάτες ταυτόχρονα και μπορεί να διακοπεί.' },
      { sel: ['#date_from', '#singleFetchPanel'], place: 'bottom', title: 'Περίοδος', enter: clickTab('#singleFetchTabBtn'),
        body: 'Ορίστε «Από – Έως». Δείχνει και πότε έγινε η τελευταία λήψη, ώστε να συνεχίσετε από εκεί που σταματήσατε.' },
      { sel: '#fetchSubmitBtn', place: 'top', title: 'Ανάκτηση', enter: clickTab('#singleFetchTabBtn'),
        body: 'Η λήψη τρέχει στο παρασκήνιο με ένδειξη προόδου· μπορείτε να συνεχίσετε να δουλεύετε και θα ειδοποιηθείτε όταν τελειώσει.' }
    ] },

    search: { label: 'Αναζήτηση & Χαρακτηρισμός', path: '/search', steps: [
      { sel: '#markInput', place: 'bottom', title: 'Αναζήτηση MARK',
        body: 'Γράψτε το <b>15ψήφιο MARK</b> ή επικολλήστε το <b>URL/QR</b> του παραστατικού. Το σύστημα φέρνει τα στοιχεία από το myDATA ή από τον πάροχο του εντύπου.' },
      { sel: ['#openQrScanner', '#qrScannerMenuToggle'], place: 'bottom', title: 'Σάρωση QR',
        body: 'Σαρώστε το QR με την κάμερα του υπολογιστή ή <b>συνδέστε το κινητό σας</b> και σκανάρετε από εκεί — τα αποτελέσματα εμφανίζονται άμεσα εδώ.' },
      { sel: ['#receiptsModeChooser', '#receiptsSwitchOff'], place: 'bottom', title: 'Τιμολόγια ή Αποδείξεις',
        body: 'Επιλέξτε τον τρόπο λειτουργίας. Στις αποδείξεις μπορείτε να δουλέψετε με <b>Ανάλυση</b> ανά γραμμή ή <b>Μικτό</b> ποσό.' },
      { sel: ['#repeatEntrySwitch', '#editRepeatMappingBtn'], place: 'bottom', title: 'Επαναληπτική εισαγωγή',
        body: 'Αντιστοιχίζετε μία φορά κάθε συντελεστή ΦΠΑ σε κατηγορία χαρακτηρισμού και το σύστημα χαρακτηρίζει και αποθηκεύει <b>αυτόματα</b> τα επόμενα παραστατικά.' },
      { sel: '#manageAfmRulesBtn', place: 'bottom', title: 'Κανόνες ΑΦΜ',
        body: 'Ορίστε για έναν προμηθευτή σταθερό χαρακτηρισμό: κάθε νέο παραστατικό του παίρνει αυτόματα τον κανόνα.' },
      { sel: '#uncharOpenBtn', place: 'bottom', title: 'Αχαρακτήριστα myDATA',
        body: 'Λίστα τιμολογίων που δεν έχουν χαρακτηριστεί. Επιλέξτε, χαρακτηρίστε μαζικά και αποθηκεύστε στη λίστα· μπορείτε και να κατεβάσετε τα PDF σε ZIP.' },
      { sel: '#summary-container', place: 'top', title: 'Η λίστα παραστατικών',
        body: 'Όλα τα καταχωρημένα παραστατικά με <b>φίλτρα ανά στήλη</b>, ταξινόμηση και αναζήτηση. Μαζικός χαρακτηρισμός, διαγραφή με δυνατότητα <b>αναίρεσης</b>.' },
      { sel: ['#listDownloadControls', '#downloadToggle', '#summary-container'], place: 'bottom', title: 'Εξαγωγές',
        body: 'Λήψη σε <b>Excel</b> και <b>γέφυρα Epsilon</b> (προεπισκόπηση, XLSX, αρχείο HyperLog .ld, ενημέρωση backup Epsilon) χωρίς διπλοεξαγωγή ήδη εξαγμένων.' }
    ] },

    e3: { label: 'Έλεγχος Ε3', path: '/e3_check', steps: [
      { sel: '#quickCheckTabBtn', place: 'bottom', title: 'Γρήγορος έλεγχος',
        body: 'Ανεβάστε το <b>ισοζύγιο</b> (Excel) και συγκρίνετέ το με τα δεδομένα myDATA και τα πεδία του εντύπου Ε3, ανά κωδικό, με τη διαφορά σε κάθε γραμμή.' },
      { sel: ['#e3Credential', '#e3CredentialLocked'], place: 'bottom', title: 'Πελάτης και περίοδος', enter: clickTab('#quickCheckTabBtn'),
        body: 'Ο έλεγχος δουλεύει για τον ενεργό πελάτη· ορίστε το διάστημα «Από – Έως» και φορτώστε Ε3 / myDATA / ισοζύγιο.' },
      { sel: '#analyticTabBtn', place: 'bottom', title: 'Αναλυτικός έλεγχος (E3 Brain)',
        body: 'Συνδυάζει ΑΑΔΕ/ΓΕΜΗ, <b>ΕΦΚΑ / ΤΕΚΑ</b>, <b>ΚΕΑΟ</b>, μισθωτήρια και <b>Ε9/ΕΝΦΙΑ</b>, με αποθήκευση των PDF βεβαιώσεων. Υπάρχει ατομική και μαζική εκτέλεση, αποθηκευμένα στοιχεία και log ανακτήσεων.' }
    ] },

    ar: { label: 'Λογιστικό Αποτέλεσμα', path: '/accounting_result', steps: [
      { sel: '#arTabSingleBtn', place: 'bottom', title: 'Ατομικός υπολογισμός', enter: clickTab('#arTabSingleBtn'),
        body: 'Επιλέξτε εταιρία και περίοδο και υπολογίστε το λογιστικό αποτέλεσμα από τα παραστατικά myDATA, με προαιρετικό ισοζύγιο Excel.' },
      { sel: ['#arSingleVatForecast', '#arSingleCredential'], place: 'bottom', title: 'Εκτιμήσεις', enter: clickTab('#arTabSingleBtn'),
        body: 'Προσθέστε εκτίμηση ΦΠΑ και φόρου εισοδήματος. Αν χρειάζεται, θα σας ζητηθούν πρώτα έλεγχοι (απόθεμα λήξης, μηνιαία σύνολα, αποσβέσεις).' },
      { sel: '#arSingleComputeBtn', place: 'top', title: 'Υπολογισμός και PDF', enter: clickTab('#arTabSingleBtn'),
        body: 'Το αποτέλεσμα εμφανίζεται αναλυτικά, αποθηκεύεται στο <b>ιστορικό</b> και κατεβαίνει σε PDF.' },
      { sel: '#arTabBulkBtn', place: 'bottom', title: 'Μαζικός υπολογισμός',
        body: 'Υπολογίζει πολλές εταιρίες μαζί. Τρέχει στον server, οπότε <b>συνεχίζει</b> ακόμη κι αν αλλάξετε σελίδα ή αποσυνδεθείτε· ZIP με PDF ανά εταιρία ή συγκεντρωτικό PDF.' },
      { sel: '#arTabSavedBtn', place: 'bottom', title: 'Αποθηκευμένα',
        body: 'Η λίστα εταιριών με τους κωδικούς τους: εισαγωγή από Excel, ανίχνευση ΦΠΑ και μαζική αναζήτηση στοιχείων ΑΑΔΕ.' }
    ] },

    finish: { label: 'Τέλος', path: null, steps: [
      { title: 'Έτοιμοι! 🎉',
        body: 'Προτεινόμενη σειρά: <b>ομάδα → πελάτης → λήψη → αναζήτηση/χαρακτηρισμός → έλεγχος Ε3 → λογιστικό αποτέλεσμα</b>.<br><br>Ο <b>Οδηγός χρήσης</b> έχει αναλυτικές οδηγίες για κάθε ενότητα και μπορείτε να ξαναδείτε την ξενάγηση οποιαδήποτε στιγμή.',
        final: true }
    ] }
  };
  var FULL_ORDER = ['intro', 'groups', 'credentials', 'fetch', 'search', 'e3', 'ar', 'finish'];

  // ---- Βοηθητικά -----------------------------------------------------------
  function normPath(p) { return (p || '/').replace(/\/+$/, '') || '/'; }
  function curPath() { return normPath(window.location.pathname); }
  function $(sel) { try { return document.querySelector(sel); } catch (_) { return null; } }
  function isVisible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var cs = window.getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  }
  function findTarget(sel) {
    var list = Array.isArray(sel) ? sel : (sel ? [sel] : []);
    for (var i = 0; i < list.length; i++) {
      var el = $(list[i]);
      if (el && isVisible(el)) return el;
    }
    return null;
  }
  // Το side menu θεωρείται κλειστό όταν είναι hidden ή έχει ακόμη translate-x-full (κλείνει / δεν άνοιξε).
  function sideMenuClosed(m) { return m.classList.contains('hidden') || m.classList.contains('translate-x-full'); }
  // Πατά (αν υπάρχει) ένα κουμπί καρτέλας ώστε το panel του στόχου να είναι ορατό. Ιδεμποτενές για tabs.
  function clickTab(sel) {
    return function () {
      try { var b = document.querySelector(sel); if (b) b.click(); } catch (_) {}
    };
  }
  function openSideMenu() {
    var m = document.getElementById('sideMenu');
    if (m && sideMenuClosed(m)) {
      var b = document.getElementById('sideMenuToggle');
      if (b) b.click();
    }
  }
  function closeSideMenu() {
    var m = document.getElementById('sideMenu');
    if (m && !sideMenuClosed(m)) {
      var b = document.getElementById('sideMenuClose');
      if (b) b.click();
    }
  }
  function readState() {
    try {
      var s = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
      if (s && s.ts && (Date.now() - s.ts) < MAX_AGE_MS) return s;
    } catch (_) {}
    return null;
  }
  function writeState(s) {
    try {
      if (s) { s.ts = Date.now(); localStorage.setItem(LS_KEY, JSON.stringify(s)); }
      else localStorage.removeItem(LS_KEY);
    } catch (_) {}
  }
  // «Το είδα»: ανά χρήστη, στο localStorage της συσκευής (καμία κλήση προς τον server).
  function seenKey() { return 'smd_tour_seen_' + String((window.SMD_USER && window.SMD_USER.id) || 'anon'); }
  function markSeen(status) {
    try { localStorage.setItem(seenKey(), status || 'completed'); } catch (_) {}
  }
  function wasSeen() {
    try { return !!localStorage.getItem(seenKey()); } catch (_) { return false; }
  }
  function navigate(path) {
    if (PARTIAL_PATHS.indexOf(path) >= 0 && typeof window.__partialNavigateTo === 'function') {
      return Promise.resolve(window.__partialNavigateTo(path, {})).catch(function () { window.location.href = path; });
    }
    window.location.href = path;
    return new Promise(function () {});   // η σελίδα φορτώνει ξανά· η συνέχεια γίνεται από το resume
  }
  function waitFor(sel, ms) {
    return new Promise(function (resolve) {
      var t0 = Date.now();
      (function poll() {
        var el = findTarget(sel);
        if (el) return resolve(el);
        if (Date.now() - t0 >= ms) return resolve(null);
        setTimeout(poll, 140);
      })();
    });
  }

  // ---- Κατάσταση εκτέλεσης -------------------------------------------------
  var run = null;   // { name, steps:[{...,section}], i, skip:{section:true}, full:boolean }
  var ui = null;    // { root, blocker, ring, card }
  var token = 0;    // ακυρώνει ασύγχρονες συνέχειες παλιών βημάτων
  var rafId = 0;

  function buildSteps(name) {
    var order = name === 'full' ? FULL_ORDER : [name];
    var out = [];
    order.forEach(function (sec) {
      var S = SECTIONS[sec];
      if (!S) return;
      S.steps.forEach(function (st, idx) {
        var c = {}; for (var k in st) c[k] = st[k];
        c.section = sec; c.path = (st.path !== undefined ? st.path : S.path); c.sectionLabel = S.label; c.first = idx === 0;
        out.push(c);
      });
    });
    // Μεμονωμένη ενότητα: κλείνει με ένα τελικό βήμα.
    if (name !== 'full' && name !== 'finish' && out.length) {
      out.push({ section: name, path: null, sectionLabel: SECTIONS[name].label, final: true, single: true,
        title: 'Αυτό ήταν!', body: 'Για περισσότερες λεπτομέρειες δείτε τον <b>Οδηγό χρήσης</b>.' });
    }
    return out;
  }

  // ---- UI ------------------------------------------------------------------
  function ensureUi() {
    if (ui && document.body.contains(ui.root)) return ui;
    var root = document.createElement('div');
    root.id = 'smdTourRoot';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-live', 'polite');
    root.innerHTML =
      '<div class="smdt-blocker"></div>' +
      '<div class="smdt-ring"></div>' +
      '<div class="smdt-card">' +
      '  <div class="smdt-head"><span class="smdt-section"></span><button type="button" class="smdt-x" aria-label="Κλείσιμο ξενάγησης" title="Κλείσιμο (Esc)">✕</button></div>' +
      '  <h3 class="smdt-title"></h3>' +
      '  <div class="smdt-body"></div>' +
      '  <div class="smdt-note" hidden></div>' +
      '  <div class="smdt-progress"><div class="smdt-bar"></div></div>' +
      '  <div class="smdt-foot">' +
      '    <span class="smdt-count"></span>' +
      '    <span class="smdt-btns">' +
      '      <button type="button" class="smdt-btn smdt-skip">Παράλειψη</button>' +
      '      <button type="button" class="smdt-btn smdt-prev">← Πίσω</button>' +
      '      <button type="button" class="smdt-btn smdt-next smdt-primary">Επόμενο →</button>' +
      '    </span>' +
      '  </div>' +
      '</div>';
    document.body.appendChild(root);
    ui = {
      root: root,
      blocker: root.querySelector('.smdt-blocker'),
      ring: root.querySelector('.smdt-ring'),
      card: root.querySelector('.smdt-card'),
      section: root.querySelector('.smdt-section'),
      title: root.querySelector('.smdt-title'),
      body: root.querySelector('.smdt-body'),
      note: root.querySelector('.smdt-note'),
      bar: root.querySelector('.smdt-bar'),
      count: root.querySelector('.smdt-count'),
      prev: root.querySelector('.smdt-prev'),
      next: root.querySelector('.smdt-next'),
      skip: root.querySelector('.smdt-skip')
    };
    ui.next.addEventListener('click', function () { go(+1); });
    ui.prev.addEventListener('click', function () { go(-1); });
    ui.skip.addEventListener('click', function () {
      if (run && run.steps.length > 0 && ui.skip.textContent.indexOf('ενότητα') >= 0) { skipSection(); }
      else { stop('dismissed'); }
    });
    root.querySelector('.smdt-x').addEventListener('click', function () { stop('dismissed'); });
    return ui;
  }

  function destroyUi() {
    clearTimeout(rafId);
    if (ui && ui.root && ui.root.parentNode) ui.root.parentNode.removeChild(ui.root);
    ui = null;
  }

  function placeCard(target, place) {
    var u = ui; if (!u) return;
    var card = u.card, vw = window.innerWidth, vh = window.innerHeight, M = 12, GAP = 14;
    // Ρητό πλάτος: αλλιώς ένα fixed στοιχείο κοντά στη δεξιά άκρη «στενεύει» (shrink-to-fit) σε κινητά.
    card.style.width = Math.min(target ? 400 : 460, vw - 2 * M) + 'px';
    if (!target) {
      u.ring.style.opacity = '0';
      u.root.classList.add('smdt-centered');
      card.style.left = '50%'; card.style.top = '50%';
      card.style.transform = 'translate(-50%,-50%)';
      return;
    }
    u.root.classList.remove('smdt-centered');
    card.style.transform = 'none';
    var r = target.getBoundingClientRect();
    var pad = 6;
    var rx = Math.max(0, r.left - pad), ry = Math.max(0, r.top - pad);
    var rw = Math.min(vw - rx, r.width + pad * 2), rh = Math.min(vh - ry, r.height + pad * 2);
    u.ring.style.opacity = '1';
    u.ring.style.left = rx + 'px'; u.ring.style.top = ry + 'px';
    u.ring.style.width = rw + 'px'; u.ring.style.height = rh + 'px';

    var cw = card.offsetWidth, ch = card.offsetHeight;
    var spaces = { bottom: vh - (ry + rh), top: ry, right: vw - (rx + rw), left: rx };
    var order = [place || 'bottom', 'bottom', 'top', 'right', 'left'];
    var chosen = null;
    for (var i = 0; i < order.length; i++) {
      var p = order[i];
      var need = (p === 'bottom' || p === 'top') ? ch + GAP : cw + GAP;
      if (spaces[p] >= need) { chosen = p; break; }
    }
    var left, top;
    if (!chosen) {   // δεν χωράει πουθενά → πάνω στο στόχο, στο κάτω μέρος της οθόνης
      left = (vw - cw) / 2; top = vh - ch - M;
    } else if (chosen === 'bottom') { top = ry + rh + GAP; left = rx + rw / 2 - cw / 2; }
    else if (chosen === 'top') { top = ry - ch - GAP; left = rx + rw / 2 - cw / 2; }
    else if (chosen === 'right') { left = rx + rw + GAP; top = ry + rh / 2 - ch / 2; }
    else { left = rx - cw - GAP; top = ry + rh / 2 - ch / 2; }
    left = Math.max(M, Math.min(left, vw - cw - M));
    top = Math.max(M, Math.min(top, vh - ch - M));
    card.style.left = left + 'px'; card.style.top = top + 'px';
  }

  var trackTarget = null, trackPlace = null;
  function trackLoop() {
    if (!ui) return;
    if (trackTarget && document.body.contains(trackTarget)) placeCard(trackTarget, trackPlace);
    rafId = setTimeout(trackLoop, 140);
  }

  // ---- Ροή -----------------------------------------------------------------
  function saveRun() {
    if (!run) return;
    writeState({ name: run.name, i: run.i, skip: run.skip });
  }

  function render(step, target, note) {
    var u = ensureUi();
    var total = run.steps.length;
    u.section.textContent = step.sectionLabel || '';
    u.title.textContent = step.title || '';
    u.body.innerHTML = step.body || '';
    if (note) { u.note.hidden = false; u.note.textContent = note; } else { u.note.hidden = true; u.note.textContent = ''; }
    var cur = run.steps[run.i];
    var order = [];
    run.steps.forEach(function (st) { if (order.indexOf(st.section) < 0) order.push(st.section); });
    var inSec = run.steps.filter(function (st) { return st.section === cur.section; });
    var secNo = order.indexOf(cur.section) + 1;
    var multi = order.length > 2;   // πλήρης ξενάγηση (πολλές ενότητες)
    if (multi) {
      u.count.textContent = 'Ενότητα ' + secNo + '/' + order.length + ' · βήμα ' + (inSec.indexOf(cur) + 1) + '/' + inSec.length;
    } else {
      u.count.textContent = 'Βήμα ' + (run.i + 1) + ' από ' + total;
    }
    u.bar.style.width = Math.round(((run.i + 1) / total) * 100) + '%';
    u.prev.disabled = !prevIndex();
    u.prev.style.visibility = prevIndex() ? 'visible' : 'hidden';
    // Πλήρης ξενάγηση: «Επόμενη ενότητα»· μεμονωμένη ενότητα: «Παράλειψη» (κλείνει). Το ✕ κλείνει πάντα όλη την ξενάγηση.
    var lastSection = secNo === order.length;
    u.skip.style.display = (step.final || (multi && lastSection)) ? 'none' : '';
    u.skip.textContent = multi ? 'Επόμενη ενότητα ⏭' : 'Παράλειψη';
    u.skip.title = multi ? 'Παράλειψη των υπόλοιπων βημάτων αυτής της ενότητας' : 'Κλείσιμο ξενάγησης';
    if (step.final) {
      u.next.textContent = (step.single ? 'Κλείσιμο' : 'Τέλος ✓');
    } else {
      u.next.textContent = 'Επόμενο →';
    }
    // Στο τελικό βήμα της πλήρους ξενάγησης προσφέρουμε και άνοιγμα του οδηγού.
    var extra = u.card.querySelector('.smdt-manual');
    if (extra) extra.remove();
    if (step.final && !step.single) {
      var a = document.createElement('a');
      a.href = '/manual'; a.className = 'smdt-btn smdt-manual'; a.textContent = '📖 Οδηγός χρήσης';
      a.addEventListener('click', function () { stop('completed'); });
      u.card.querySelector('.smdt-btns').insertBefore(a, u.next);
    }
    placeCard(target, step.place);
    trackTarget = target; trackPlace = step.place;
    clearTimeout(rafId); trackLoop();
    try { u.next.focus({ preventScroll: true }); } catch (_) {}
  }

  function nextIndex() {
    for (var j = run.i + 1; j < run.steps.length; j++) {
      if (!run.skip[run.steps[j].section]) return j;
    }
    return 0;
  }
  function prevIndex() {
    for (var j = run.i - 1; j >= 0; j--) {
      if (!run.skip[run.steps[j].section]) return j + 1;   // +1 ώστε το 0 να σημαίνει «κανένα»
    }
    return 0;
  }

  function leaveCurrent() {
    try { var s = run && run.steps[run.i]; if (s && typeof s.leave === 'function') s.leave(); } catch (_) {}
  }

  function skipSection() {
    if (!run) return;
    leaveCurrent();
    run.skip[run.steps[run.i].section] = true;
    var n = nextIndex();
    if (!n) { stop('completed'); return; }
    run.i = n;
    saveRun();
    show();
  }

  function go(dir) {
    if (!run) return;
    var cur = run.steps[run.i];
    if (dir > 0 && cur.final) { stop(cur.single ? 'single' : 'completed'); return; }
    leaveCurrent();
    if (dir > 0) {
      var n = nextIndex();
      if (!n) { stop('completed'); return; }
      run.i = n;
    } else {
      var p = prevIndex();
      if (!p) return;
      run.i = p - 1;
    }
    saveRun();
    show();
  }

  async function show() {
    if (!run) return;
    var my = ++token;
    var step = run.steps[run.i];
    var u = ensureUi();
    u.root.classList.add('smdt-busy');
    // 1) Σωστή σελίδα
    if (step.path && curPath() !== step.path) {
      var alreadyTried = run.navTried === run.i;
      if (!alreadyTried) {
        run.navTried = run.i; saveRun();
        await navigate(step.path);
        run.justNavigated = true;
        if (my !== token) return;
        await new Promise(function (r) { setTimeout(r, 250); });
      }
      if (curPath() !== step.path) {
        // Η σελίδα μας ανακατεύθυνε (π.χ. δεν υπάρχει ενεργή ομάδα/πελάτης): παραλείπουμε την ενότητα.
        run.skip[step.section] = true;
        saveRun();
        run.navTried = null;
        u.root.classList.remove('smdt-busy');
        render({ title: (step.sectionLabel || 'Ενότητα') + ' — δεν είναι διαθέσιμη ακόμη',
          body: 'Για να δείτε αυτή την ενότητα πρέπει πρώτα να έχετε επιλέξει <b>ομάδα</b> και <b>ενεργό πελάτη</b>. Θα τη βρείτε στον Οδηγό χρήσης όταν είστε έτοιμοι.',
          sectionLabel: step.sectionLabel, place: 'bottom' }, null, null);
        return;
      }
    }
    run.navTried = null;
    // 2) Προετοιμασία + στόχος
    try {
      if (typeof step.enter === 'function') {
        step.enter();
        // Περιμένουμε να ολοκληρωθεί το animation του side menu (ή να περάσει ένα λογικό όριο).
        var t0 = Date.now();
        while (step.menu && Date.now() - t0 < 1800) {
          var sm = document.getElementById('sideMenu');
          if (!sm) break;
          var rr = sm.getBoundingClientRect();
          if (!sideMenuClosed(sm) && rr.width > 100 && rr.left <= window.innerWidth - rr.width + 4) break;
          await new Promise(function (r) { setTimeout(r, 80); });
        }
        await new Promise(function (r) { setTimeout(r, step.menu ? 120 : 200); });   // μικρή αναμονή για το άνοιγμα καρτέλας
        if (my !== token) return;
      }
    } catch (_) {}
    // Μετά από μετάβαση σελίδας περιμένουμε περισσότερο (scripts/δεδομένα)· αλλιώς το στοιχείο είτε υπάρχει είτε όχι.
    var justNavigated = !!run.justNavigated; run.justNavigated = false;
    var target = step.sel ? await waitFor(step.sel, justNavigated ? 4500 : 1300) : null;
    if (my !== token) return;
    var note = null;
    if (step.sel && !target) note = 'Αυτό το στοιχείο δεν είναι ορατό αυτή τη στιγμή (ίσως απαιτείται ενεργός πελάτης ή άλλη οθόνη).';
    if (target) {
      var r = target.getBoundingClientRect();
      if (r.top < 70 || r.bottom > window.innerHeight - 40) {
        try { target.scrollIntoView({ block: 'center', behavior: 'auto' }); } catch (_) {}
        await new Promise(function (res) { setTimeout(res, 80); });
        if (my !== token) return;
      }
    }
    u.root.classList.remove('smdt-busy');
    render(step, target, note);
  }

  function start(name, opts) {
    opts = opts || {};
    name = name || 'full';
    if (name === 'intro_only') name = 'intro';
    var steps = buildSteps(name);
    if (!steps.length) return false;
    if (run) { leaveCurrent(); }
    run = { name: name, steps: steps, i: 0, skip: {}, navTried: null, auto: !!opts.auto };
    saveRun();
    document.documentElement.classList.add('smdt-active');
    bindKeys();
    show();
    return true;
  }

  function stop(status) {
    leaveCurrent();
    var wasFull = run && run.name === 'full';
    run = null; token++;
    writeState(null);
    unbindKeys();
    document.documentElement.classList.remove('smdt-active');
    destroyUi();
    trackTarget = null;
    if (wasFull && (status === 'completed' || status === 'dismissed')) markSeen(status);
  }

  // ---- Πληκτρολόγιο --------------------------------------------------------
  function onKey(e) {
    if (!run) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); stop('dismissed'); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); e.stopImmediatePropagation(); go(+1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopImmediatePropagation(); go(-1); }
  }
  function onResize() { if (run && trackTarget) placeCard(trackTarget, trackPlace); }
  var keysBound = false;
  function bindKeys() {
    if (keysBound) return; keysBound = true;
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', onResize, true);
  }
  function unbindKeys() {
    keysBound = false;
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', onResize);
    window.removeEventListener('scroll', onResize, true);
  }

  // ---- Συνέχιση μετά από φόρτωση σελίδας / αυτόματη εκκίνηση ----------------
  function resume() {
    var s = readState();
    if (!s || run) return false;
    var steps = buildSteps(s.name);
    if (!steps.length || s.i >= steps.length) { writeState(null); return false; }
    run = { name: s.name, steps: steps, i: s.i, skip: s.skip || {}, navTried: s.i, justNavigated: true };
    document.documentElement.classList.add('smdt-active');
    bindKeys();
    show();
    return true;
  }

  function cookieBannerOpen() {
    var b = document.getElementById('cookieConsent');
    return !!(b && b.style.display !== 'none' && isVisible(b));
  }
  function onSyncPage() {
    var p = document.getElementById('syncProgressOverlay');
    return /\/sync\//.test(window.location.pathname) || !!(p && isVisible(p));
  }

  var autoChecked = false;
  var NEW_ACCOUNT_DAYS = 14;
  function autoStart() {
    if (autoChecked || run) return;
    if (!window.IS_LOGGED_IN) return;
    if (/^\/(firebase-auth|login|signup|manual|terms|privacy)/.test(window.location.pathname)) return;
    autoChecked = true;
    if (wasSeen()) return;
    // Νέος λογαριασμός = δημιουργήθηκε τις τελευταίες NEW_ACCOUNT_DAYS μέρες· οι παλιότεροι βλέπουν μόνο μια ενημέρωση.
    var created = window.SMD_USER && window.SMD_USER.created ? Date.parse(window.SMD_USER.created) : NaN;
    var isNew = isNaN(created) ? true : (Date.now() - created) <= NEW_ACCOUNT_DAYS * 86400000;
    var tries = 0;
    (function when() {
      if (run) return;
      if ((cookieBannerOpen() || onSyncPage() || document.getElementById('groupNoticeModal')) && tries++ < 120) return setTimeout(when, 500);
      if (isNew) {
        start('full', { auto: true });
      } else {
        if (typeof window.showFlash === 'function') {
          window.showFlash('📖 Νέο: «Οδηγός χρήσης» με ξενάγηση στην εφαρμογή — ☰ Μενού → Οδηγός χρήσης.', 'info', 9000);
        }
        markSeen('announced');
      }
    })();
  }

  window.addEventListener('app:page-ready', function () {
    if (run) { show(); } else { resume(); }
  });

  function boot() {
    if (!resume()) setTimeout(autoStart, 2200);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  // Κουμπιά [data-tour-start="intro|full|…"] (π.χ. στον Οδηγό χρήσης και στο μενού).
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest ? e.target.closest('[data-tour-start]') : null;
    if (!b) return;
    e.preventDefault();
    if (!window.IS_LOGGED_IN) { window.location.href = '/firebase-auth/login'; return; }
    closeSideMenu();
    start(b.getAttribute('data-tour-start') || 'full');
  }, true);

  window.SmdTour = { start: start, stop: stop, sections: SECTIONS };
})();
