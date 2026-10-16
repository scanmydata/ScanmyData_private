/* ScanmyData — responsive βοηθός (φορτώνεται ΜΙΑ φορά, εκτός #appShell).
   Πίνακας που δεν χωρά στο πλάτος του γονέα του τυλίγεται σε <div class="rsp-scroll"> ώστε να κυλά
   οριζόντια μέσα στη θέση του αντί να σπρώχνει όλη τη σελίδα. Πίνακες που χωράνε δεν αγγίζονται
   (κανένα αποτέλεσμα στη desktop εμφάνιση). Εξαίρεση με data-no-rsp στον πίνακα ή σε πρόγονο. */
(function () {
  'use strict';
  if (window.__smdResponsive) return;
  window.__smdResponsive = true;

  function hasScrollingAncestor(el) {
    for (var p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      var ox = getComputedStyle(p).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
    }
    return false;
  }

  function wrapIfOverflowing(table) {
    var parent = table.parentElement;
    if (!parent || parent.classList.contains('rsp-scroll')) return;
    if (table.closest('[data-no-rsp]')) return;
    var avail = parent.clientWidth;
    if (!avail || !table.offsetWidth) return;            // κρυφό (π.χ. κλειστό modal): ξανά αργότερα
    if (table.offsetWidth <= avail + 1) return;
    if (hasScrollingAncestor(table)) return;
    var w = document.createElement('div');
    w.className = 'rsp-scroll';
    parent.insertBefore(w, table);
    w.appendChild(table);
  }

  // Η πλοήγηση της κεφαλίδας κυλά οριζόντια σε στενές οθόνες: φέρε το ενεργό κουμπί σε θέα
  // (μία φορά ανά header — μετά από partial navigation το header είναι νέο).
  function revealActiveNav() {
    var nav = document.querySelector('header .layout-header__nav');
    if (!nav || nav.__rspDone || nav.scrollWidth <= nav.clientWidth + 1) return;
    nav.__rspDone = true;
    var a = nav.querySelector('a.bg-sky-600');
    if (!a) return;
    var left = a.getBoundingClientRect().left - nav.getBoundingClientRect().left + nav.scrollLeft
      - (nav.clientWidth - a.offsetWidth) / 2;
    nav.scrollLeft = Math.max(0, left);
  }

  function scan() {
    try { revealActiveNav(); } catch (_) {}
    var tables = document.querySelectorAll('#appShell table, body > .fixed table, body > [role="dialog"] table');
    for (var i = 0; i < tables.length; i++) {
      try { wrapIfOverflowing(tables[i]); } catch (_) {}
    }
  }

  var timer = null;
  function schedule() {
    if (timer) return;
    timer = setTimeout(function () {
      timer = null;
      (window.requestAnimationFrame || setTimeout)(scan);
    }, 250);
  }
  window.smdResponsiveScan = scan;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();
  window.addEventListener('load', schedule);
  window.addEventListener('resize', schedule, { passive: true });
  window.addEventListener('orientationchange', schedule);
  try {
    // νέοι πίνακες (partial navigation, AJAX, DataTables) και modals που ανοίγουν (class/style)
    new MutationObserver(schedule).observe(document.documentElement, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style', 'open']
    });
  } catch (_) {}
})();
