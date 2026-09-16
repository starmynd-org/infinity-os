/* The board's right-hand details panel (A4 INFINITY-STREAMLINE, Andrew's D-INFINITY-UX-RULINGS-1,
 * 2026-09-14, amending MUST-NOT-BUILD item 2 condition 3). At wide widths a press on a board row
 * opens that row's task page beside the table: ONE GET of the href the row already carries, and its
 * `section[data-task-page]` lifted in. Of that page's forms only Accept work and Send back are kept
 * (the two the ruling names); every other form is removed and the full page is one link away. The
 * kept forms are wired by the console's own `wire()` through `console:rewire`, so they post to the
 * same `/<room>/act` door with the same room token as on /task/<id>. This file posts nothing.
 * Narrow widths and modified presses follow the row's link exactly as before. */
(function () {
  'use strict';
  const WIDE = window.matchMedia('(min-width: 1100px)');
  const ALLOWED = ['accept_work', 'send_back'];
  const TASK_HREF = /^\/task\/[^/?#]+/;
  const panel = document.querySelector('[data-board-panel]');
  const table = document.querySelector('.tbl');
  if (!panel || !table) return;
  const body = panel.querySelector('[data-board-panel-body]');
  const full = panel.querySelector('[data-board-panel-full]');
  const title = panel.querySelector('[data-board-panel-title]');
  let openRow = null;
  let ticket = 0;

  function place() {
    const header = document.querySelector('body > header');
    panel.style.top = (header ? Math.max(0, header.getBoundingClientRect().bottom) : 0) + 'px';
  }

  function mark(row) {
    if (openRow) openRow.classList.remove('bp-row-open');
    openRow = row;
    if (row) row.classList.add('bp-row-open');
  }

  function close() {
    ticket += 1;
    panel.hidden = true;
    document.body.classList.remove('bp-panel-open');
    mark(null);
  }

  /* Only the two forms the ruling allows survive the lift. A form whose action is anything else
   * (the stopwatch, an image attach) is replaced by a sentence pointing at the full page, so the
   * panel never offers a write the ruling did not name. */
  function keepAllowedForms(root) {
    Array.prototype.forEach.call(root.querySelectorAll('form'), function (form) {
      const action = form.querySelector('input[name="action"]');
      if (action && ALLOWED.indexOf(action.value) !== -1) return;
      const note = document.createElement('p');
      note.className = 'hint';
      note.textContent = 'More actions for this task are on its full page.';
      form.replaceWith(note);
    });
  }

  function fill(html, status) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const section = doc.querySelector('section[data-task-page]');
    body.textContent = '';
    if (!section) {
      body.textContent = 'The task page answered ' + status + ' without a record this panel can ' +
                         'show. Open the full page instead.';
      return;
    }
    const lifted = document.importNode(section, true);
    keepAllowedForms(lifted);
    body.appendChild(lifted);
    const heading = body.querySelector('.ctitle');
    title.textContent = heading ? heading.textContent : 'Task detail';
    document.dispatchEvent(new Event('console:rewire'));
    body.scrollTop = 0;
  }

  function show(href, row) {
    const mine = ++ticket;
    const url = new URL(href, location.href);
    mark(row);
    full.setAttribute('href', url.href);
    title.textContent = 'Task detail';
    body.textContent = 'Reading this task…';
    panel.hidden = false;
    document.body.classList.add('bp-panel-open');
    place();
    fetch(url.href, {credentials: 'same-origin', headers: {Accept: 'text/html'}})
      .then(function (r) { return r.text().then(function (text) { return {status: r.status, text: text}; }); })
      .then(function (res) { if (mine === ticket) fill(res.text, res.status); })
      .catch(function (e) {
        if (mine !== ticket) return;
        body.textContent = 'This panel could not read the task: ' + String(e) + '. Open the full page instead.';
      });
  }

  table.addEventListener('click', function (e) {
    if (!WIDE.matches || e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const row = e.target.closest('tbody tr');
    if (!row) return;
    const link = row.querySelector('td.tid a[href]');
    if (!link) return;
    /* TASK ROWS ONLY. A board row can link a question (`/question/<id>`) as well as a task, and this
     * panel only knows the task page's record and forms. Intercepting a question row replaced a
     * working link with a dead end (measured in slot 3b: the first linked row was q0014), so every
     * other href is left to the browser, exactly as below the breakpoint. */
    if (!TASK_HREF.test(link.getAttribute('href'))) return;
    const pressed = e.target.closest('a[href]');
    if (pressed && pressed !== link) return;
    e.preventDefault();
    show(link.getAttribute('href'), row);
  });

  panel.addEventListener('click', function (e) {
    if (e.target.closest('[data-board-panel-close]')) close();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !panel.hidden) close();
  });
  WIDE.addEventListener('change', function () { if (!WIDE.matches && !panel.hidden) close(); });
  addEventListener('scroll', function () { if (!panel.hidden) place(); }, {passive: true});
  addEventListener('resize', function () { if (!panel.hidden) place(); });
})();
