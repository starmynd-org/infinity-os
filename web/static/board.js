/* The board's drag, and it is an AFFORDANCE ON A VERB rather than a mechanism. Bus row 0436.
 *
 * `web/MUST-NOT-BUILD.md` item 3 was overruled for this one board on 2026-08-28 with one
 * condition, which is the operator's requirement and not a hedge on it:
 *
 *     A COLUMN IS A PROJECT STATE, AND MOVING BETWEEN COLUMNS CALLS A REGISTERED TRANSITION
 *     WHOSE ARGUMENT IS THAT STATE. The drop is an affordance on a verb, never a written
 *     coordinate.
 *
 * SO THIS FILE WRITES NOTHING AND POSTS NOTHING. Every move is the `<form class="act-form">`
 * already rendered in each column, which posts to the one write door `/board/act` with the
 * room-scoped CSRF token and is handled by `console.js`'s existing submit handler. All this
 * script does is fill that form in and put the cursor in the reason field. Turn it off and the
 * board still moves projects; the drag is faster, not load-bearing.
 *
 * IT ALSO NEVER MOVES THE CARD. The card is rendered in the column its `brain.project.state`
 * names, and it moves when the store says it moved and the page re-reads. A script that
 * optimistically re-parented the node would be showing the operator a state the store does not
 * hold, which is precisely the invisible second state machine item 3 exists against -- and the
 * store WILL refuse some drops, because a reason is required in both directions.
 *
 * NO DRAG-TO-REORDER. `MUST-NOT-BUILD` item 2 is KEPT. There is no handle inside a column, no
 * drop position, no index anywhere in this file, and cards sort by the store's own ORDER BY.
 */
(function () {
  'use strict';

  var dragging = null;

  /* `drops()` is queried live, on the one path that needs every drop zone at once. There is no
   * `cards()` any more: nothing holds a card node between events now that the listeners are
   * delegated, and a helper returning a list that is stale by the next poll is a trap. */
  function drops() { return document.querySelectorAll('.bddrop'); }

  /* Fill the target column's form with the project that was dropped, then hand the operator the
   * reason field. The submit is his: the drop selects the argument, it does not run the verb. A
   * drag that ran the transition on release would be a state change with no reason attached, and
   * the table refuses that anyway. */
  function arm(col, slug) {
    var form = col.querySelector('form.bdmove');
    if (!form) { return; }
    var sel = form.querySelector('select[name=id]');
    var why = form.querySelector('input[name=text]');
    if (!sel) { return; }
    var found = false;
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === slug) { sel.selectedIndex = i; found = true; break; }
    }
    /* A card dropped on the column it is already in has no option to select, because the form
     * lists only projects that are somewhere else. Say nothing and change nothing: the server
     * would refuse it as a no-op and a receipt for a change that did not happen is how two
     * surfaces come to disagree. */
    if (!found) { return; }
    if (why) { why.focus(); }
  }

  function onDragStart(e, card) {
    dragging = card.getAttribute('data-slug');
    card.classList.add('dragging');
    try {
      e.dataTransfer.setData('text/plain', dragging);
      e.dataTransfer.effectAllowed = 'move';
    } catch (err) { /* older engines: `dragging` above is the fallback and is enough */ }
  }

  function onDragEnd(e, card) {
    card.classList.remove('dragging');
    dragging = null;
    drops().forEach(function (d) { d.classList.remove('over'); });
  }

  function onDragOver(e, drop) { e.preventDefault(); drop.classList.add('over'); }
  function onDragLeave(e, drop) { drop.classList.remove('over'); }

  function onDrop(e, drop) {
    e.preventDefault();
    drop.classList.remove('over');
    var slug = dragging;
    try { slug = e.dataTransfer.getData('text/plain') || dragging; } catch (err) { /* as above */ }
    if (!slug) { return; }
    var col = drop.closest('.bdcol');
    if (col) { arm(col, slug); }
  }

  /* KEYBOARD REACHES THE SAME PLACE, because a drag that is the only way in is a control half the
   * operator's own asks (item 3 keyboard-first) cannot use. Focus a card, press Enter or Space,
   * and the SELECT in every other column already offers it -- so the keyboard route is the form
   * itself and this only moves the cursor to the first one. */
  function onKey(e, card) {
    if (e.key !== 'Enter' && e.key !== ' ') { return; }
    var slug = card.getAttribute('data-slug');
    var here = card.closest('.bdcol');
    var cols = document.querySelectorAll('.bdcol');
    for (var i = 0; i < cols.length; i++) {
      if (cols[i] !== here) { e.preventDefault(); arm(cols[i], slug); return; }
    }
  }

  /* ONE BINDING, ON A NODE NO REPAINT CAN REPLACE. Lane D5, 2026-08-30.
   *
   * THIS FILE USED TO BIND TO THE CARDS AND THE DROP ZONES THEMSELVES, at `defer` time, and
   * those nodes do not survive the page. `board.html` declares `.bdcols` a patch region and
   * `console.js` boots with one immediate `patch()` before the three second timer starts, so
   * `[data-region="bdcols"]`'s innerHTML is reassigned about 350ms after load. The cards a
   * person then drags are DIFFERENT OBJECTS with no listeners on them.
   *
   * MEASURED BEFORE THIS CHANGE, on a seeded scratch board: with the poll blocked at the
   * browser the drag armed the form at 10 seconds, every time; with the poll allowed it armed
   * nothing after 1, 2 or 5 patch cycles. No `dragging` class, no `over` highlight, no armed
   * select, no focused reason field. A person who reads the board for two seconds before
   * dragging was always outside the working window, and the suite that covered this passed
   * because it dragged inside it.
   *
   * SO THE LISTENERS MOVE UP TO `document`, WHICH IS THE ONLY NODE GUARANTEED TO OUTLIVE ANY
   * REPAINT. Every drag event here bubbles, so delegation loses nothing, and each handler is
   * gated on the board's own class before it runs, so nothing outside `.bdcard` / `.bddrop`
   * can reach one. Re-binding after each patch was the other candidate and is worse: it needs
   * a hook `console.js` does not publish, and it would put this file's correctness back on the
   * order two independent modules happen to run in, which is the defect being repaired.
   *
   * NOTHING ELSE ABOUT THE DRAG CHANGES. It still fills the column's form in and focuses the
   * reason field; it still posts nothing, writes nothing and moves no card. The condition item
   * 3's overrule was granted under is untouched: the verb is the form's, and the form is the
   * one write door. */
  function on(type, sel, fn) {
    document.addEventListener(type, function (e) {
      var t = e.target;
      var node = t && t.closest ? t.closest(sel) : null;
      if (node) { fn(e, node); }
    });
  }

  on('dragstart', '.bdcard', onDragStart);
  on('dragend', '.bdcard', onDragEnd);
  on('keydown', '.bdcard', onKey);
  on('dragover', '.bddrop', onDragOver);
  on('dragleave', '.bddrop', onDragLeave);
  on('drop', '.bddrop', onDrop);
})();
