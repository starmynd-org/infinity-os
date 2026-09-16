/* Attention's right-hand panel (A4 INFINITY-STREAMLINE, D-ALPHA-UX-1, 2026-09-14): a row opens its
 * inspector beside the table instead of replacing the page. The panel only READS: one GET of the
 * inspector URL the row already links to, a page whose own template says it never acts. Below the
 * breakpoint, and on a modified click, every link navigates exactly as it did before. */
(function () {
  'use strict';
  const WIDE = window.matchMedia('(min-width: 1100px)');
  const panel = document.querySelector('[data-att-panel]');
  const section = document.querySelector('section[data-attention-queue]');
  if (!panel || !section) return;
  const body = panel.querySelector('[data-att-panel-body]');
  const full = panel.querySelector('[data-att-panel-full]');
  const title = panel.querySelector('[data-att-panel-title]');
  const INTERACTIVE = 'a, button, input, textarea, select, summary, label, form, details';
  let openRow = null;
  let ticket = 0;

  function place() {
    const header = document.querySelector('body > header');
    panel.style.top = (header ? Math.max(0, header.getBoundingClientRect().bottom) : 0) + 'px';
  }

  function mark(row) {
    if (openRow) openRow.classList.remove('att-row-open');
    openRow = row;
    if (row) row.classList.add('att-row-open');
  }

  function close() {
    ticket += 1;
    panel.hidden = true;
    document.body.classList.remove('att-panel-open');
    const row = openRow;
    mark(null);
    if (row && row.isConnected) row.focus({preventScroll: true});
  }

  /* The inspector's own words, lifted out of its `main`. The chrome that is not the inspector
   * (the say bar and the celebration host) stays behind. A failed read says so in the panel and
   * keeps the full-page link, rather than showing a stale row's detail. */
  function fill(html, status, hash) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const main = doc.getElementById('main-content');
    body.textContent = '';
    if (!main) {
      body.textContent = 'The inspector answered ' + status + ' without a page this panel can read. ' +
                         'Open the full page instead.';
      return;
    }
    Array.prototype.forEach.call(main.children, function (child) {
      if (child.id === 'saybar' || child.id === 'celebrate') return;
      body.appendChild(document.importNode(child, true));
    });
    const heading = body.querySelector('h1');
    title.textContent = heading ? heading.textContent : 'Row detail';
    const target = hash ? body.querySelector('[id="' + hash.slice(1).replace(/"/g, '') + '"]') : null;
    if (target) target.scrollIntoView({block: 'start'});
    else body.scrollTop = 0;
  }

  function show(url, row) {
    const mine = ++ticket;
    const parsed = new URL(url, location.href);
    const hash = parsed.hash;
    parsed.hash = '';
    mark(row);
    full.setAttribute('href', parsed.href);
    title.textContent = 'Row detail';
    body.textContent = 'Reading this row’s inspector…';
    panel.hidden = false;
    document.body.classList.add('att-panel-open');
    place();
    fetch(parsed.href, {credentials: 'same-origin', headers: {Accept: 'text/html'}})
      .then(function (r) { return r.text().then(function (text) { return {status: r.status, text: text}; }); })
      .then(function (res) { if (mine === ticket) fill(res.text, res.status, hash); })
      .catch(function (e) {
        if (mine !== ticket) return;
        body.textContent = 'This panel could not read the inspector: ' + String(e) +
                           '. Open the full page instead.';
      });
  }

  section.addEventListener('click', function (e) {
    if (!WIDE.matches || e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const row = e.target.closest('tr[data-queue-row]');
    if (!row) return;
    const source = row.querySelector('[data-att-panel-src]');
    if (!source) return;
    const link = e.target.closest('a[href]');
    if (link) {
      /* Only links to THIS row's inspector (Inspect and the evidence chips) open the panel. The
       * report and task links keep going to their own pages. */
      const to = new URL(link.getAttribute('href'), location.href);
      const inspector = new URL(source.getAttribute('data-att-panel-src'), location.href);
      if (to.pathname !== inspector.pathname) return;
      e.preventDefault();
      show(link.getAttribute('href'), row);
      return;
    }
    if (e.target.closest(INTERACTIVE)) return;
    show(source.getAttribute('data-att-panel-src'), row);
  });

  panel.addEventListener('click', function (e) {
    if (e.target.closest('[data-att-panel-close]')) close();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !panel.hidden) close();
  });
  /* An act removes its row and leaves a receipt: the detail of a row that has gone is closed. */
  section.addEventListener('attention:changed', function () {
    if (openRow && !openRow.isConnected) close();
  });
  WIDE.addEventListener('change', function () { if (!WIDE.matches && !panel.hidden) close(); });
  addEventListener('scroll', function () { if (!panel.hidden) place(); }, {passive: true});
  addEventListener('resize', function () { if (!panel.hidden) place(); });
})();
