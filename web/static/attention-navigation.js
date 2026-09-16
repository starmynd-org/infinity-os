/* Return links name a real row. Make that destination visible and keyboard-usable. */
(function () {
  'use strict';
  function returnToRow() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
    const row = id && document.getElementById(id);
    if (!row || !row.matches('[data-queue-row]')) return;
    const header = document.querySelector('header');
    row.style.scrollMarginTop = ((header ? header.getBoundingClientRect().height : 0) + 12) + 'px';
    row.focus({preventScroll: true});
    row.scrollIntoView({block: 'start'});
  }
  returnToRow();
  addEventListener('hashchange', returnToRow);
})();
