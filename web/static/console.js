/* The console's whole client. No framework, no bundler, no web font, no service worker.
 *
 * Three jobs:
 *   1. poll and patch every three seconds, SKIPPING ANY REGION HOLDING THE FOCUSED INPUT
 *   2. post actions to the one write door and show what came back
 *   3. celebration, which fires on events ARRIVING FROM the system and never on a click
 *
 * No SSE and no websocket, by prohibition and also because a laptop that sleeps leaves a
 * half-open connection behind and a poll leaves nothing.
 */
(function () {
  'use strict';

  var POLL_MS = 3000;
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------------------------------------------------------- theme */
  var tb = document.getElementById('tb');
  if (tb) tb.onclick = function () {
    var c = document.documentElement.getAttribute('data-theme');
    var n = c === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', n);
    try { localStorage.setItem('vd-theme', n); } catch (e) {}
    document.cookie = 'vd_theme=' + n + ';path=/;max-age=31536000;samesite=lax';
  };

  /* deep work is a cookie so the SERVER knows: the fast pane is rendered server side, and a
   * client-only deep work would still ship the interrupting HTML down the wire. */
  var params = new URLSearchParams(location.search);
  if (params.has('deep')) {
    document.cookie = 'io_deep=' + (params.get('deep') === '1' ? '1' : '0') +
      ';path=/;max-age=31536000;samesite=lax';
  }

  /* ------------------------------------------------------- focus-safe patch */
  function focusedRegion() {
    var a = document.activeElement;
    if (!a) return null;
    var tag = (a.tagName || '').toLowerCase();
    if (tag !== 'input' && tag !== 'textarea' && tag !== 'select' && !a.isContentEditable) {
      return null;
    }
    return a.closest('[data-region]');
  }

  /* Instrumentation, not behaviour: the focus test has to prove a repaint REALLY happened
   * while the input was focused, and "the value survived" is worth nothing if nothing repainted. */
  window.__patches = 0;
  window.__patchedRegions = [];
  window.__skippedRegions = [];

  /* THE COMPARISON IS AGAINST WHAT THE SERVER LAST SENT, and never against live innerHTML.
   * Two separate things make live innerHTML permanently unequal to the string it was assigned
   * from, so a DOM-to-wire comparison answers "changed" on a page where nothing changed:
   *
   *   1. wire() below stamps data-wired="1" into the DOM after every patch, so every region
   *      holding a wired element differs from the served markup the moment it is wired.
   *   2. The browser re-serialises what it parsed. Jinja wraps attribute lists across newlines
   *      and the parser normalises them to spaces, which is why `tiers` -- which holds zero
   *      wired elements -- churned too.
   *
   * Measured before this line existed: three of eight regions swapped on every poll of an idle
   * queue, twenty times a minute, which is what destroyed an opened Defer panel in under a
   * second. One string per region kills both causes together.
   */
  var served = Object.create(null);

  function patch() {
    var room = document.body.getAttribute('data-room');
    if (!room || document.hidden || document.getElementById('stackwrap')) return;
    fetch('/api/patch/' + room + location.search, { headers: { 'x-console-poll': '1' } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var skip = focusedRegion();
        window.__patches++;
        Object.keys(j.regions || {}).forEach(function (name) {
          var host = document.querySelector('[data-region="' + name + '"]');
          if (!host) return;
          /* THE RULE: a repaint never takes the keyboard out from under a sentence being
           * typed. The region holding the focused input is left exactly as it is, including
           * its value, its selection, and the enabled state of its submit button. */
          if (skip && (host === skip || host.contains(skip))) {
            window.__skippedRegions.push(name);
            /* `served` is deliberately NOT updated here. The change is deferred, not dropped:
             * the next poll after the caret leaves still sees it as new and paints it. */
            return;
          }
          var html = j.regions[name];
          if (served[name] !== html) {
            host.innerHTML = html;
            served[name] = html;
            window.__patchedRegions.push(name);
          }
        });
        wire();
      })
      .catch(function () { /* a poll that fails is a poll; the next one is in three seconds */ });
  }

  /* ------------------------------------------- the transient hosts, kept in the viewport */
  /* ROW 0409's SECOND HALF, AND IT IS A GEOMETRY DEFECT RATHER THAN AN ABSENT RECEIPT.
   *
   * `#saybar` and `#celebrate` are the first children of `.shell`, so both render at the TOP of
   * a document the operator scrolls. The receipt for his own act therefore exists, lands on
   * time, and lands where he is not looking. Measured on a real console 2026-08-28, accepting
   * the LAST card of a three-card Judge tier in a 757px viewport at scrollY 543:
   *
   *   #saybar banner   `0002 accepted by operator`     top -395px   inView false   at 470ms
   *   receipt stripe   `✓ 0002 accepted by operator`   top -145px   inView false   at 770ms
   *   the card he clicked (button at y 357) left the DOM, and scrollY moved 543 -> 472
   *
   * From inside his viewport: a card vanished, the page shifted, and nothing said anything.
   * "Nothing happened when I accepted work" was an accurate report of what was on his screen.
   * The SAME accept at scrollY 0 puts those two at 77px and 327px, both visible -- which is why
   * this reads as intermittent rather than broken, and why it survived a green driver run.
   *
   * So the transient hosts stop living at a document coordinate. Fixed under the sticky header,
   * stacked in markup order, taking no space when empty -- which also stops the page REFLOWING
   * when a message arrives and again when it clears, and moving what somebody is reading is the
   * thing item 9 is about.
   *
   * NOT A NEW SURFACE AND NOT A NOTIFICATION. Both hosts already existed, already held exactly
   * these two sentences, and the durable record is still the receipt stripe in the list, where
   * item 10 requires it and where the inverse verb lives. This moves two divs.
   *
   * The position is set from here rather than from `console.css` because this lane owns
   * `console.js` and not the stylesheet; everything about how they LOOK is still `.banner` and
   * `.receipt` in the stylesheet. Deep work absents `#celebrate` from the markup entirely, so
   * this is a no-op for it there and touches only the operator's own receipt.
   */
  var HOST_GAP = 8;

  function placeTransients() {
    var shell = document.querySelector('.shell');
    if (!shell) return;
    var box = shell.getBoundingClientRect();
    var cs = getComputedStyle(shell);
    var padL = parseFloat(cs.paddingLeft) || 0;
    var padR = parseFloat(cs.paddingRight) || 0;
    var hd = document.querySelector('header');
    var top = (hd ? Math.round(hd.getBoundingClientRect().height) : 0) + HOST_GAP;
    ['saybar', 'celebrate'].forEach(function (id) {
      var host = document.getElementById(id);
      if (!host) return;
      var full = !!host.firstChild;
      host.style.position = 'fixed';
      host.style.left = Math.round(box.left + padL) + 'px';
      host.style.width = Math.round(box.width - padL - padR) + 'px';
      host.style.zIndex = '35';
      /* An empty host is zero-height, but it still spans the shell. `none` so it can never eat
       * a click meant for the card under it. */
      host.style.pointerEvents = full ? 'auto' : 'none';
      host.style.top = top + 'px';
      if (full) top += Math.round(host.getBoundingClientRect().height) + HOST_GAP;
    });
  }

  /* --------------------------------------------------------------- actions */
  var sayTimer = null;

  function say(msg, kind, clear_after) {
    var host = document.getElementById('saybar');
    if (!host) return;
    if (sayTimer) { clearTimeout(sayTimer); sayTimer = null; }
    host.innerHTML = '';
    if (!msg) { placeTransients(); return; }
    /* textContent, not innerHTML: this now states receipts, and a receipt carries item titles,
     * which are agent-written text. */
    var b = document.createElement('div');
    b.className = 'banner ' + (kind || '');
    b.textContent = msg;
    host.appendChild(b);
    placeTransients();
    /* A refusal stays until something replaces it. A statement of what just happened does not:
     * a banner that outlives the act it describes stops being a receipt and becomes decoration. */
    if (clear_after) {
      sayTimer = setTimeout(function () { host.innerHTML = ''; placeTransients(); }, clear_after);
    }
  }

  function post(form) {
    var data = new FormData(form);
    var btn = form.querySelector('button[type=submit], button.act, button.sec');
    if (btn) btn.disabled = true;
    /* The form's own action, which is `/<room>/act` and carries the room-scoped CSRF token the
     * server rendered into it. Not a constant URL with the room in the body: that is the shape
     * that let a request from Study post `room=queue` and get the Queue's verbs. */
    return fetch(form.getAttribute('action'), { method: 'POST', body: data })
      .then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (res) {
        if (!res.j.ok) {
          say(res.j.error, res.s === 403 ? 'refusal' : '');
          if (btn) btn.disabled = false;
          return;
        }
        /* NOTHING SUCCEEDS SILENTLY. The receipt is stated here and not only in the receipt
         * stripe, because not every surface has one: /scope renders zero [data-region] hosts, so
         * a Post it that created a work item left the operator with a grey button and no other
         * signal at all. Where a stripe does render, this is the same sentence twice for seven
         * seconds, which is the cheap side of the trade. */
        say(res.j.receipt + (res.j.note ? ' · ' + res.j.note : ''), 'ok', 9000);
        /* A CLICK STILL NEVER CELEBRATES ITSELF, AND IT IS NO LONGER THIS LINE THAT SAYS SO.
         *
         * This read `if (res.j.celebrate_from) since = res.j.celebrate_from;` under the comment
         * "A click never celebrates itself: the cursor jumps past this write." The rule is
         * right. The instrument was wrong: `celebrate_from` is `max(seq)` over the WHOLE
         * thread, not the seq of this write, so jumping to it stepped over everything anybody
         * else had done since the last poll as well.
         *
         * Reproduced 2026-08-28 on `brain_c0409`: agent T9 lands a `done`, the operator presses
         * Accept 200ms later, and T9's `done` is never celebrated at all -- 12 seconds of
         * silence, where the identical `done` with no click fires reliably at ~2.1s. His click
         * deleted somebody else's event, silently, and no test or note anywhere records it.
         *
         * The server now excludes his own acts by `from_agent` (`model.celebrations(mine=)`), so
         * the exclusion is an identity and cannot take bystanders with it. The cursor goes back
         * to being only a position and advances on the poll, like every other cursor here.
         *
         * `celebrate_from` is still sent by `/queue/act` and is deliberately left in the
         * response: it is a fact about the store at the moment of the write and other readers
         * may want it. Nothing consumes it here any more.
         */
        /* THE FOCUS GUARD PROTECTS A SENTENCE BEING TYPED, and this sentence has been spent.
         * Leaving the caret in the field made the guard suppress the one region that had to
         * change: the receipt for this very write renders in the queue's list, so every gated
         * verb -- Send back, Bump it, Move it out of Decide, Answer, Post it, Decline, Mark my
         * task done -- landed its write and showed nothing. Blur, then patch. */
        var focused = document.activeElement;
        if (focused && focused.blur) focused.blur();
        /* Re-enabled on SUCCESS as well as on failure. The grey button was the only signal that
         * anything had happened, and a button that never comes back is how the stack bricked.
         * The form is reset first so a gated button returns to disabled-because-empty by its own
         * gate rather than by this line, and the spent text is not left sitting in the field. */
        if (btn) btn.disabled = false;
        form.reset();
        form.querySelectorAll('[data-gate]').forEach(function (g) {
          g.dispatchEvent(new Event('input'));
        });
        /* The stack does not poll and has no regions, so patch() is a no-op there and the mode
         * dead-ended on its first decision. It advances by navigating, exactly as `skip for now`
         * already does; the server states where next in data-next. */
        var wrap = document.getElementById('stackwrap');
        if (wrap && wrap.dataset.next) { location.assign(wrap.dataset.next); return; }
        patch();
      })
      .catch(function (e) { say(String(e)); if (btn) btn.disabled = false; });
  }

  function wire() {
    document.querySelectorAll('form.act-form').forEach(function (f) {
      if (f.dataset.wired) return;
      f.dataset.wired = '1';
      f.addEventListener('submit', function (e) { e.preventDefault(); post(f); });
    });
    /* Send back stays disabled on empty and on a too-short reason. */
    document.querySelectorAll('[data-gate]').forEach(function (ta) {
      if (ta.dataset.wired) return;
      ta.dataset.wired = '1';
      var target = document.getElementById(ta.dataset.gate);
      var min = parseInt(ta.dataset.min || '12', 10);
      var check = function () {
        var value = ta.value.trim();
        var size = value.length;
        if (ta.dataset.normalize === 'words') {
          // Python str.split whitespace, including its extra control separators;
          // count Unicode code points as the server does, not UTF-16 code units.
          value = ta.value.replace(/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g, ' ').replace(/^ +| +$/g, '');
          size = Array.from(value).length;
        }
        if (target) target.disabled = size < min;
      };
      ta.addEventListener('input', check);
      check();
    });
    /* Run the stack must return to the tier AND the scroll position it was opened from, so the
     * link carries the scroll offset the server cannot know. */
    document.querySelectorAll('a.runbtn, a.burn').forEach(function (a) {
      if (a.dataset.wired) return;
      a.dataset.wired = '1';
      a.addEventListener('click', function () {
        a.href = a.href + (a.href.indexOf('?') < 0 ? '?' : '&') + 'scroll=' +
          Math.round(window.scrollY);
      });
    });
    document.querySelectorAll('[data-toggle]').forEach(function (b) {
      if (b.dataset.wired) return;
      b.dataset.wired = '1';
      b.addEventListener('click', function () {
        var t = document.getElementById(b.dataset.toggle);
        if (t) t.hidden = !t.hidden;
      });
    });
  }

  document.addEventListener('console:rewire', wire);

  /* ----------------------------------------------------------- celebration */
  /* DEEP WORK TURNS THIS WHOLE BLOCK OFF, AND NOT BY LETTING IT FALL THROUGH (task 0235).
   * `base.html` stopped emitting `#celebrate` and `<canvas id="cf">` under `io_deep=1` in the
   * same change, so `cv` is null here and `burst()`'s `if (!cx) return` would already swallow
   * every particle. THAT IS NOT ENOUGH AND WAS NEVER THE CONTRACT. Two things are wrong with
   * resting on it:
   *
   *   1. The poller ran regardless. `setInterval(celebrate, POLL_MS)` at the boot below was
   *      unconditional, so a mode whose whole promise is that nothing reaches the operator was
   *      asking `/api/celebrate` every three seconds what there was to celebrate. The events
   *      came back and were dropped on the floor -- an accident of code order, one `innerHTML`
   *      away from becoming a receipt across the reading surface.
   *   2. `fit()` and the `resize` listener are viewport work for a canvas that is not there.
   *
   * `deep` reads the class the SERVER set (`base.html`), so the client and the markup agree by
   * construction rather than by a second cookie read that could disagree with the response it
   * is running inside. Nothing here is a visual suppression: with the mode on there is no host,
   * no canvas, no timer and no fetch. */
  var deep = document.body.classList.contains('deep');
  var since = parseInt(document.body.getAttribute('data-since') || '0', 10);
  var fired = 0;
  var cv = deep ? null : document.getElementById('cf');
  var cx = cv ? cv.getContext('2d') : null;
  var parts = [];

  function fit() { if (cv) { cv.width = innerWidth; cv.height = innerHeight; } }
  if (!deep) { fit(); addEventListener('resize', fit); }

  function burst(n) {
    if (!cx) return;
    var cs = ['#F2913D', '#5FD8E8', '#F3C34A', '#F2789F', '#AA8FF2', '#EFF3FA'];
    for (var i = 0; i < n; i++) parts.push({
      x: innerWidth / 2, y: innerHeight * 0.42, vx: (Math.random() - 0.5) * 11,
      vy: Math.random() * -10 - 3, g: 0.28, r: Math.random() * 3 + 1.5,
      c: cs[i % cs.length], life: 1
    });
    if (parts.length) requestAnimationFrame(tick);
  }

  function tick() {
    cx.clearRect(0, 0, cv.width, cv.height);
    var live = false;
    parts.forEach(function (p) {
      p.vy += p.g; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.life -= 0.011;
      if (p.life > 0 && p.y < cv.height + 20) {
        live = true; cx.globalAlpha = Math.max(p.life, 0); cx.fillStyle = p.c;
        cx.beginPath(); cx.arc(p.x, p.y, p.r, 0, 7); cx.fill();
      }
    });
    cx.globalAlpha = 1;
    if (live) requestAnimationFrame(tick);
    else { parts = []; cx.clearRect(0, 0, cv.width, cv.height); }
  }

  /* WHAT GETS PARTICLES, AND WHY IT IS NOW ONLY TIER 2. Row 0409.
   *
   * Reproduced 2026-08-28: the operator sits reading an opened panel, makes no write of his
   * own, and a CLI `done` in another terminal paints a full-viewport burst at him +2.12s later,
   * centred at 42% of the screen, over whatever he was reading. His word for it was "randomly".
   *
   * Three things are true about that burst and each one of them argues the same way:
   *
   *   1. It is fired by somebody else's act, on a READING surface, with no way to ask for it
   *      and no way to dismiss it. Item 9's posture is that nothing interrupts work; the mode
   *      that enforces it absolutely happens to be deep work, but the reason is not.
   *   2. It leaves NO TRACE. The badge clears after seven seconds and nothing records that it
   *      happened, so a burst the operator half-saw cannot be traced back to its cause. Item 10
   *      is the same argument about cards: nothing vanishes without a receipt.
   *   3. It fires on `done`, which is routine fleet throughput. A burst that goes off every
   *      time work completes is a reward for work completing, which is what item 8 forbids in
   *      the sentence "nothing rewards clearing an item". Item 8 was PUT TO THE OPERATOR TODAY
   *      AND KEPT.
   *
   * Tier 2 keeps its particles, and that is the whole of what a celebration is for here: a
   * wager verdict is the ONLY exogenous confirmation of value in this system (`celebrations()`
   * says so), it is rare, and it appears nowhere else on this surface. Tier 1 keeps its
   * SENTENCE -- it now names the actor, the item and the item's title instead of `0013 done by
   * T9` -- and loses the motion. A statement he can ignore is not an interruption; a burst he
   * cannot is.
   *
   * WHAT THIS DOES NOT DO, stated because it is the part somebody will want to add back. It
   * does not celebrate his own act. He asked for exactly that -- "that would maybe be a moment
   * for confetti" -- and it is the one half of his ask that lands AGAINST item 8 rather than
   * inside it: an acceptance clears an item, so confetti on every acceptance is a reward that
   * fires when the queue goes down. What his ask needs is that the act be VISIBLE, and that is
   * the receipt, delivered by `placeTransients()` above. The collision is left recorded here
   * for the operator to rule on rather than decided by a lane.
   */
  function celebrate() {
    if (document.hidden) return;
    fetch('/api/celebrate?since=' + since).then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.events || !j.events.length) { since = j.head || since; return; }
        var top = j.events[0];
        var host = document.getElementById('celebrate');
        if (host) {
          /* textContent, NEVER a concatenated innerHTML. `text` now carries the work item's
           * title, which is agent-written text arriving over the wire; `say()` above made the
           * same choice for the same reason and this host had been the exception. */
          host.innerHTML = '';
          var card = document.createElement('div');
          card.className = 'receipt';
          var mark = document.createElement('b');
          mark.textContent = top.tier === 2 ? '◆' : '✓';
          card.appendChild(mark);
          var line = document.createElement('span');
          line.textContent = ' ' + top.text;
          card.appendChild(line);
          host.appendChild(card);
          placeTransients();
          setTimeout(function () { host.innerHTML = ''; placeTransients(); }, 7000);
        }
        /* prefers-reduced-motion already replaced the burst with the static badge. Tier 1 now
         * takes the same path for everybody, for the reasons above; the badge was always the
         * statement and the particles were always decoration on top of it. */
        if (!reduce && top.tier === 2 && fired < 4) { burst(120); fired++; }
        since = j.head || since;
      }).catch(function () {});
  }

  /* ------------------------------------------------------------------ boot */
  wire();
  /* BEFORE the first paint of anything transient, and again on resize: the hosts are pinned to
   * the viewport, so their left and width are the shell's and both change with the window.
   * Empty hosts are zero-height, so doing this on an idle page costs two style writes and moves
   * nothing. */
  placeTransients();
  addEventListener('resize', placeTransients);
  if (params.has('scroll')) {
    window.scrollTo(0, parseInt(params.get('scroll'), 10) || 0);
  }
  /* ONE PATCH IMMEDIATELY, and it is not an optimisation. `served` starts empty, so whichever
   * poll runs first paints every region once whether or not anything changed; run at boot that
   * repaint lands about seventy milliseconds after load and destroys nothing, and run three
   * seconds later it lands exactly where an operator who clicked `Defer ▾` on arrival has a panel
   * open. Seeding `served` from the live DOM instead would be the bug this file just fixed:
   * innerHTML is the browser's re-serialisation and is not what the server sent. */
  patch();
  setInterval(patch, POLL_MS);
  /* THE TIMER IS NOT STARTED IN DEEP WORK. See the celebration banner above: absenting the
   * host without stopping the ask would leave the mode's promise resting on code order. Any
   * particle state a previous document left behind goes with it -- entering deep work is a
   * navigation, so this document starts empty, and clearing it here says so rather than
   * assuming it. */
  if (deep) {
    parts.length = 0;
    if (cx) cx.clearRect(0, 0, cv.width, cv.height);
  } else {
    setInterval(celebrate, POLL_MS);
  }
  addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var close = document.getElementById('stack-close');
      if (close) close.click();
    }
  });
})();

/* =========================================================================================
 * V10 lane sections (SHELL-0, task 0156). Each build lane adds ONE self-contained block under
 * its own banner below. Nobody edits `patch()` or `wire()` above: that core is V-SHELL's
 * (OWNERSHIP-MAP section 7), and V4's poller is a SIBLING of `patch()` keyed by stream
 * position (C section 2.5.1), honouring the same never-replace-activeElement law -- shared
 * invariant, no shared lines.
 * ========================================================================================= */
// ---- V3: option rail -- radiogroup selection, focus swap, no-stray-Enter (C 1.3-1.5) ----
(function () {
  'use strict';
  /* ONE DELEGATED LISTENER ON `document`, AND THAT IS THE WHOLE DESIGN. The queue repaints every
   * three seconds by replacing a region's innerHTML, and `wire()` inside the core IIFE re-stamps
   * per-element handlers after each patch. This block is outside that IIFE by construction
   * (SHELL-0's banner: no lane edits `wire()` or `patch()`), so a per-element handler here would
   * be wired once and lost on the first repaint. A listener on `document` survives every repaint
   * and needs no re-wiring at all.
   *
   * WHAT THIS CODE MAY NOT DO, and each of these is a rule rather than a preference:
   *
   *   1. It never checks a radio. No option is selected on arrival, ever. A pre-selected option
   *      is a default ridden rather than a choice made, and the recommendation pays the same one
   *      tap as every other row (C 1.3).
   *   2. It never focuses the verb button. Selecting a row moves focus to the row -- the browser
   *      does that itself for a radio -- and the button is a second, deliberate act on a second
   *      target. `.focus()` on the button would collapse two acts into one keystroke and delete
   *      the mechanical half of "a drafted option cannot dispatch without a human choosing it".
   *   3. It never submits anything. The rail is not inside a form; the only submit in this
   *      component is the operator pressing the verb button, which the core's own `act-form`
   *      handler posts. A stray Enter on a focused row has no form under it to submit.
   *
   * The radios are real `<input type=radio>` and not ARIA-decorated divs, which buys three
   * things for free: native arrow-key navigation inside the group, native focus, and the core
   * patcher's activeElement guard -- `focusedRegion()` skips a region whose focused element is an
   * input, so a repaint cannot take a selection out from under the operator mid-read. */
  var TOKEN = 'ofocus';

  function swap(id, n) {
    var host = document.getElementById('opts-' + id);
    if (!host) return;
    host.querySelectorAll('[data-optempty="' + id + '"]').forEach(function (e) {
      e.hidden = true;
    });
    host.querySelectorAll('[data-optfocus="' + id + '"]').forEach(function (b) {
      /* `hidden` and not `display:none` in a class: the block that is not selected is out of the
       * accessibility tree as well as off the screen, so a screen reader reads one plan and one
       * counterargument, which is the same reading load the sighted reader carries. */
      b.hidden = (b.getAttribute('data-n') !== String(n));
    });
    host.querySelectorAll('.orow').forEach(function (r) {
      var input = r.querySelector('input[type=radio]');
      r.classList.toggle('on', !!(input && input.checked));
    });
  }

  document.addEventListener('change', function (e) {
    var t = e.target;
    if (!t || t.type !== 'radio' || !t.getAttribute('data-opt')) return;
    swap(t.getAttribute('data-opt'), t.value);
  });

  /* A repaint restores the served markup, in which every focus block is `hidden` and no radio is
   * checked -- which is correct, because the served markup is the honest state of a card nobody
   * has chosen on yet. Nothing is re-applied here on purpose: re-checking a radio after a
   * repaint would be this file deciding a selection the operator did not make on the card the
   * server just sent. The patcher already refuses to repaint a region holding the focused input,
   * so a live selection is not repainted away while he is reading it.
   *
   * `TOKEN` is referenced so this block's one constant is not dead: the id prefix the markup and
   * this file must agree on. */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    var t = e.target;
    if (t && t.type === 'radio' && t.getAttribute('data-opt')) {
      /* Belt and braces over rule 3: even inside some future form, Enter on a row is inert. It
       * re-states the selection and dispatches nothing. */
      e.preventDefault();
      swap(t.getAttribute('data-opt'), t.value);
    }
  });

  window.__optionRail = { swap: swap, token: TOKEN };
})();
// ---- V4: runfeed poller, take-control panel, picker pending chip (C 2.5-2.7) ----
// ---- V5: fit/full toggle, and a failed image fetch that becomes a finding (C 3.2, 3.3) ----
/* Task 0167. One self-contained block. It touches no node the poller owns and it registers its
 * listeners on `document`, so a region the poller replaces comes back wired without this file
 * knowing that the poller exists.
 *
 * DROP-TO-ATTACH IS NOT HERE, and its absence is a decision rather than an omission.
 * DESIGN-SYSTEM section 7.3 offers `Attach image` verb OR drop-with-word; a browser hands
 * JavaScript a File object and never a path, so a drop could only be honoured by uploading the
 * bytes and writing them somewhere -- a second copy of the image, in a location DESIGN-SYSTEM
 * section 10 routes to V6. The verb ships; the drop word does not, because a target that says
 * `drop to attach` and cannot record a pointer is an affordance promising what the posture
 * forbids. Raised in V-CROSSTALK slot 6. */
(function () {
  /* Fit <-> full, IN FLOW. The page grows; nothing overlays anything. */
  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-imgtoggle]') : null;
    if (!b) return;
    var box = document.getElementById(b.dataset.imgtoggle);
    if (!box) return;
    var full = box.classList.toggle('full');
    b.textContent = full ? 'fit' : 'view full size';
    b.setAttribute('aria-expanded', full ? 'true' : 'false');
  });

  /* A FETCH THAT FAILS BECOMES THE FINDING, NEVER THE BROWSER'S BROKEN GLYPH.
   *
   * The server computes MISSING at render time, so this fires only in the window between a
   * render and its fetch -- the file deleted while the card was on screen. That window is real
   * and the design's rule about it is absolute: `no broken-img glyph ever renders`. So the <img>
   * is REMOVED from the DOM and a finding is put in its place; the finding is markup, not a
   * style, which is the same rule deep work is built on. The next poll replaces this with the
   * server's own MISSING block, which carries the pointer, the host and the recorded hash. This
   * one carries what the client can prove by itself and does not guess the rest.
   *
   * Capture phase: `error` on an <img> does not bubble. */
  document.addEventListener('error', function (e) {
    var img = e.target;
    if (!img || img.tagName !== 'IMG' || !img.dataset || !img.dataset.imgfind) return;
    var box = img.parentNode;
    if (!box) return;
    img.remove();
    box.classList.add('gone');
    box.classList.remove('dim');
    var f = document.createElement('div');
    f.className = 'imgfind red bare';
    f.innerHTML = '<b>MISSING</b> \u00b7 the file was there when this card was drawn and the ' +
                  'browser could not fetch it just now' +
                  '<div class="sub">Attach ' + (img.dataset.imgfind || '') + '. The next poll ' +
                  'replaces this with the server\u2019s reading, which carries the pointer, the ' +
                  'host and the recorded hash.</div>';
    box.appendChild(f);
  }, true);
})();
// ---- V-SHELL: keyboard core, mode pill and door choreography (DS 3-4) ----
/* V-SHELL-1 (task 0175). THE MODE PILL NEEDS NO CLIENT AT ALL, and that is the design rather
 * than an omission: both segments are links, `io_dispatch` is written by the server
 * (`web/app.py`, `_mode_cookies`), and the strip is rendered server side in every room. A mode
 * whose state depends on a script is a mode that is off on the one page whose script failed.
 *
 * What DOES need a client is the change of place (DS 3.3), because only the client knows which
 * navigation was an ENTRY. The stack advances by navigating -- every decision renders a fresh
 * document -- so a CSS-only entry animation would replay the 240ms arrival on every card and
 * eat the whole press-to-readable budget DS section 5 sets at 250ms. The cursor tells them
 * apart: an entry carries no `n` and no `d` (`queue.html`'s three doors are bare
 * `/queue/stack?from=...` links), an advance always carries at least one.
 *
 * `stack.html` is not this task's file and is not touched: this adds one class to an element
 * that is already there, and the keyframes it triggers live in this lane's CSS section. */
(function () {
  'use strict';
  if (document.body.getAttribute('data-room') !== 'stack') return;
  var p = new URLSearchParams(location.search);
  if (p.has('n') || p.has('d')) return;          /* an advance, not an entry */
  var w = document.getElementById('stackwrap');
  if (w) w.classList.add('entering');
})();

// ---- V-SHELL-2: the door, Ctrl+., and the threshold line (DS 3.4, DS 6) ----
/* V-SHELL-2 (task 0176). THE DOOR. DS section 3.4 and V10-B SPEC section 4, adopted verbatim.
 *
 * THREE SENTENCES GOVERN EVERY LINE BELOW.
 *
 * 1. THE ONLY EXIT IS `leave`. The pill segment or Ctrl+. from anywhere ENTERS, including
 *    mid-Dispatch-run. Inside deep work Ctrl+. renders a one-line pointer and does nothing
 *    else. There is no Esc handler in this block and there must never be one: the core IIFE's
 *    Escape binding above clicks `#stack-close`, an element that exists only on the stack
 *    surface, so Escape in deep work already does nothing and that is the correct behaviour and
 *    not an oversight. No timer, no auto-exit on navigation, and never a confirmation dialog --
 *    a toll on leaving teaches people not to enter.
 *
 * 2. THE ANIMATION IS THEATER OVER THE STATE CHANGE, NEVER THE STATE CHANGE (DS:301). The
 *    cookie is written by `initiate()` at the top of the click handler, BEFORE any class is
 *    added and before anything moves. The elements that slide out are ghosts on the document
 *    being left; the document that arrives never contained them. Kill this whole file and both
 *    controls are still plain links that work, because the server reads the cookie and the
 *    `?deep=` arg; the swap is then instant and complete, which is the same property stated
 *    from the other end.
 *
 * 3. STATE FOLLOWS THE LAST DELIBERATE ACT, NEVER THE ANIMATION (B section 4.5). Clicking the
 *    door again mid-flight rewrites the cookie the other way, cancels the pending navigation and
 *    lets the CSS transitions unwind from wherever they actually are. A reversal that lands back
 *    on the state the document is already rendering navigates nowhere at all.
 *
 * A DELEGATED LISTENER ON `document`, per SHELL-0's banner: no lane outside the core IIFE may
 * touch `wire()` or `patch()`, so a per-element handler here would be stamped once and lost on
 * the first three-second repaint. */
(function () {
  'use strict';

  /* The two door budgets are the spec's, not this file's opinion, and they are read FROM THE
   * STYLESHEET so there is exactly one place either number lives: `--t-door-in: 560ms` and
   * `--t-door-out: 420ms` at console.css:93. A literal here would be a second copy that drifts. */
  function ms(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    var n = parseFloat(v);
    return (v.slice(-2) === 'ms' && n > 0) ? n : (v.slice(-1) === 's' && n > 0 ? n * 1000 : fallback);
  }
  var DOOR_IN = ms('--t-door-in', 560);
  var DOOR_OUT = ms('--t-door-out', 420);
  /* Phase 1 -- "the interruptions leave" -- is 0-180ms of the door and is the only phase that
   * plays on the outgoing document (B section 4.3). It is also therefore how long an ENTERING
   * click waits before it navigates.
   *
   * IT APPLIES TO ENTERING ONLY, AND THAT IS B SECTION 4.4 RATHER THAN AN OPTIMISATION. The two
   * directions are not the same choreography reversed in time; the spec reverses the ORDER of
   * the movements. Entering stages "interruptions leave, then the light, then the room settles",
   * so the first movement is on the document being left. Leaving is "light first, then the
   * furniture" -- palette 0-260ms, panes and nav returning 200-420ms -- and BOTH of those are
   * properties of the document that arrives. There is no movement on the outgoing deep document
   * to wait for, and there is nothing on it to slide out anyway: deep work already removed the
   * interruptions from the markup.
   *
   * MEASURED, WHICH IS WHY THIS IS NOT A GUESS: holding the leave click for 180ms put the
   * measured door OUT at 727ms against a 420ms budget on this host. The hold was 180ms of that.
   * The rest is the round trip, which is discussed where the budget is spent below. */
  var PHASE1 = 180;
  var KEY = 'vs2-door';

  /* WHAT THE THRESHOLD LINE SAYS, AND THE SENTENCE THAT IS NOT IN IT.
   *
   * DS section 3.4 spells this `Deep work. Nothing will reach you.` and in the same breath makes
   * it "subject to the honesty condition in section 8". DS section 8 (:528-538) is a PENDING
   * OPERATOR DECISION and it states the interim rule for implementers in as many words:
   *
   *     "The dishonest middle -- muted room, live pager, no label -- is ruled out either way.
   *      Until he decides, implementers ship the honest label, not the promise."
   *
   * and, on the branch where deep work is accepted as visual-only:
   *
   *     "section 3.4's threshold line `Nothing will reach you.` must not ship."
   *
   * Today deep work is a console cookie. No paging policy reads it -- `subscribers/
   * operator_paging/` decides on quiet hours and urgency and has never heard of `io_deep` -- so
   * a page raised right now still reaches the phone on the desk. The second sentence is a
   * promise this system cannot keep, and emitting a promise the system cannot keep is worse than
   * emitting nothing. IT IS NOT EMITTED.
   *
   * This is the one string to change when the operator answers section 8. If he chooses the
   * narrowing branch, `Nothing will reach you.` becomes true and gets appended here. If he
   * chooses visual-only, this stays exactly as it is and the pill grows section 8's label
   * instead. Either way the edit is one line in one place, which is why the sentence lives in a
   * named constant rather than inline. */
  var THRESHOLD_LINE = 'Deep work.';

  var root = document.documentElement;
  var deep = document.body.classList.contains('deep');
  var flight = null;          /* the pending navigation, and the proof a door is in flight */
  var flightTo = null;

  function cookie(k, v, maxAge) {
    document.cookie = k + '=' + v + ';path=/;max-age=' + maxAge + ';samesite=lax';
  }

  function hhmm() {
    var d = new Date(), p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(d.getHours()) + ':' + p(d.getMinutes());
  }

  /* THE STATE CHANGE. Called first, always, before a single class is added.
   *
   * The `since` clock is written HERE and in the operator's own local time, because "since
   * 14:02" on a header he is reading can only honestly mean his clock -- the console is reached
   * over Tailscale from elsewhere and the server's timezone is not his. `web/app.py` refuses
   * anything that is not HH:MM and renders an honest dash instead, so this is a hint the server
   * validates rather than a value it trusts.
   *
   * Leaving deletes the clock (max-age 0) rather than zeroing it: a mode that is off and a mode
   * that was never entered are the same state, and the jar should not carry a word for it. */
  function initiate(toDeep) {
    cookie('io_deep', toDeep ? '1' : '0', 31536000);
    if (toDeep) { cookie('io_deep_since', hhmm(), 31536000); }
    else { cookie('io_deep_since', '', 0); }
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ t0: Date.now(), to: toDeep ? 1 : 0 }));
    } catch (e) {}                    /* private mode: the door still works, unstaged */
  }

  function open(href, toDeep) {
    /* A second act on a door already in flight is a REVERSAL, not a second door. */
    if (flight !== null) {
      clearTimeout(flight); flight = null;
      root.classList.remove('doorway');
      initiate(!flightTo);            /* the cookie follows the last deliberate act */
      if ((!flightTo) === deep) { try { sessionStorage.removeItem(KEY); } catch (e) {} }
      flightTo = null;
      return;
    }
    initiate(toDeep);                 /* 1. STATE, at initiation */
    flightTo = toDeep;
    root.classList.add('doorway');    /* 2. theater, on ghosts */
    /* Entering waits out phase 1 on this document; leaving does not, per B section 4.4. An exit
     * that pauses before it starts is the ceremony that section explicitly refuses -- "leaving
     * is already decided and should not cost ceremony". */
    var hold = toDeep ? PHASE1 : 0;
    flight = setTimeout(function () { location.href = href; }, hold);
  }

  /* Ctrl+. -- and the asymmetry is the whole design (B section 4.2). Entry may be cheap: the
   * cost of an accidental entry is a moment of calm and one obvious exit. Exit may not be, so
   * this key is not a toggle. */
  addEventListener('keydown', function (e) {
    if (!e.ctrlKey || e.altKey || e.metaKey || e.key !== '.') return;
    e.preventDefault();
    if (deep) { point(); return; }    /* IN deep work: the pointer, and nothing else, ever */
    var seg = document.getElementById('seg-w');
    if (seg) open(seg.href, true);
  });

  /* THE POINTER HAS NO SERVER MARKUP AT ALL. DS section 6.2 [V10-E]: deep-work-only chrome is
   * not in the markup outside deep work, and "the Ctrl+. pointer text is written only when it is
   * shown". So it is built on the keystroke and removed again -- never rendered empty, never
   * `display:none`, nothing to leak into the other mode through a stylesheet regression. */
  function point() {
    var old = document.getElementById('deeppoint');
    if (old) { old.parentNode.removeChild(old); }
    var el = document.createElement('div');
    el.id = 'deeppoint';
    el.className = 'deeppoint';
    el.setAttribute('role', 'status');
    el.textContent = 'you are in deep work · leave with the control above';
    var hd = document.querySelector('header');
    if (!hd) return;
    hd.appendChild(el);
    setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, 6000);
  }

  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('#deepleave,#seg-w') : null;
    if (!a) return;
    /* A modified click is the operator asking the BROWSER for something (new tab, download).
     * Never swallow it: the mode would change without the document that shows it changing. */
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    open(a.href, a.id === 'seg-w');
  });

  /* ---- the far side of the crossing -------------------------------------------------------
   * Phases 2 and 3 play HERE, on the document that arrived, because the light and the measure
   * are properties of the new markup rather than of the old.
   *
   * THE BUDGET IS COUNTED FROM THE OPERATOR'S CLICK, NOT FROM THIS DOCUMENT'S LOAD. The door is
   * 560ms of the operator's life; a slow round trip does not get to make it 900. Whatever the
   * navigation spent is subtracted, and a round trip that already exceeded the budget lands the
   * line immediately rather than adding a delay to a wait that already happened.
   *
   * ONE ANNOUNCEMENT PER CROSSING (B section 9), and the staged entry is what enforces it: the
   * sessionStorage key is written by `initiate()` and removed the moment it is consumed. Walking
   * between rooms inside deep work, and every three-second poll, find nothing staged and
   * announce nothing. */
  var staged = null;
  try { staged = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) {}
  if (staged && staged.to === (deep ? 1 : 0)) {
    try { sessionStorage.removeItem(KEY); } catch (e) {}
    root.classList.add('doorlit');
    if (!deep) { root.classList.add('out'); }
    var budget = deep ? DOOR_IN : DOOR_OUT;
    var left = Math.max(0, budget - (Date.now() - staged.t0));
    setTimeout(function () {
      root.classList.remove('doorlit');
      root.classList.remove('out');
      if (deep) {
        var say = document.getElementById('deepsay');
        if (say) { say.textContent = THRESHOLD_LINE; }
      }
      /* Instrumentation, not behaviour -- the same shape as `window.__patches` in the core
       * above. The door's timings are a specified number, so they have to be MEASURABLE from
       * outside rather than asserted from the stylesheet: this is the elapsed wall clock from
       * the operator's click to the end of the door, across the navigation. */
      /* `nav` is what the ROUND TRIP cost, read from the browser's own navigation timing, and
       * it is stamped beside the total because without it the total is unreadable. The door is
       * scheduled to land at exactly `t0 + budget`; the only thing that can push it past that is
       * a navigation that alone outlasted the budget, and then it is late by precisely that
       * excess and not by a millisecond more. Reporting `ms` without `nav` would let a slow
       * server look like a slow door -- or, worse, let someone widen the budget to match a
       * Flask dev server on a DrvFs mount and call the door conformant. */
      var nav = 0;
      try {
        var e = performance.getEntriesByType('navigation')[0];
        if (e) { nav = Math.round(e.responseEnd); }
      } catch (err) {}
      window.__vs2Door = { ms: Date.now() - staged.t0, to: staged.to, budget: budget,
                           nav: nav, hold: staged.to ? PHASE1 : 0 };
    }, left);
  }
})();

/* ==========================================================================================
 * V4: THE RUNFEED POLLER. Task 0166.
 *
 * A SIBLING of patch() above, not a branch inside it, and the difference is the keying. patch()
 * swaps whole regions by name; this one APPENDS entries above a stream position it already
 * holds, and replaces exactly two nodes by id. Merging them would give one of them the other's
 * bugs.
 *
 * THE RULE IT EXISTS TO KEEP: a repaint never takes the keyboard out from under a sentence being
 * typed. Three layers, and the first is markup rather than code:
 *
 *   1. The steering input and the picker are SIBLINGS of the feed container, never children, so
 *      a repaint cannot reach them at all.
 *   2. The two in-place nodes are skipped when they contain document.activeElement.
 *   3. The feed is append-only, so nothing already on screen is re-rendered by a poll.
 *
 * v1 proved the caret at offset 6 surviving two poll cycles; `web/tests/test_runfeed_caret.py`
 * re-proves it here, with events arriving, against this poller.
 * ========================================================================================== */
(function () {
  'use strict';
  var feed = document.getElementById('feed');
  if (!feed) return;

  /* The console's cadence, and the same number the room poller uses. Declared here rather than
   * shared because that one is closed over by its own IIFE; two literals that must agree is
   * worse than one, so if this ever changes both change together. */
  var POLL_MS = 3000;
  var name = (location.pathname.split('/')[2] || '');
  var since = 0, seq = 0, timer = null;
  var seen = Object.create(null);

  /* Instrumentation, the same shape patch() carries and for the same reason: "the value
   * survived" is worth nothing if nothing repainted while it was focused. */
  window.__rfPolls = 0;
  window.__rfAppended = 0;
  window.__rfSkipped = [];

  /* The cursor comes from the SERVER, which knows which run it belongs to. Deriving it from the
   * highest data-pos on screen was wrong and measurably so: the feed shows the last three runs,
   * stream positions are per file, and an older run's file is routinely longer -- so the poller
   * asked `since=30` of a current run whose newest line was 16 and appended nothing at all. */
  var run = feed.getAttribute('data-run') || '';
  since = parseInt(feed.getAttribute('data-since'), 10) || 0;
  seq = parseInt(feed.getAttribute('data-seq'), 10) || 0;
  Array.prototype.forEach.call(feed.querySelectorAll('[data-key]'), function (el) {
    seen[el.getAttribute('data-key')] = 1;
  });

  function holdsFocus(node) {
    var a = document.activeElement;
    return !!(node && a && (node === a || node.contains(a)));
  }

  function replace(id, html) {
    var host = document.getElementById(id);
    if (!host) return;
    /* THE SKIP. A node holding the caret is left exactly as it is -- value, selection and the
     * enabled state of its button. The change is deferred, not dropped: the next poll after the
     * caret leaves paints it. */
    if (holdsFocus(host)) { window.__rfSkipped.push(id); return; }
    if (host.innerHTML !== html) host.innerHTML = html;
  }

  function poll() {
    if (document.hidden) return;
    fetch('/api/runfeed/' + encodeURIComponent(name) +
          '?since=' + since + '&seq=' + seq, { headers: { 'x-console-poll': '1' } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        window.__rfPolls++;
        replace('vt', j.vitals || '');
        replace('live', j.live || '');
        (j.entries || []).forEach(function (e) {
          if (seen[e.key]) return;          /* append-only: nothing on screen is re-rendered */
          seen[e.key] = 1;
          var wrap = document.createElement('div');
          wrap.innerHTML = e.html;
          while (wrap.firstChild) feed.appendChild(wrap.firstChild);
          window.__rfAppended++;
        });
        if (typeof j.position === 'number') since = Math.max(since, j.position);
        if (typeof j.seq === 'number') seq = Math.max(seq, j.seq);
        /* Entering or leaving steering changes the CHROME, which this poller does not own. A
         * reload is the honest way to pick it up: the band, the input's very existence in the
         * DOM and the take-control panel are all server-rendered decisions, and re-deriving
         * them here would be the second implementation. It never fires while the operator is
         * typing, for the obvious reason. */
        /* Following the agent across tasks. A new run means new copy at the top of the page --
         * the seam, the vitals, the take-control panel's very shape -- and a cursor that means
         * nothing against the new file. The server decides all of that, so the honest move is to
         * ask it again rather than rebuild it here. */
        if (j.run && run && j.run !== run &&
            !holdsFocus(document.getElementById('steerinput'))) {
          location.reload();
          return;
        }
        var on = feed.getAttribute('data-steering') === '1';
        if (!!j.steering !== on && !holdsFocus(document.getElementById('steerinput'))) {
          location.reload();
        }
      })
      .catch(function () { /* a poll that fails is a poll; the next one is in three seconds */ });
  }

  timer = setInterval(poll, POLL_MS);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });

  /* `show` on a fold: the churn is fetched on demand and never shipped with the poll. One fold
   * on a live run held 1,199 events when this was measured. */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('.foldbtn') : null;
    if (!btn) return;
    var out = btn.parentNode.nextElementSibling;
    if (!out || !out.classList.contains('foldout')) {
      out = document.createElement('div');
      out.className = 'foldout';
      btn.parentNode.parentNode.insertBefore(out, btn.parentNode.nextSibling);
    }
    if (!out.hidden) { out.hidden = true; return; }
    out.hidden = false;
    out.textContent = 'reading…';
    fetch('/api/runfeed/' + encodeURIComponent(name) + '/fold?path=' +
          encodeURIComponent(btn.getAttribute('data-fold')) +
          '&from=' + btn.getAttribute('data-from') + '&to=' + btn.getAttribute('data-to'))
      .then(function (r) { return r.json(); })
      .then(function (j) {
        out.innerHTML = '';
        (j.lines || []).forEach(function (l) {
          var d = document.createElement('div');
          d.className = 'fl';
          d.textContent = l.text;         /* textContent: these are engine-written strings */
          out.appendChild(d);
        });
        if (j.dropped) {
          var c = document.createElement('div');
          c.className = 'capped';
          /* NO SILENT CAPS. What was dropped is stated, because a truncation nobody mentions
           * reads as "that was all of it". */
          c.textContent = j.dropped + ' more lines in this fold are not shown (read cap).';
          out.appendChild(c);
        }
        if (!(j.lines || []).length && !j.dropped) out.textContent = 'nothing legible in this fold.';
      })
      .catch(function () { out.textContent = 'the fold could not be read just now.'; });
  });
})();

/* ==========================================================================================
 * V-SHELL-3 (task 0177): THE STACK SURFACE. DESIGN-SYSTEM.md section 5, which adopts V10-A SPEC
 * sections 3-7 verbatim as the normative component spec, as amended.
 *
 * FOUR SENTENCES GOVERN EVERY LINE BELOW.
 *
 * 1. PRESS TO READABLE IS 250ms AND A NAVIGATION IS NOT. The stack advances by navigating -- the
 *    server owns both cursors, so every decision renders a fresh document -- and a round trip on
 *    the press would spend the whole budget before a pixel moved. So the NEXT DOCUMENT IS
 *    PREFETCHED while the operator is still reading the current card, and the press swaps a node
 *    out of a document the client already holds. The prefetch reads `data-next`, which is the
 *    exact URL the fallback navigation uses, so the fast path and the slow path render the same
 *    server truth from the same route. If the prefetch has not landed, the form submits and the
 *    browser navigates: slower, correct, and the only thing lost is the 250ms.
 *
 * 2. THE PIP IS THE THUNK AND IT FIRES ON THE PRESS. Not on the round trip. The press is the
 *    operator's act and the pip is its physical record; a pip that waited for the server would be
 *    reporting the network's opinion of his decision. Everything the press does is optimistic and
 *    everything optimistic has a put-back, which is `fail()` below.
 *
 * 3. NOTHING PREEMPTS THE CARD IN YOUR HANDS (DS 5.2). Urgency buys position behind it, never
 *    focus in front of it. The three-second poll here is a SIBLING of `patch()` above and never a
 *    branch inside it, for the same reason V4's poller is: different keying. `patch()` swaps
 *    whole regions by name; this one refreshes exactly three nodes -- the header counts, the pip
 *    row and the damage line -- and `.stackcard` is not one of them and can never become one.
 *    Repaint safety here is structural, not careful. The never-replace-activeElement guard is
 *    kept anyway, as a second layer, because V3's option rail lives inside the card and a future
 *    surface may put a focusable node inside a patched region.
 *
 * 4. NO KEY IS BOUND TWICE. Escape is already the core IIFE's (console.js:274, it clicks
 *    `#stack-close`), and Ctrl+. is already V-SHELL-2's global door. DS 5.8 lists both for this
 *    surface and both already behave exactly as it says, so binding them again here would give
 *    one keystroke two handlers and a leave-and-enter race. V10-B:208-210's collision rule says
 *    drop `Ctrl+.` on a surface where it collides rather than remap it -- it does not collide
 *    here, it is simply already correct, so this block adds Enter, S, J and U and nothing else.
 * ========================================================================================== */
// ---- V-SHELL-3: the resolve loop, interruption, the ticker and completion (DS 5.1-5.9) ----
(function () {
  'use strict';
  if (document.body.getAttribute('data-room') !== 'stack') return;
  var wrap = document.getElementById('stackwrap');
  if (!wrap) return;

  /* Durations are READ FROM THE STYLESHEET, never restated here, so each one lives in exactly
   * one place: `console.css:91-93` is DS section 2.4's scale. V-SHELL-2 reads the two door
   * budgets the same way and for the same reason. */
  function ms(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    var n = parseFloat(v);
    return (v.slice(-2) === 'ms' && n > 0) ? n : (v.slice(-1) === 's' && n > 0 ? n * 1000 : fallback);
  }
  var T_EXIT = ms('--t-exit', 140);
  var T_SKIP = ms('--t-skip', 120);
  var T_ENTER = ms('--t-enter', 160);
  var T_PIP = ms('--t-pip', 180);
  var T_CONVERGE = ms('--t-converge', 500);
  var T_SETTLE = ms('--t-settle', 150);

  /* THE CONTRACT NUMBER, AND IT IS DERIVED RATHER THAN DECLARED. V10-A 3.1 puts the entry at
   * t=90 and the card fully readable at t=250; the entry itself is `--t-enter`. So the delay is
   * whatever is left of the budget after the entry, and if the motion scale ever moves the entry
   * duration the overlap moves with it instead of silently blowing the budget. The 50ms overlap
   * the spec asks for falls out of the same arithmetic: 90 + 160 = 250, and the exit ends at
   * 140. */
  var READABLE = 250;
  var ENTER_AT = Math.max(0, READABLE - T_ENTER);
  var RECEIPT_AT = T_EXIT;              /* t=140: the line appears below the card, never over it */
  var POLL_MS = 3000;
  var TICKER_MS = 7000;                 /* the receipt-stripe family's 7s (DS 4.2) */
  var ARM_MS = 3000;                    /* DS 5.6's window */
  var TIMEOUT_MS = 8000;                /* V10-A 3.3: a POST that has not answered by here failed */

  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* THE TIMELINE, PUBLISHED. `press to readable <= 250ms` is a contract, and a contract nobody
   * can re-measure is a claim. These marks -- when the outgoing card was told to leave, when the
   * incoming one was prepared, when the entry timer fired, when it was in the document, and when
   * the browser reported its entry animation done -- are what a later lane needs to find out
   * WHERE the budget goes rather than only that it went. They cost one integer each and land on
   * the same object the measurement does, `window.__vs3.marks`. */
  var MARKS = {};

  /* THE LAYOUT LAG, MEASURED AT RUN TIME RATHER THAN GUESSED ONCE.
   *
   * V10-A 3.1 puts the entry BEGINNING at t=90 and the card READABLE at t=250, and 90 + 160 is
   * exactly 250: the spec's arithmetic allocates zero milliseconds to laying out and painting a
   * real card. A browser does not work for free. Measured on this host, a swapped-in stack card
   * is inserted at t=90 and its entry animation actually BEGINS at t=114-124 -- one to two
   * frames of style and layout on a card carrying a title, a meta row, a counterargument, V5's
   * media slot and V3's rail -- so the fade ends at 263-277ms and the contract is missed by the
   * cost of drawing the thing.
   *
   * So the surface asks EARLIER by exactly what it has measured itself to be late by. The entry
   * then begins at t=90, which is the row of the spec's table this compensates, and ends at 250,
   * which is the row that is the contract. Nothing about the 250ms bar moves and no duration
   * token is touched; what moves is when this code asks, and it asks earlier only because it
   * measured itself asking late.
   *
   * Clamped, and the clamp is the point: it can never delay an entry (a negative lag is ignored)
   * and it can never pull one more than the whole 90ms window forward, so a pathological
   * measurement degrades to "swap immediately" rather than to nonsense. It starts at zero, so
   * the FIRST press of a run pays the full lag and every press after it does not -- an estimate
   * this surface has not yet earned is not one it will spend. */
  var LAG = 0, GAPS = [];
  function learn(inserted, entered) {
    if (typeof inserted !== 'number' || typeof entered !== 'number') return;
    var gap = entered - inserted;
    if (gap < 0 || gap > ENTER_AT) return;           /* nonsense, or larger than the window */
    /* THE WORST OF THE LAST FIVE, NOT THE MEAN OF THEM, AND THAT IS THE DIFFERENCE BETWEEN A
     * BUDGET AND AN AVERAGE. Smoothing two samples put the estimate at the MIDDLE of the
     * observed layout cost, so roughly half the presses started their entry late by whatever
     * that press was above the mean, and the run came back 214, 216, 251, 243, 241, 242, 241,
     * 237 -- one press over a contract that says <= 250, measured. A deadline is met against
     * the worst case that is still plausible, so the estimate is the largest of the last five
     * gaps and the window is what stops one pathological frame owning it forever.
     *
     * Being EARLY is not the same kind of error as being late: it can only move the entry
     * towards the press, it is clamped to the whole 90ms window, and the card is then readable
     * before 250ms rather than after it. V10-A 3.1's t=90 is the row this compensates; t=250 is
     * the row that is the contract. */
    GAPS.push(gap);
    if (GAPS.length > 5) GAPS.shift();
    LAG = Math.min(ENTER_AT, Math.max.apply(null, GAPS));
  }

  /* ---------------------------------------------------------------- the run's own memory
   * The server cannot see a run that is over: at the completion screen every item it decided has
   * left the queue, so `unblocked N tasks` and `N agents resume` cannot be summed server side.
   * They are summed HERE, from the cards this client actually watched resolve, which is the only
   * honest source for them. sessionStorage and not a variable, because the fallback path is a
   * real navigation and a variable would not survive it. An unobserved run renders no leverage
   * line at all rather than a zero. */
  var KEY = 'vs3-run';
  function run() {
    try { return JSON.parse(sessionStorage.getItem(KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function save(r) { try { sessionStorage.setItem(KEY, JSON.stringify(r)); } catch (e) {} }
  var r0 = run();
  if (!r0.at) { save({ at: Date.now(), decided: 0, unblocked: 0, waiting: 0, seen: [] }); }

  function seen(id) {
    var r = run(), s = r.seen || [];
    if (s.indexOf(id) >= 0) return true;
    s.push(id); r.seen = s; save(r);
    return false;
  }

  /* THE FIRST PRESS OF A RUN SHOULD NOT BE THE SLOW ONE, and without this it was: `LAG` starts
   * at zero, so the first card paid the full unlearned layout cost and came in at 271ms while
   * every press after it sat at 228-256ms. Measured, task 0177.
   *
   * So the surface calibrates itself once, in idle time, on a clone of the card it is already
   * showing: same subtree, same stylesheet, same entry rule, inserted off-screen where it cannot
   * be seen and cannot reflow anything the operator is reading, timed from insertion to the
   * animation actually starting, then removed. That is the same control experiment 0177 ran by
   * hand to prove the lag was this code's and not the browser's -- run by the code, on the card
   * in front of it, rather than assumed from a number measured on some other host. */
  function calibrate() {
    var card = document.getElementById('stackcard');
    if (!card || reduce) return;
    var probe = card.cloneNode(true);
    probe.removeAttribute('id');
    probe.className = 'stackcard arriving';
    probe.setAttribute('aria-hidden', 'true');
    probe.style.cssText = 'position:fixed;left:-99999px;top:0;width:34rem;pointer-events:none';
    var t = 0;
    probe.addEventListener('animationstart', function () {
      learn(0, Math.round(performance.now() - t));
      probe.remove();
    });
    probe.addEventListener('animationend', function () { probe.remove(); });
    document.body.appendChild(probe);
    t = performance.now();
    setTimeout(function () { if (probe.parentNode) probe.remove(); }, 2000);
  }

  /* ---------------------------------------------------------------- prefetch */
  var docs = {};
  function prefetch(url) {
    if (!url || docs[url]) return;
    docs[url] = null;                       /* in flight: never fetched twice */
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (t) { if (t) docs[url] = new DOMParser().parseFromString(t, 'text/html'); })
      .catch(function () { delete docs[url]; });
  }
  function ready(url) { return url && docs[url] ? docs[url] : null; }

  /* ---------------------------------------------------------------- what this client has
   * already taken off its own screen.
   *
   * THE RACE THIS CLOSES, MEASURED RATHER THAN REASONED. The route excludes the ids named on
   * `?x=`, and it read exactly ONE of them until 2026-08-19: the card the press had just
   * removed. That assumed the write for card A had committed before the client asked for the
   * document that follows B, and nothing enforced it -- `warm()` fires ~250ms after the press
   * and a resolve POST answers in ~1s on this host. So the next document was rendered while the
   * previous write was still in flight, A was still an open row, and A CAME BACK AT THE FRONT.
   * Eight presses, measured on a clean store: q0008, q0010, q0008 again, q0008, q0012, q0014,
   * q0012 again, q0012. Every card decided two or three times; the second write refused and put
   * back, which is loud rather than silent and still the loop replaying a decision.
   *
   * A skip is NOT in this set. It cycles to the back and must come round again; that is the one
   * exit whose item is still in the run.
   *
   * This is also what the poll reads (`watch()` below), because a poller that sees a row the
   * screen has already spent sees a card mismatch and announces `resolved elsewhere` about the
   * operator's own press. Same stale row, same cure, and the address bar keeps the plain URL:
   * the drops ride the FETCH, never `data-self`. */
  var committed = [];
  function withDrops(u) {
    if (!u || !committed.length) return u;
    var has = u.match(/[?&]x=([^&]*)/);
    var ids = (has ? decodeURIComponent(has[1]).split(',') : []).filter(Boolean);
    committed.forEach(function (id) { if (id && ids.indexOf(id) < 0) ids.push(id); });
    var q = 'x=' + encodeURIComponent(ids.join(','));
    return has ? u.replace(/([?&])x=[^&]*/, '$1' + q)
               : u + (u.indexOf('?') < 0 ? '?' : '&') + q;
  }
  function commit(id) { if (id && committed.indexOf(id) < 0) committed.push(id); }
  function uncommit(id) {
    var i = committed.indexOf(id);
    if (i >= 0) committed.splice(i, 1);            /* the write failed: the row is open again */
  }

  function urls() {
    return { next: withDrops(wrap.getAttribute('data-next')),
             skip: withDrops(wrap.getAttribute('data-skip')),
             judge: withDrops(wrap.getAttribute('data-judge')),
             self: wrap.getAttribute('data-self') };
  }
  function warm() {
    /* ONLY `next`, AND THAT IS A LOAD DECISION WITH A MEASUREMENT BEHIND IT.
     *
     * Warming all three exits meant three full renders of `/queue/stack` per card, and every
     * render of that route opens a fresh Postgres connection (`web/model.py` says so at its own
     * `_options_fields`: 13.0ms a connection on this host). Added to the three-second watch
     * poll, an eight-card run asked this store for roughly thirty renders it mostly threw away,
     * and on 2026-08-19 during task 0177's measurement run one of them came back
     * `server closed the connection unexpectedly` and the surface took a 500.
     *
     * `next` is the one exit with a 250ms contract on it. `skip` is a link and navigating is
     * what a link does; `not fast` is a write and pays a write's latency. Both have working slow
     * paths below, so neither is broken by not being prefetched -- they are merely not instant,
     * which is what the spec asks of them anyway. */
    prefetch(urls().next);
  }

  /* ---------------------------------------------------------------- the ticker (DS 5.7)
   * One line, newest only, bottom of the stack body. Every resolution leaves one; 7s; `undo`
   * where a real inverse verb exists and `no undo — <reason>` IN THE VERB'S OWN TERMS where not.
   * Amber and persistent on failure -- a failure is not a receipt, so it does not get the 7s.
   * Never a silent vanish: a line that expires has already been read for seven seconds, which is
   * the opposite of vanishing. */
  var tickTimer = null;
  function tick(opts) {
    var t = document.getElementById('ticker');
    if (!t) return null;
    if (tickTimer) { clearTimeout(tickTimer); tickTimer = null; }
    var line = document.createElement('div');
    line.className = 'tline' + (opts.failed ? ' failed' : '');
    var mark = document.createElement('b');
    mark.textContent = opts.failed ? '⚠' : '✓';
    line.appendChild(mark);
    var span = document.createElement('span');
    span.textContent = opts.text;
    line.appendChild(span);
    if (opts.undo) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = opts.undo.label || 'undo';
      b.addEventListener('click', function () { opts.undo.run(); });
      line.appendChild(b);
    } else if (opts.noUndo) {
      var why = document.createElement('span');
      why.className = 'why';
      /* The em dash is DS 4.2's own spelling of this string and V10-A 6.6's: `no undo —
       * <reason>`. It is a frozen receipt vocabulary, not prose, so it is reproduced exactly. */
      why.textContent = 'no undo — ' + opts.noUndo;
      line.appendChild(why);
    }
    t.textContent = '';
    t.appendChild(line);
    if (!opts.failed) tickTimer = setTimeout(function () { if (t.firstChild === line) t.textContent = ''; },
                                             TICKER_MS);
    return line;
  }

  /* ---------------------------------------------------------------- the swap (DS 5.1)
   * Everything the operator sees at t=90 comes out of `doc`, a document this client already had.
   * The three chrome nodes travel with the card because they are the same server render of the
   * same cursor: patching the card from one document and the pips from another is how a surface
   * starts disagreeing with itself. */
  /* `busy` IS A LATCH, AND A LATCH THAT CANNOT UNLATCH IS WORSE THAN THE RACE IT PREVENTS.
   *
   * It is raised before a swap and lowered when the swap lands. Every early return in `adopt()`
   * -- a document with no wrapper, a body that is not there -- used to leave it raised forever,
   * and a raised latch makes this whole surface inert: `resolve`, `leave` and `watch` all check
   * it first, so `not fast` and the J key silently did nothing while the card sat there looking
   * fine. MEASURED, task 0177: two verification runs in a row where the first section worked and
   * every section after it was dead.
   *
   * Two defences, because one of them is the bug and the other is the class of bug. `release()`
   * at every failure path is the fix. `hold()`'s watchdog is the guarantee: a hold nobody
   * released expires, so the worst a future early return can cost is five seconds of a surface
   * declining a second press, not a mode the operator has to reload out of. */
  var busy = false, holdT = null;
  function hold() {
    busy = true;
    clearTimeout(holdT);
    holdT = setTimeout(function () { busy = false; }, 5000);
  }
  function release() { busy = false; clearTimeout(holdT); holdT = null; }

  /* A SWAP'S DEFERRED WORK CAN BE CANCELLED, AND THE PUT-BACK IS WHAT CANCELS IT.
   *
   * The tail of a swap -- the server's chrome for the new cursor, the history entry, the next
   * prefetch -- runs one frame after the card, off the critical path. A failed commit unwinds
   * the swap in between, and without this counter that tail then landed ON TOP of the put-back:
   * MEASURED, task 0177, the failed card was correctly back in the operator's hands with its
   * amber note, above a pip row reading `done, cur, ...` and a header reading `2 of 7` -- the
   * chrome of a decision that did not happen. Every swap takes a number; `fail()` burns it. */
  var swapSeq = 0;
  function adopt(doc, mode, onSwapped, t0) {
    var card = document.getElementById('stackcard');
    var body = document.querySelector('.stackbody');
    var newWrap = doc.getElementById('stackwrap');
    var newCard = doc.getElementById('stackcard');
    if (!body || !newWrap) return false;

    var hadFocus = card && card.contains(document.activeElement);
    /* THE ENTRY IS OVER THE MOMENT THE FIRST CARD LEAVES, AND UNTIL THIS LINE NOTHING SAID SO.
     *
     * V-SHELL-1 (task 0175) adds `.entering` to `#stackwrap` when the URL carries no `n` and no
     * `d`, which is how it tells an ENTRY into Dispatch apart from an ADVANCE within it, and its
     * rule `.stackwrap.entering .stackcard` staged the first card in with an 80ms delay. That
     * was correct when every advance was a navigation, because the next document simply did not
     * carry the class. THIS LANE STOPPED NAVIGATING. The class now survives on the wrapper for
     * the whole run, and `.stackwrap.entering .stackcard` (two classes and a type) out-specifies
     * this lane's `.stackcard.arriving` (two classes), so EVERY card after the first inherited
     * the first card's 80ms stagger.
     *
     * MEASURED, task 0177: `getComputedStyle` on a swapped-in card returned
     * `animationName: vshell-first-card, animationDelay: 0.08s`, and press-to-readable came back
     * 346-357ms against the 250ms contract -- 90ms of insert plus 80ms of someone else's delay
     * plus the 160ms fade. A control ruled the browser out first: the same fade on a trivial
     * element started 7-14ms after insertion and ran 150ms at a clean 60fps.
     *
     * The fix is a state correction, not a specificity war: the wrapper is no longer entering,
     * because a card is being resolved out of it. V-SHELL-1's CSS and JS are not touched -- its
     * rule is right, and it is now applied to exactly the one card it was written for. */
    wrap.classList.remove('entering');
    if (card) card.classList.add(mode === 'skip' ? 'leaving-skip'
                                : mode === 'judge' ? 'leaving-judge' : 'leaving');

    /* THE CLONE IS PREPARED NOW, WHILE THE OUTGOING CARD IS STILL LEAVING, and it carries its
     * entry class BEFORE it is ever in the document. Both halves of that sentence are budget.
     * Cloning a whole card is real work and doing it inside the 90ms window pushed the entry
     * animation's start from t=90 to t=191, measured; and adding `.arriving` after insertion
     * costs a second style recalculation the browser does not need. */
    /* THE DEADLINE IS MEASURED FROM THE PRESS, NOT FROM HERE, and that one word was 100ms of
     * the budget. Everything above -- the clone, the chrome clones, the FormData, the run's
     * sessionStorage read -- is real work that happens BEFORE the timer can even be scheduled,
     * so a flat `setTimeout(90)` put the entry at t=190 rather than t=90 and the card readable
     * at 357ms against a 250ms contract. Measured, task 0177, on this host. The delay below is
     * whatever is LEFT of the 90ms after the preparation, which is zero when the preparation
     * overran it -- late is then unavoidable, but it is never made later by arithmetic. */
    var seq = ++swapSeq;
    var mk = function (name) { if (t0) MARKS[name] = Math.round(performance.now() - t0); };
    mk('leaving');
    var prepared = newCard ? newCard.cloneNode(true) : null;
    if (prepared) prepared.classList.add('arriving');
    mk('prepared');
    var preparedChrome = ['stackcounts', 'pips', 'stackdmg'].map(function (id) {
      var theirs = doc.getElementById(id);
      return { id: id, node: theirs ? theirs.cloneNode(true) : null };
    });

    setTimeout(function () {
      mk('timer');
      /* THE CARD FIRST. Everything else on this surface is chrome, and chrome that lands one
       * frame later is invisible; a card that lands one frame later is the whole contract. */
      if (card) {
        if (prepared) {
          card.replaceWith(prepared);
        } else {
          /* The stack emptied. The completion screen is the document the server just sent; the
           * sequence that plays over it is theater over a state change that already happened. */
          card.remove();
          var fin = doc.getElementById('stackdone');
          if (fin) body.appendChild(fin.cloneNode(true));
        }
      }
      ['data-next', 'data-skip', 'data-judge', 'data-self', 'data-t0',
       'data-decided', 'data-working', 'data-red'].forEach(function (a) {
        var v = newWrap.getAttribute(a);
        if (v !== null) wrap.setAttribute(a, v);
      });
      var landed = document.getElementById('stackcard');
      mk('inserted');
      /* The frame the browser actually paints the incoming card in. `inserted` is DOM time and
       * costs nothing to hit; this is the first moment anything is on screen, and the gap
       * between the two is the rendering pipeline rather than this code. */
      if (window.requestAnimationFrame) requestAnimationFrame(function () { mk('painted'); });
      if (onSwapped) onSwapped(landed, hadFocus);

      /* EVERYTHING BELOW IS OFF THE CRITICAL PATH and is deferred until the entry has had its
       * first frames. */
      setTimeout(function () {
        if (seq !== swapSeq) return;      /* unwound: a put-back owns the screen now */
        /* THE CHROME LANDS ONE FRAME AFTER THE CARD, AND THAT ORDER IS THE 250ms.
         *
         * Replacing the header counts, the pip row and the damage line in the SAME task as the
         * card put four `replaceWith` calls and the layout they force in front of the entry
         * animation's first style resolution. Measured, task 0177, on this host: the card was
         * inserted at t=90 and painted at t=92, but its entry animation did not START until
         * t=175-188, and press-to-readable came back 345-356ms against a 250ms contract. A
         * CONTROL settled which end that lag was: the same 160ms `--t-enter` fade, same page,
         * same stylesheet, on a trivial element started 7-14ms after insertion and ran 150ms,
         * with a clean 60fps frame trace. So the lag was this code's layout work, not the
         * browser's compositor, and moving three nodes one frame later is the whole fix.
         *
         * The pip that matters is already correct before any of this: it commits on the press,
         * in `resolve()`, on the node that is already on screen. What lands here is the SERVER's
         * pip row for the new cursor, and a row that agrees with itself one frame later is not
         * a thing the eye can catch. */
        preparedChrome.forEach(function (c) {
          var mine = document.getElementById(c.id);
          if (mine && c.node) mine.replaceWith(c.node);
        });
        if (landed) {
          var id = landed.getAttribute('data-id');
          /* DS 5.3 state 5: the `new` marker is dropped after the card is first shown. A card
           * that comes round again after a skip is not news. */
          if (id && seen(id)) {
            var mark = landed.querySelector('.cnew');
            if (mark) mark.remove();
          }
        }
        try { history.replaceState(null, '', wrap.getAttribute('data-self') || location.href); }
        catch (e) {}
        docs = {};
        if (!landed) complete();
        /* THE PREFETCH WAITS FOR THE BUDGET TO CLOSE, AND THIS IS THE LAST 90ms OF IT.
         *
         * `warm()` starts three fetches whose replies are parsed by `DOMParser` on this same
         * thread. Deferred by `--t-enter` it landed at t=250ms -- exactly the frame the entry
         * animation ends on -- and blocked the dispatch of `animationend` behind three document
         * parses. MEASURED, task 0177, on this host: the incoming card was PAINTED at 91ms in
         * every sample, and `readable` still came back 340-347ms. The card was on screen the
         * whole time; the surface was lying about when, because the thing measuring it was stuck
         * behind the prefetch.
         *
         * The next press is seven seconds away in the rhythm this mode is designed around, so
         * there is no cost to starting the prefetch after the current card has settled. Idle
         * time if the browser will tell us about it, a plain timer if it will not. */
        var later = function () { warm(); };
        if (window.requestIdleCallback) requestIdleCallback(later, { timeout: 1200 });
        else setTimeout(later, READABLE);
      }, reduce ? 0 : T_ENTER);
    }, reduce ? 0 : Math.max(0, (mode === 'skip' ? ENTER_AT - (T_EXIT - T_SKIP) : ENTER_AT)
                                  - LAG - (t0 ? performance.now() - t0 : 0)));
    return true;
  }

  /* ---------------------------------------------------------------- the measurement
   * `press to next-card-readable` is a claim, so it is MEASURED and published rather than
   * asserted from a transition duration. `t` starts on the press and stops when the incoming
   * card's entry animation ends -- the frame at which it is at full opacity and no longer
   * moving, which is what "readable" means. The number lands on `#stackwrap[data-readable]` and
   * on `window.__vs3`, so a browser test reads the same number the operator's eyes did. */
  function measure(t0, landed) {
    var mk2 = function (name) { MARKS[name] = Math.round(performance.now() - t0); };
    function stop() {
      var dt = Math.round(performance.now() - t0);
      wrap.setAttribute('data-readable', String(dt));
      MARKS.readable = dt;
      window.__vs3.readable.push(dt);
      window.__vs3.marks.push(JSON.parse(JSON.stringify(MARKS)));
    }
    if (!landed || reduce) { stop(); return; }
    var fired = false;
    landed.addEventListener('animationstart', function (e) {
      if (e.target !== landed || MARKS.entered !== undefined) return;
      mk2('entered');
      learn(MARKS.inserted, MARKS.entered);
    });
    landed.addEventListener('animationend', function (e) {
      if (e.target !== landed) return;         /* a descendant's animation is not this contract */
      if (!fired) { fired = true; stop(); }
    });
    setTimeout(function () { if (!fired) { fired = true; stop(); } }, READABLE + 120);
  }

  /* ---------------------------------------------------------------- resolve (DS 5.1) */
  function resolve(form) {
    if (busy) return true;
    var card = document.getElementById('stackcard');
    var u = urls();
    var doc = ready(u.next);
    if (!card) return false;
    if (!doc) {
      /* THE SLOW PATH, AND IT IS THE OLD PATH ON PURPOSE. The prefetch has not landed, so there
       * is nothing to swap in; post and navigate, which is exactly what the core's `post()` did
       * for this surface before this lane existed. What is NOT acceptable here is falling
       * through to a native form submit: `/queue/act` answers JSON, so the browser would render
       * a page of JSON at the operator. Slower is a cost; a JSON page is a broken surface. */
      hold();
      fetch(form.getAttribute('action'), { method: 'POST', body: new FormData(form),
                                           credentials: 'same-origin' })
        .then(function (res) { return res.json(); })
        .then(function () { location.assign(u.next || location.href); })
        .catch(function () { location.assign(u.next || location.href); });
      return true;
    }
    hold();
    var t0 = performance.now();
    MARKS = { press: 0 };
    var id = card.getAttribute('data-id');
    var verb = form.getAttribute('data-verb') || 'resolved';
    var body = new FormData(form);
    /* THE OUTGOING NODE ITSELF IS THE SNAPSHOT. `replaceWith` detaches it; the variable keeps
     * it alive, intact, with V3's rail and V5's slot exactly as the operator left them. Cloning
     * a whole card to keep a copy of a card that is about to be handed to the garbage collector
     * is pure cost on the one path that has a 250ms contract on it. */
    var snapshot = card;
    /* The pip row AS IT WAS, so the put-back restores it rather than reconstructs it. The
     * reconstruction was wrong in the one case that matters: on the last card of a run the
     * optimistic swap reaches the completion screen, which removes the row, and there is then
     * no `.pip.done` left to un-fill. */
    var pipsBefore = (document.getElementById('pips') || { innerHTML: '' }).innerHTML;
    var countsBefore = (document.getElementById('stackcounts') || { innerHTML: '' }).innerHTML;
    /* THE PRE-PRESS CURSOR, kept so the put-back can return to it. The next document's URL
     * carries `?x=<id>` -- the client telling the server which row it has already committed to
     * on screen -- and a failed commit must not keep asking for a list without an item that is
     * still in it. These four are the whole of that state. */
    var before = { next: u.next, skip: u.skip, judge: u.judge, self: u.self };
    var pipRow = document.getElementById('pips');
    var cur = pipRow ? pipRow.querySelector('.pip.cur') : null;

    /* THE PIP COMMITS ON THE PRESS. This is the whole of rule 2 and it is three lines. */
    if (cur) { cur.className = 'pip done committing'; }
    /* And so does the id: every document this client asks for from here on is a document
     * without this row in it, whether or not the write has landed yet. `uncommit` in `fail()`
     * is the other half -- a refused write leaves the row open and it must come back. */
    commit(id);

    var r = run();
    r.decided = (r.decided || 0) + 1;
    r.unblocked = (r.unblocked || 0) + (parseInt(card.getAttribute('data-unblocks'), 10) || 0);
    if (parseInt(card.getAttribute('data-live'), 10) > 0) r.waiting = (r.waiting || 0) + 1;
    save(r);

    /* The provisional line is the CLIENT's statement of what it just did, shown at t=140 while
     * the server is still answering. It is cancelled rather than shown if the answer beats it:
     * a line that says `committing` appearing AFTER the failure line said `did not commit` is
     * the optimistic UI lying twice in a row, which is worse than not showing it at all.
     * Measured, task 0177: a forced 500 answered in under 140ms and the provisional overwrote
     * the amber failure line. */
    var answered = false;
    setTimeout(function () {
      if (!answered) tick({ text: id + ' ' + verb.toLowerCase() + ' · committing' });
    }, reduce ? 0 : RECEIPT_AT);

    var swapped = adopt(doc, 'resolve', function (landed, hadFocus) {
      /* Focus moves outgoing-primary -> incoming-primary at entry end, and is NEVER stolen from
       * anything else: the swap only takes the keyboard if the swap is where the keyboard was. */
      if (hadFocus && landed) {
        setTimeout(function () {
          var b = landed.querySelector('.stack-primary');
          if (b && !b.disabled) b.focus();
        }, reduce ? 0 : T_ENTER);
      }
      measure(t0, landed);
      release();
    }, t0);
    /* `adopt` refuses a document it cannot read. The write below still goes out -- the operator
     * pressed the button and the press is the decision -- but the latch comes straight back down
     * and the next server render is what puts the screen right. */
    if (!swapped) { release(); location.assign(u.next || location.href); }

    /* The POST runs in the background. It is the LAST thing this function does, deliberately:
     * everything above already happened on the operator's screen. */
    var done = false;
    var timer = setTimeout(function () {
      if (!done) { done = true; answered = true; fail(id, snapshot, before, 'timed out', pipsBefore, countsBefore); }
    }, TIMEOUT_MS);
    fetch(form.getAttribute('action'), { method: 'POST', body: body, credentials: 'same-origin' })
      .then(function (res) { return res.json().then(function (j) { return { ok: res.ok, j: j }; }); })
      .then(function (out) {
        if (done) return;
        done = true; answered = true; clearTimeout(timer);
        if (!out.ok || !out.j || !out.j.ok) { fail(id, snapshot, before, (out.j && out.j.error) || 'refused', pipsBefore, countsBefore); return; }
        /* The server's record replaces the client's provisional statement of it. Same line, same
         * place; what changes is whose sentence it is and whether it carries an inverse verb. */
        tick({ text: out.j.receipt || (id + ' ' + verb.toLowerCase()),
               undo: out.j.undo ? { label: out.j.undo.label || 'undo',
                                    run: function () { undo(out.j.undo, form.getAttribute('action')); } } : null,
               noUndo: out.j.undo ? null : (out.j.no_undo_reason || 'this verb has no inverse') });
      })
      .catch(function (e) {
        if (done) return;
        done = true; answered = true; clearTimeout(timer);
        fail(id, snapshot, before, String(e && e.message || e), pipsBefore, countsBefore);
      });
    return true;
  }

  /* ---------------------------------------------------------------- the put-back (DS 5.1)
   * OPTIMISTIC UIs LIE BY DEFAULT, and this is the line that stops this one lying. A resolution
   * that quietly failed is the silent vanish wearing a new face. The item re-enters at the
   * FRONT, its pip un-fills with the reverse of the commit animation, the amber line persists
   * until the operator does something about it, and NOTHING IS RETRIED SILENTLY. */
  function fail(id, snapshot, before, why, pipsBefore, countsBefore) {
    /* FIRST, BEFORE ANY OF THE PUT-BACK'S OWN WORK: this client is no longer entitled to render
     * documents without this row. It said it had taken the row off its screen; the server said
     * no. Leaving it in the committed set would hide the very item being put back. */
    uncommit(id);
    /* THE PUT-BACK WAITS FOR THE SWAP IT IS UNDOING.
     *
     * The POST can answer before the optimistic swap has run -- a refused write on a local
     * server answers in single-digit milliseconds and the swap is scheduled for t=90 -- and a
     * put-back that ran first was simply overwritten by the swap that followed it. MEASURED,
     * task 0177, with a forced 500: the amber ticker line was correct and persistent, and the
     * card underneath it was the NEXT card with no put-back note on it, which is the optimistic
     * UI lying in the one place it exists not to.
     *
     * So this waits for `busy` to clear rather than racing it. The wait is bounded by the swap
     * it is waiting on -- at most the entry window -- and the amber line is already on screen
     * from the caller's point of view either way. */
    if (busy) {
      /* Said at once, waited on after: silence during the wait would be the vanish this whole
       * path exists to refuse. `tick()` replaces its own line, so saying it twice costs nothing
       * and the second call is what carries the reason into the title. */
      tick({ failed: true, text: id + ' did not commit · put back, nothing lost' });
      setTimeout(function () { fail(id, snapshot, before, why, pipsBefore, countsBefore); }, 20);
      return;
    }
    swapSeq++;                          /* the swap's tail is now stale and must not land */
    /* The cursor goes back FIRST, before anything moves, so that whatever the surface does next
     * -- a reload, the 3s watch, a second press -- reads the list the item is still in. */
    if (before) {
      wrap.setAttribute('data-next', before.next || '');
      wrap.setAttribute('data-skip', before.skip || '');
      wrap.setAttribute('data-judge', before.judge || '');
      wrap.setAttribute('data-self', before.self || '');
      try { history.replaceState(null, '', before.self || location.href); } catch (e) {}
      docs = {};
    }
    var r = run();
    r.decided = Math.max(0, (r.decided || 0) - 1);
    save(r);
    var body = document.querySelector('.stackbody');
    var card = document.getElementById('stackcard');
    /* The optimistic swap may already have reached the completion screen. It is removed before
     * anything else: a run with an item still in it has not completed, and leaving a `Stack
     * clear.` heading above a card that did not commit is the worst sentence this surface could
     * say. */
    var doneScreen = document.getElementById('stackdone');
    if (doneScreen) doneScreen.remove();
    var pipRow = document.getElementById('pips');
    if (!pipRow && typeof pipsBefore === 'string' && pipsBefore && body) {
      /* The completion sequence removes the row, and on the LAST card of a run the optimistic
       * swap has already reached it by the time the failure answers. Rebuilt, not mourned: a
       * put-back with no pip row is a run whose shape the operator can no longer see. */
      pipRow = document.createElement('div');
      pipRow.className = 'pips';
      pipRow.id = 'pips';
      pipRow.setAttribute('aria-hidden', 'true');
      body.appendChild(pipRow);
    }
    var countsNode = document.getElementById('stackcounts');
    if (countsNode && typeof countsBefore === 'string' && countsBefore) {
      countsNode.innerHTML = countsBefore;   /* `2 of 7` was a decision that did not happen */
    }
    if (pipRow && typeof pipsBefore === 'string' && pipsBefore) {
      /* Restored, not reconstructed. `uncommitting` then plays the commit animation in reverse
       * on the pip that had just filled, which is the row arriving back where it was. */
      pipRow.innerHTML = pipsBefore;
      var cur = pipRow.querySelector('.pip.cur');
      if (cur) cur.classList.add('uncommitting');
    } else if (pipRow) {
      var filled = pipRow.querySelectorAll('.pip.done');
      var last = filled.length ? filled[filled.length - 1] : null;
      if (last) last.className = 'pip cur uncommitting';
    }
    if (body && snapshot) {
      if (card) {
        card.classList.add('leaving-skip');
        setTimeout(function () { card.remove(); }, reduce ? 0 : T_SKIP);
      }
      setTimeout(function () {
        var back = snapshot;
        back.classList.remove('leaving', 'leaving-skip', 'leaving-judge');
        var note = back.querySelector('#cputback');
        if (note) { note.hidden = false; note.textContent = 'did not commit, nothing was lost'; }
        back.classList.add('arriving');
        var pips = document.getElementById('pips');
        if (pips) body.insertBefore(back, pips); else body.appendChild(back);
        var b = back.querySelector('.stack-primary');
        if (b && !b.disabled) b.focus();
      }, reduce ? 0 : T_SKIP);
    }
    var line = tick({ failed: true, text: id + ' did not commit · put back, nothing lost',
                      noUndo: null, undo: null });
    if (line && why) line.setAttribute('title', why);
    release();
  }

  /* Undo returns the item as the CURRENT card (DS 5.7). The inverse verb is a real write, so it
   * goes through the same door every other write does and the page then re-reads the server. */
  function undo(spec, action) {
    var f = new FormData();
    f.append('csrf', (document.querySelector('input[name=csrf]') || {}).value || '');
    f.append('action', spec.action);
    f.append('id', spec.id || spec.qid);
    fetch(action, { method: 'POST', body: f, credentials: 'same-origin' })
      .then(function () { location.href = wrap.getAttribute('data-self') || location.href; })
      .catch(function () {});
  }

  /* ---------------------------------------------------------------- skip and not fast */
  function leave(kind) {
    if (busy) return false;
    var u = urls();
    var url = kind === 'skip' ? u.skip : u.judge;
    var doc = ready(url);
    var card = document.getElementById('stackcard');
    if (!card) return false;
    if (!doc) {
      /* THE SLOW PATH FOR THE TWO EXITS THAT ARE NOT DECISIONS. A skip is a plain link and the
       * caller lets the browser follow it. `not fast` is a WRITE, and letting its form submit
       * natively would render `/queue/act`'s JSON at the operator, so it posts and navigates --
       * the same shape the core used for every stack write before this lane existed. */
      if (kind === 'skip') return false;
      var jf = document.querySelector('form.stack-judge');
      if (!jf) return false;
      hold();
      fetch(jf.getAttribute('action'), { method: 'POST', body: new FormData(jf),
                                         credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function () { location.assign(url || location.href); })
        .catch(function () { location.assign(url || location.href); });
      return true;
    }
    hold();
    var id = card.getAttribute('data-id');
    var t0 = performance.now();
    if (kind === 'judge') {
      /* DS 5.5: the pip is REMOVED, not marked failed. The denominator shrinks. */
      var pipRow = document.getElementById('pips');
      var cur = pipRow ? pipRow.querySelector('.pip.cur') : null;
      if (cur) cur.classList.add('dropping');
      var f = document.querySelector('form.stack-judge');
      if (f) {
        /* DS 5.5 removes the item from the RUN, so the same in-flight-write race applies: the
         * demote is a write and the document after it is fetched before that write lands. */
        commit(id);
        fetch(f.getAttribute('action'), { method: 'POST', body: new FormData(f),
                                          credentials: 'same-origin' })
          .then(function (res) { return res.json(); })
          .then(function (j) {
            tick({ text: j && j.receipt ? j.receipt : (id + ' → Judge · not fast'),
                   undo: j && j.undo ? { label: j.undo.label || 'undo',
                                         run: function () { undo(j.undo, f.getAttribute('action')); } } : null,
                   noUndo: (j && j.undo) ? null : ((j && j.no_undo_reason) || 'the miss is the record') });
          })
          .catch(function () { fail(id, card.cloneNode(true), null, 'not fast did not commit', null, null); });
        /* `fail` un-commits, so the demote's failure path needs nothing else here. */
      }
    } else {
      /* A skip is not a write. Nothing is posted, the pip goes hollow at the back, and the done
       * count is unchanged -- skipping inside a stack is not a wake-condition decision. */
      tick({ text: id + ' skipped · back of the stack',
             noUndo: 'it is still in this run; it comes round again' });
    }
    if (!adopt(doc, kind, function (landed) { measure(t0, landed); release(); }, t0)) {
      release();
      location.assign(url || location.href);
    }
    return true;
  }

  /* ---------------------------------------------------------------- DS 5.6 the two-press
   * SEVERABLE, and marked as such at both ends: DS 5.6 says so, and if the operator rules the
   * mock's one-press was a decision this whole function is deleted and nothing else references
   * it. Stack only -- the list keeps one press, because the list has no motor rhythm to ambush.
   * Rhythm cost, stated honestly: one extra press and roughly 400ms, on external items only. */
  var armed = null;
  function arming(form, btn) {
    var card = document.getElementById('stackcard');
    if (!card || card.getAttribute('data-external') !== '1') return false;
    if (armed === btn) { armed = null; btn.classList.remove('armed'); restore(btn); return false; }
    if (armed) { armed.classList.remove('armed'); restore(armed); }
    armed = btn;
    btn.setAttribute('data-rest', btn.innerHTML);
    btn.classList.add('armed');
    btn.innerHTML = (form.getAttribute('data-verb') || 'Approve') +
                    '<span class="noundo"> · no undo, press again</span>';
    setTimeout(function () {
      if (armed === btn) { armed = null; btn.classList.remove('armed'); restore(btn); }
    }, ARM_MS);
    return true;
  }
  function restore(btn) {
    var rest = btn.getAttribute('data-rest');
    if (rest !== null) btn.innerHTML = rest;
  }

  /* ---------------------------------------------------------------- the press */
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || !form.classList) return;
    if (form.classList.contains('stack-act')) {
      var btn = form.querySelector('.stack-primary');
      if (btn && arming(form, btn)) { e.preventDefault(); return; }
      if (armed) { armed.classList.remove('armed'); restore(armed); armed = null; }
      if (resolve(form)) e.preventDefault();
      return;
    }
    if (form.classList.contains('stack-judge')) {
      if (leave('judge')) e.preventDefault();
    }
  });
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a.stack-skip') : null;
    if (a && leave('skip')) e.preventDefault();
  });

  /* ---------------------------------------------------------------- DS 5.8 the keyboard
   * Enter, S, J, U. Escape and Ctrl+. are deliberately absent: see sentence 4 of the banner.
   * Nothing here fires when a text input has focus, and every binding has a visible equivalent
   * on the surface -- a keystroke that can do something no button can do is a feature only the
   * author knows about. */
  function typing() {
    var t = document.activeElement;
    if (!t) return false;
    var tag = (t.tagName || '').toLowerCase();
    return tag === 'textarea' || (tag === 'input' && t.type !== 'radio' && t.type !== 'hidden')
           || t.isContentEditable;
  }
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var card = document.getElementById('stackcard');
    if (!card) return;
    if (e.key === 'Enter') {
      /* DS AMENDMENT (b): Enter is INERT on option-carrying cards until an option is selected AND
       * the primary button itself holds focus. Ordinary cards unchanged. The rail is V3's and is
       * not touched -- this only reads what it already renders. */
      var form = card.querySelector('form.stack-act');
      var btn = form ? form.querySelector('.stack-primary') : null;
      if (!form || !btn || btn.disabled) return;
      if (parseInt(card.getAttribute('data-options'), 10) > 0) {
        var chosen = card.querySelector('input[data-opt]:checked');
        if (!chosen || document.activeElement !== btn) return;
      } else if (typing()) {
        return;
      }
      e.preventDefault();
      if (form.requestSubmit) form.requestSubmit(btn); else form.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }));
      return;
    }
    if (typing()) return;
    var k = e.key.toLowerCase();
    if (k === 's') { e.preventDefault(); var s = card.querySelector('a.stack-skip'); if (s) s.click(); }
    else if (k === 'j') { e.preventDefault(); if (!leave('judge')) {
      var jf = card.querySelector('form.stack-judge'); if (jf) jf.submit(); } }
    else if (k === 'u') {
      var b = document.querySelector('#ticker .tline button');
      if (b) { e.preventDefault(); b.click(); }
    }
  });

  /* ---------------------------------------------------------------- DS 5.2 interruption
   * The sibling of `patch()`. Three nodes, by id, and `.stackcard` is not one of them. */
  function watch() {
    if (busy) return;
    var self = wrap.getAttribute('data-self');
    if (!self) return;
    /* WHAT THE READ WAS A READ OF. A poll started before a press and answered after the swap is
     * a stale read, and acting on one made this poller announce `q0014 resolved elsewhere` about
     * a card the operator had just resolved himself, then swap a stale document in over the live
     * one. Measured, task 0177: seven presses in a row stopped advancing because of it. The
     * `busy` flag alone cannot catch this -- the fetch was already in the air when `busy` went
     * up -- so the response is discarded unless BOTH the cursor and the card are still what they
     * were when the request left. */
    var atSelf = self;
    var atCard = (document.getElementById('stackcard') || {}).getAttribute
                 ? document.getElementById('stackcard').getAttribute('data-id') : null;
    fetch(withDrops(self), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (t) {
        if (!t || busy) return;
        if (wrap.getAttribute('data-self') !== atSelf) return;          /* the cursor moved */
        var now = (document.getElementById('stackcard') || {}).getAttribute
                  ? document.getElementById('stackcard').getAttribute('data-id') : null;
        if (now !== atCard) return;                                     /* the card moved */
        var doc = new DOMParser().parseFromString(t, 'text/html');
        var card = document.getElementById('stackcard');
        var theirCard = doc.getElementById('stackcard');
        if (card && theirCard && card.getAttribute('data-id') !== theirCard.getAttribute('data-id')) {
          /* V10-A 3.4: the item in hand was resolved on another surface. It leaves with the
           * neutral skip motion, its pip fills GREY and not green -- it is not your commit --
           * and the next card enters. No modal, no error. */
          var pipRow = document.getElementById('pips');
          var cur = pipRow ? pipRow.querySelector('.pip.cur') : null;
          if (cur) cur.className = 'pip elsewhere';
          tick({ text: card.getAttribute('data-id') + ' resolved elsewhere · skipped ahead',
                 noUndo: 'it was not your press; the surface that made it owns the undo' });
          hold();
          if (!adopt(doc, 'skip', function () { release(); }, performance.now())) release();
          return;
        }
        ['stackcounts', 'pips', 'stackdmg'].forEach(function (id) {
          var mine = document.getElementById(id), theirs = doc.getElementById(id);
          /* The second layer of rule 3, kept even though the first makes it unreachable today. */
          if (!mine || !theirs || mine.contains(document.activeElement)) return;
          if (mine.innerHTML !== theirs.innerHTML) mine.replaceWith(theirs.cloneNode(true));
        });
        ['data-working', 'data-red'].forEach(function (a) {
          var v = doc.getElementById('stackwrap');
          if (v && v.getAttribute(a) !== null) wrap.setAttribute(a, v.getAttribute(a));
        });
        stamp();
      })
      .catch(function () {});
  }

  /* ---------------------------------------------------------------- DS 5.9 completion */
  function complete() {
    var screen = document.getElementById('stackdone');
    if (!screen) return;
    var r = run();
    var el = document.getElementById('doneelapsed');
    if (el && r.at) {
      var secs = Math.max(0, Math.round((Date.now() - r.at) / 1000));
      el.textContent = ' · ' + (secs >= 60 ? Math.floor(secs / 60) + 'm ' + (secs % 60) + 's'
                                           : secs + 's');
    }
    var facts = document.getElementById('donefacts');
    if (facts && r.decided) {
      if (r.unblocked) {
        var a = document.createElement('div');
        a.className = 'lead';
        a.textContent = 'Your answers unblocked ' + r.unblocked + ' task' +
                        (r.unblocked === 1 ? '.' : 's.');
        facts.appendChild(a);
      }
      if (r.waiting) {
        var b = document.createElement('div');
        b.id = 'doneresume';
        /* PRESENT TENSE AND UNSTAMPED until the poll observes the agents' state change. The
         * stamp keys off observed agent state, never off the resolve succeeding, and if the
         * observation never comes this line simply stays as it is. That is the honest ending. */
        b.textContent = r.waiting + ' agent' + (r.waiting === 1 ? '' : 's') + ' resuming';
        b.setAttribute('data-baseline', wrap.getAttribute('data-working') || '0');
        facts.appendChild(b);
      }
    }
    var pipRow = document.getElementById('pips');
    if (pipRow && !reduce) {
      /* Final pip commits -> 300ms hold -> converge 500ms -> the dot pops to a check. The check
       * is already in the markup; this only holds it back until the pips have finished arriving
       * at it, which is why reduced motion needs no branch here beyond skipping the hold. */
      var check = document.getElementById('donecheck');
      if (check) check.style.visibility = 'hidden';
      setTimeout(function () {
        pipRow.classList.add('converging');
        setTimeout(function () {
          pipRow.remove();
          if (check) { check.style.visibility = ''; }
        }, T_CONVERGE);
      }, 300);
    } else if (pipRow) {
      pipRow.remove();
    }
    var arrived = document.getElementById('donearrived');
    var counts = document.getElementById('stackcounts');
    if (arrived && counts && /\+(\d+) arrived/.test(counts.textContent || '')) {
      var n = RegExp.$1;
      var link = document.createElement('a');
      link.href = wrap.getAttribute('data-self') || location.href;
      link.textContent = n + ' arrived while you ran · run them ▸';
      arrived.appendChild(link);
    }
    try { sessionStorage.removeItem(KEY); } catch (e) {}
    setInterval(stampPoll, POLL_MS);
  }

  /* The stamp, and the reason it is a separate poll: it keys off a state change in the FLEET,
   * observed after the fact, not off this client's own write returning 200. */
  function stampPoll() {
    var line = document.getElementById('doneresume');
    if (!line || line.querySelector('.donestamp')) return;
    fetch(wrap.getAttribute('data-self') || location.href, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (t) {
        if (!t) return;
        var doc = new DOMParser().parseFromString(t, 'text/html');
        var w = doc.getElementById('stackwrap');
        if (w) wrap.setAttribute('data-working', w.getAttribute('data-working') || '0');
        stamp();
      })
      .catch(function () {});
  }
  function stamp() {
    var line = document.getElementById('doneresume');
    if (!line || line.querySelector('.donestamp')) return;
    var base = parseInt(line.getAttribute('data-baseline'), 10) || 0;
    var now = parseInt(wrap.getAttribute('data-working'), 10) || 0;
    if (now <= base) return;                       /* not observed yet: the line stays as it is */
    line.textContent = line.textContent.replace('resuming', 'resume now');
    var s = document.createElement('span');
    s.className = 'donestamp';
    s.textContent = 'CONFIRMED';
    line.appendChild(s);
  }

  /* `ready()` is published for the verification harness and for anyone measuring this surface
   * later: the 250ms is a claim about the FAST path, and a measurement taken while the prefetch
   * is still in flight is a measurement of the fallback wearing the fast path's name. */
  window.__vs3 = { readable: [], marks: [], resolve: resolve, tick: tick, fail: fail, warm: warm,
                   lag: function () { return LAG; },
                   ready: function () { return !!ready(urls().next); } };
  warm();
  if (window.requestIdleCallback) requestIdleCallback(calibrate, { timeout: 2000 });
  else setTimeout(calibrate, 400);
  setInterval(watch, POLL_MS);
  if (!document.getElementById('stackcard')) complete();
})();

/* ==========================================================================================
 * ROW 0431 -- THE OPEN ROW SURVIVES THE POLL, AND THE FIRST CLICK DOES NOT MOVE THE PAGE
 * ==========================================================================================
 *
 * A SIBLING OF `patch()` AND NEVER A BRANCH INSIDE IT, which is this file's own rule: the core
 * IIFE at the top is V-SHELL's and no lane edits `patch()` or `wire()`. This module observes the
 * list host instead, so it needs nothing from either.
 *
 * WHAT IT IS FOR, AND IT IS A MEASUREMENT AND NOT A PREFERENCE. The two levels of expansion the
 * operator asked for are built on a URL fragment, because a fragment is not in the DOM and so
 * survives `host.innerHTML = html`. The FRAGMENT survives it. The `:target` STYLE does not:
 * measured on chromium 1223 on 2026-08-28 and again on 2026-08-29, with `location.hash` unchanged
 * at `#pan-q0006`, emptying `[data-region="list"]` and assigning the identical markup back leaves
 * the inline expansion and the panel both at `display:none`. The browser resolves the target
 * element once and does not re-resolve it when an element carrying that id is re-inserted.
 *
 * So on a board where anything at all is moving -- an `agent waiting 12m` that ticks to 13, a row
 * that arrives, a gate that clears -- the row the operator had open closed under him at a moment
 * he did not choose and for a reason he could not see. It was recorded as a KNOWN LIMIT on
 * 2026-08-28 because the lane that found it did not own this file. This lane does.
 *
 * TWO THINGS, AND THE SECOND IS THE ONE HE WOULD HAVE FELT FIRST.
 *
 * 1. THE OPEN ROW IS RE-APPLIED AS A CLASS AFTER EVERY REPAINT. `is-open` and `is-panel` mirror
 *    the `:target` rules exactly, in the same stylesheet, so JS on and JS off render the same
 *    surface: the class layer is a re-resolvable `:target` and nothing more. The hash stays the
 *    single source of truth, which is why there is no state held in here to go stale -- read the
 *    hash, paint the classes, and a repaint is just another reason to do it again.
 *
 * 2. THE FIRST CLICK NO LONGER JUMPS THE PAGE. A fragment navigation scrolls its target into
 *    view, and measured at 1440x760 on `/queue?tier=shape`, clicking the LAST row moved the page
 *    122px and moved the row 122px up the screen under the cursor that had just clicked it. That
 *    is the 0409 shape again -- something happened, off where he was looking -- so the click is
 *    taken here, the hash is written with `replaceState`, which does not scroll, and the classes
 *    are painted directly. Nothing moves but the row opening.
 *
 *    `replaceState` AND NOT `pushState`, DELIBERATELY. `pushState` would make Back close a row,
 *    which is worth having, but it also puts one history entry on the stack per row opened, so
 *    leaving the queue after reading nine rows costs nine Back presses. Escape and Space are row
 *    0432's, and that is where a close key belongs.
 *
 * WITHOUT JAVASCRIPT NOTHING HERE IS LOAD-BEARING. The anchors are real anchors with real
 * fragments, `:target` still opens them, and the only thing lost is the two improvements above.
 */
(function () {
  var host = document.querySelector('[data-region="list"]');
  if (!host) return;                       /* not the queue: /scope and /stack render no list */

  /* THE HASH IS THE STATE. `#card-<id>` is level one, `#pan-<id>` is level two and implies level
   * one underneath it, and anything else -- including the `#c-<id>` collapse anchor -- is closed.
   * Reading it rather than remembering it is what makes a repaint a no-op instead of an event. */
  function apply() {
    var h = (location.hash || '').replace(/^#/, '');
    var rowId = null, panel = false;
    if (h.indexOf('card-') === 0) { rowId = h; }
    else if (h.indexOf('pan-') === 0) { rowId = 'card-' + h.slice(4); panel = true; }
    var rows = host.querySelectorAll('.qrow');
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], open = (r.id === rowId);
      r.classList.toggle('is-open', open);
      r.classList.toggle('is-panel', open && panel);
    }
  }

  /* The click, taken before the browser scrolls. Only fragments: a server-expanded row closes
   * with a real round trip to `/queue?tier=<t>` and that link is left exactly as it is. */
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a') : null;
    if (!a || !host.contains(a)) return;
    var mine = false, want = ['qopen', 'qshut', 'qmore', 'qpclose', 'qcollapse'];
    for (var k = 0; k < want.length; k++) { if (a.classList.contains(want[k])) mine = true; }
    if (!mine) return;
    var href = a.getAttribute('href') || '';
    if (href.charAt(0) !== '#') return;                    /* the round-trip close, left alone */
    e.preventDefault();
    if (window.history && history.replaceState) {
      history.replaceState(null, '', location.pathname + location.search + href);
    } else {
      location.hash = href.slice(1);                       /* the jump, but never a dead click */
    }
    apply();
  });

  /* ----------------------------------------------------------------------------------------
   * AND THE REASON BOX THE OPERATOR HAD OPEN INSIDE THAT ROW, WITH WHAT HE HAD TYPED IN IT.
   *
   * The row surviving the repaint is only half of it. `Defer`, `Bump`, `not fast`, `Answer`,
   * `Send back` and `Mark my task done` each open a panel by flipping a `hidden` attribute, and
   * that attribute lives in the DOM, so `host.innerHTML = html` puts every one of them back the
   * way the SERVER renders it, which is closed. A `<textarea>`'s typed value is worse than that:
   * it is a property and not an attribute, so it is not in the markup at all and re-parsing the
   * region empties it with nothing to put back.
   *
   * Measured on chromium 1223, 2026-08-29, with the region assigned the REAL `/api/patch` payload
   * rather than its own serialised innerHTML -- which matters, because reading innerHTML back out
   * after the toggle captures the OPEN state and makes this look fixed when it is not:
   *
   *   open before true, typed 54 chars   ->   open after FALSE, typed after ''
   *
   * There is one guard already and it is not this one. `focusedRegion()` skips the region holding
   * the caret, so a repaint cannot land while he is typing. The window this closes is the one
   * after he stops: he opens Defer, reads the wake conditions, looks away, and three seconds
   * later the panel is closed and the sentence he wrote is gone with no receipt and no message.
   *
   * WHAT IS REMEMBERED IS THE MINIMUM: the id of each open panel and the value of its textarea,
   * both dropped the moment the element leaves the DOM, so a resolved card takes its own record
   * with it and nothing here can resurrect a panel for a row that is gone. */
  var openPanels = Object.create(null);              /* panel id -> the text typed into it */

  function remember(id) {
    var t = document.getElementById(id);
    if (!t) { delete openPanels[id]; return; }
    if (t.hidden) { delete openPanels[id]; return; }
    var ta = t.querySelector('textarea');
    openPanels[id] = ta ? ta.value : '';
  }

  function restore() {
    Object.keys(openPanels).forEach(function (id) {
      var t = document.getElementById(id);
      if (!t || !host.contains(t)) { delete openPanels[id]; return; }
      t.hidden = false;
      var ta = t.querySelector('textarea');
      if (ta && openPanels[id] && ta.value !== openPanels[id]) {
        ta.value = openPanels[id];
        /* The submit button is gated on the reason's length by a listener on `input`, so a value
         * assigned without this event comes back with the button still disabled: the text would
         * be there and unsendable, which is worse than losing it. */
        ta.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-toggle]') : null;
    if (!b || !host.contains(b)) return;
    /* AFTER the element's own handler, because this listener is on `document` and bubbles last,
     * so `hidden` has already been flipped by `wire()` and this reads the result rather than
     * predicting it. */
    remember(b.getAttribute('data-toggle'));
  });

  document.addEventListener('input', function (e) {
    var t = e.target.closest ? e.target.closest('[id]') : null;
    while (t && !(t.id in openPanels)) { t = t.parentElement ? t.parentElement.closest('[id]') : null; }
    if (t && host.contains(t)) remember(t.id);
  });

  /* AND IT FORGETS THE MOMENT THE REASON IS SENT, WHICH IS THE HALF THAT COULD HAVE DONE HARM.
   *
   * `Bump` does not resolve its item: the card stays on the board. `post()` submits by fetch,
   * resets the form so the length gate returns to disabled-because-empty, and leaves the panel
   * open with an empty field; the next repaint then closes it because that is how the server
   * renders it. Without this listener the restore above would have re-opened that panel and
   * REFILLED it with the sentence he had already sent, which reads as a bump that did not go
   * through and invites a second one. A bump is a decaying additive term, so two are not one.
   *
   * `form.reset()` fires `reset` and not `input`, so the memory cannot learn this from the field.
   * The submit is the signal. It bubbles even though `post()` calls `preventDefault()`, and this
   * listener is on `document`, so it runs after the handler that sent it. */
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f || !host.contains(f)) return;
    Object.keys(openPanels).forEach(function (id) {
      var t = document.getElementById(id);
      if (t && t.contains(f)) delete openPanels[id];
    });
  }, true);

  function paint() { apply(); restore(); }

  paint();
  window.addEventListener('hashchange', apply);
  /* `childList` only, and not `subtree` or `attributes`: `patch()` swaps this host's children in
   * one assignment, and `apply()` writes only classes, so this observer cannot observe itself.
   * `restore()` does write a `hidden` attribute and a textarea value, which is exactly why the
   * observer must not watch attributes or character data either. */
  if (window.MutationObserver) new MutationObserver(paint).observe(host, { childList: true });
})();

/* ==========================================================================================
 * ROW 0432 -- THE KEYBOARD, FOR POWER USERS AND NOT FOR IPAD BABIES
 * ==========================================================================================
 *
 * His ask 3, in his words: *"our users really don't want a shiny interface ... our people are
 * going to be power users, not iPad babies."* Space to open and close, arrows between items and
 * between options, number keys to pick an option, Ctrl+Z to reverse anything, V for a voice note.
 *
 * A SIBLING MODULE, exactly as the 0431 block above is, and it edits neither that block nor the
 * core IIFE at the top of this file. It shares ONE thing with 0431 and that is the thing 0431
 * left it: `location.hash` is the whole of the open state, so the keyboard writes the hash and
 * both modules paint from it. Three forms, and the third is this row's:
 *
 *     #row-<id>    the cursor is on this row and the row is CLOSED
 *     #card-<id>   the cursor is on this row and level one is open   (0431)
 *     #pan-<id>    the cursor is on this row and level two is open   (0431)
 *
 * `#row-` matches neither of 0431's prefixes, so that module reads it as closed, which is what
 * it is. Nothing there had to change.
 *
 * WHY `replaceState` PLUS A SYNTHETIC `hashchange` AND NOT `location.hash =`. Assigning the hash
 * makes the browser scroll its target into view, which is the 122px page jump 0431 measured and
 * removed; a keyboard cursor that threw the page around on every arrow would be that defect back
 * at ten times the rate. `replaceState` does not scroll and does not fire `hashchange`, so the
 * event is dispatched here: 0431 listens for it, and one dispatch keeps the two modules painting
 * from one write. The cursor is then scrolled into view with `block:'nearest'`, which moves the
 * page by the minimum needed and by nothing at all when the row is already on screen.
 *
 * GAMIFICATION IS RULED AND THE RULING IS BUILT IN HERE. MUST-NOT-BUILD item 8 is KEPT with his
 * approval to overrule it banked unspent, because what he asked for -- *"a fun game as you're
 * going through your queues"* -- is settled by his own qualifier, *"optimized for power users
 * (speed and clarity over shiny interface)"*. So the fun is FLOW: a cursor that moves without a
 * round trip, an expansion that is already in the markup, and no navigation. There is no points
 * total here, no streak, no completion percentage, no leaderboard, and NO NUMBER ANYWHERE IN
 * THIS MODULE THAT GOES UP WHEN THE QUEUE GOES DOWN. The legend under `?` counts nothing.
 *
 * NOTHING HERE IS LOAD-BEARING WITHOUT JAVASCRIPT, and nothing here is the only route to
 * anything: every key below drives a control that is already on the screen and already clickable.
 */
(function () {
  var host = document.querySelector('[data-region="list"]');
  if (!host) return;                    /* not the queue: /scope, /stack and /terminal have none */

  /* ROW 0433. Deep work is a SERVER state -- `io_deep` is a cookie `web/app.py` reads, because
   * the fast pane is rendered server side and a client-only deep work would still send the
   * interrupting HTML down the wire. So this reads the class the server put on `<body>` and
   * never a value of its own: two copies of a mode is how the two disagree. */
  var DEEP = !!(document.body && document.body.classList.contains('deep'));

  /* ------------------------------------------------------------------ saying things out loud */

  /* The say bar is the core's, and so is the geometry that keeps it in the viewport. Row 0409
   * was a receipt that existed, landed on time, and landed at -395px on a document he had
   * scrolled, so `placeTransients()` pins both transient hosts under the sticky header. That
   * function is private to the core IIFE and its published hook is the `resize` listener the
   * core registers on `window`. So this writes the banner the core's own shape and then asks the
   * core to place it. A second copy of the geometry here is the thing that would rot. */
  function say(msg, kind, clearAfter) {
    var bar = document.getElementById('saybar');
    if (!bar) return;
    bar.innerHTML = '';
    var b = document.createElement('div');
    b.className = 'banner ' + (kind || '');
    b.textContent = msg;                            /* never innerHTML: item titles are agent text */
    bar.appendChild(b);
    try { window.dispatchEvent(new Event('resize')); } catch (e) { /* older engines: it is placed
      on the next real resize, and the banner is readable either way */ }
    if (clearAfter) {
      setTimeout(function () {
        if (bar.firstChild === b) { bar.innerHTML = ''; }
        try { window.dispatchEvent(new Event('resize')); } catch (e2) {}
      }, clearAfter);
    }
  }

  /* ------------------------------------------------------------------ the hash, read and written */

  /* ROW 0434 ADDED THE `display` TEST AND NOTHING ELSE IN THIS MODULE CHANGED. The filter bar
   * hides a row that does not match by writing an inline `display:none` on it, and a cursor that
   * could still land there would arrow onto a row with no box, `space` would open something
   * invisible, and the row under the mark would not be the row on the screen. That is the
   * affordance-that-cannot-work failure with its ends swapped, so the queue's keys walk the rows
   * a person can actually see. With no filter on, no row carries an inline display and this reads
   * exactly as `querySelectorAll('.qrow')` did. */
  function rows() {
    return Array.prototype.slice.call(host.querySelectorAll('.qrow'))
      .filter(function (r) { return r.style.display !== 'none'; });
  }

  /** {row: <the .qrow or null>, id: '<item id>', level: 0 closed | 1 open | 2 panel} */
  function cursor() {
    var h = (location.hash || '').replace(/^#/, ''), id = null, level = 0;
    if (h.indexOf('pan-') === 0) { id = h.slice(4); level = 2; }
    else if (h.indexOf('card-') === 0) { id = h.slice(5); level = 1; }
    else if (h.indexOf('row-') === 0) { id = h.slice(4); level = 0; }
    var row = id ? document.getElementById('card-' + id) : null;
    if (!row || !host.contains(row)) { return { row: null, id: null, level: 0 }; }
    return { row: row, id: id, level: level };
  }

  var LEVEL_PREFIX = ['row-', 'card-', 'pan-'];

  function go(id, level, scroll) {
    var frag = '#' + LEVEL_PREFIX[level] + id;
    if (window.history && history.replaceState) {
      history.replaceState(null, '', location.pathname + location.search + frag);
    } else {
      location.hash = frag.slice(1);
    }
    /* 0431 paints the expansion from `hashchange`; this module paints the cursor from the same
     * event, through the same listener everything else here uses. One write, two painters. */
    var ev;
    try { ev = new HashChangeEvent('hashchange'); }
    catch (e) { ev = new Event('hashchange'); }
    window.dispatchEvent(ev);
    if (scroll !== false) {
      var row = document.getElementById('card-' + id);
      /* `block:'nearest'` and never `'center'` or `'start'`: it scrolls by the minimum and by
       * nothing at all when the row is already on the screen, so arrowing through rows that are
       * already visible does not move the page under him. */
      if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  }

  /* ROW 0434: THIS PAINTS EVERY ROW AND NOT ONLY THE ONES THE ARROWS WALK, and the difference
   * became real the moment `rows()` above learned to skip a filtered-out row. Painting over
   * `rows()` clears the mark from the rows it can still see and leaves it on the row it can no
   * longer see, so a filter that hid the cursor left TWO elements carrying `is-cursor` and
   * `document.querySelector('.qrow.is-cursor')` answered with the hidden one. Watched happening
   * here on 2026-08-29 before this line: cursor `card-q0013`, hidden, with `card-q0023` the only
   * row on screen. A painter must cover everything it could have painted. */
  function paintCursor() {
    var c = cursor();
    var rs = Array.prototype.slice.call(host.querySelectorAll('.qrow'));
    for (var i = 0; i < rs.length; i++) {
      rs[i].classList.toggle('is-cursor', rs[i] === c.row);
    }
  }

  /* ------------------------------------------------------------------ the options rail */

  function railOf(row) {
    return row ? row.querySelector('.rail') : null;
  }

  function optionsOf(row) {
    var rail = railOf(row);
    if (!rail) return [];
    return Array.prototype.slice.call(rail.querySelectorAll('input[type=radio][data-opt]'));
  }

  function pick(row, n) {
    var opts = optionsOf(row);
    if (!opts.length) return null;
    for (var i = 0; i < opts.length; i++) {
      if (String(opts[i].value) === String(n)) {
        opts[i].checked = true;
        /* FOCUS THE RADIO, AND IT IS NOT DECORATION. A mouse click focuses it, and the core's
         * `focusedRegion()` guard skips repainting the region holding the focused input -- which
         * is the ONLY thing stopping the three-second poll from wiping a selection, because the
         * patcher restores the served markup in which no radio is checked and nothing is
         * re-applied afterwards on purpose. Measured while writing this: without the focus, a
         * poll landing between two keystrokes put the rail back to nothing checked and the next
         * arrow started from option 1 instead of the one he had picked. `preventScroll` because
         * the row is already on screen; the cursor put it there. */
        try { opts[i].focus({ preventScroll: true }); } catch (e) { opts[i].focus(); }
        /* The rail's own `change` listener is what reveals the option's plan, its
         * counterargument and its primary button. Dispatching the real event rather than calling
         * `swap()` directly means the keyboard and the mouse take the identical path. */
        opts[i].dispatchEvent(new Event('change', { bubbles: true }));
        return opts[i];
      }
    }
    return null;
  }

  /* ------------------------------------------------------------------ Ctrl+Z */

  /* MUST-NOT-BUILD ITEM 10 IS KEPT AND THIS KEY IS WHY IT WAS KEPT RATHER THAN OVERRULED.
   *
   * He asked for *"Ctrl+Z to reverse anything"* and he approved overruling item 10 to get it.
   * The approval is banked and unspent, because item 10 does not forbid undo -- it forbids a LIE
   * about undo. Authorising the keystroke on acts that have no inverse would make it silently do
   * nothing, which is the N4 defect it was raised against, restated as a shortcut.
   *
   * So this key does exactly one thing: it presses the undo control that is ALREADY on the
   * receipt stripe. Where a real inverse verb exists the stripe carries it (`undo` on your own
   * `done` runs `reopen`, an `answer` offers `amend`, `Accept work` offers `Unaccept`), and
   * where none exists the stripe already says `no undo, <reason>` in that verb's own terms. This
   * reads the same DOM a person reads and takes the same path a click takes, so it cannot
   * disagree with what is on his screen, and if the store refuses the write he sees the store's
   * own refusal rather than this module's guess at one.
   *
   * IT IS NEVER SILENT. Three outcomes, and all three say something:
   *   an inverse exists      the control is pressed and the core states the receipt
   *   the act has no inverse the stripe's own reason is read back, naming the act
   *   nothing to undo        it says there is nothing of his on this screen to reverse
   *
   * AND IT DOES NOT ASSUME THE INVERSE EXISTS JUST BECAUSE THE CODE DOES. `unaccept` is
   * committed in `transitions.py` and migration 43 is NOT APPLIED to the live store: on 2026-08-29
   * live `brain` was at ledger 42 with 18 kinds in `thread_kind_check` and no `unaccept` among
   * them (filed as row 0471). On that store the write returns HTTP 500 with a raw Postgres
   * banner. The console renders the stripe from the ROOM ALLOWLIST rather than from the store's
   * schema, so the control is there and pressing it fails. That failure now arrives through
   * `post()`, which states `res.j.error` in the say bar, exactly as it would for a click; this
   * key adds no second, quieter path for it to fail down. */
  function undo() {
    var bar = document.querySelector('[data-region="receipts"]');
    var stripes = bar ? Array.prototype.slice.call(bar.querySelectorAll('.receipt')) : [];
    if (!stripes.length) {
      say('Nothing to undo: no act of yours is on this screen. Ctrl+Z reverses the last thing '
          + 'you did here, and the receipt stripe is where it lives.', 'refusal');
      return;
    }
    var last = stripes[0];
    var what = (last.querySelector('span') || {}).textContent || 'that act';
    var btn = last.querySelector('form.act-form button[type=submit]');
    if (btn) {
      say('Undoing: ' + what.trim(), '', 9000);
      btn.click();
      return;
    }
    var why = last.querySelector('.why');
    var reason = why ? why.textContent.replace(/^\s*no undo,?\s*/i, '').trim() : '';
    say(what.trim() + ' cannot be undone'
        + (reason ? ': ' + reason : '. The verb that did it has no inverse, so there is nothing '
           + 'for Ctrl+Z to run.'), 'refusal');
  }

  /* ------------------------------------------------------------------ V, and what is missing */

  /* HIS ASK IS *"V to record a voice note that is transcribed and posted back as a note"*, AND
   * SINCE 2026-08-31 THIS KEY DOES IT. What it used to do was say, in one sentence, that it did
   * not, and name the two things that were missing. Both are closed and the sentence is kept
   * below as the record of what they were, because the second one was a prohibition he had to
   * rule on and the ruling is the reason this works.
   *
   * WHAT IS REAL ON THIS HOST, MEASURED 2026-08-29: `voice/bin/voice` records, retains the
   * audio, and transcribes it -- `voice engines` reports whisper.cpp available and
   * `names-reliable`, windows-sapi available and `shape-only`. That path lands the result through
   * the `intake` transition as an OBJECTIVE in inbox, with a media pointer, a sha256 and one of
   * the four honest transcription statuses.
   *
   * WHAT WAS MISSING, AND HOW EACH HALF WAS CLOSED:
   *   1. THE CAPTURE. There is still NO BROWSER CAPTURE and there must not be: `getUserMedia`
   *      plus somewhere to POST the bytes is a second write door, and `test_allowlist.py` asserts
   *      `/<room>/act` is the only one. So the capture stays host side. The server runs the
   *      `voice` CLI as a subprocess, which records, retains the audio, re-hashes it and
   *      transcribes it, writing its own capture rows AS ITSELF. None of those five verbs is
   *      reachable from any room, which is what stops a rendering surface being able to open a
   *      microphone.
   *   2. THE LANDING. He said "posted back as a NOTE", and `note` was deliberately absent from
   *      the Queue's verb set under MUST-NOT-BUILD item 5. THAT ITEM WAS PUT TO HIM AND HE
   *      OVERRULED IT, on 2026-08-30: *"i want to override and say voice is needed to make it
   *      easy for users to get through inbox"*. The overrule is one line on the allowlist and it
   *      carries a condition that pays item 5's incident rather than waiving it: no box, no typed
   *      note, and the only body this path can post is a transcript. `web/MUST-NOT-BUILD.md`
   *      item 5 has the full statement and the three checks.
   *
   * THIS KEY PRESSES THE CARD'S OWN CONTROL and does not post anything itself. One path, one set
   * of guards. A key with its own POST is a second way to do one thing, and the two drift the
   * first time one of them grows a guard. */
  function voice() {
    var c = cursor();
    /* IT NEEDS A ROW AND IT SAYS SO. `V` is answered above the empty-tier guard because the old
     * refusal was true of an empty tier too; the working version is not, so it does the guard's
     * job itself rather than being moved back below it and going silent on the one page he opens
     * first. */
    if (!c.row) {
      var rs = rows();
      if (!rs.length) { nothingHere(); return; }
      say('V records a voice note ON a row. Move to one with the arrows, or press ? for the keys.',
          'refusal');
      return;
    }
    var form = c.row.querySelector('form.qvoice');
    if (!form) {
      /* A ROW WITHOUT THE CONTROL IS A FACT ABOUT THE ROW, NOT A REASON TO INVENT A POST. The
       * stack, the phone and `/task/<id>` render their own controls, and a key that reached past
       * a missing one would be a second write path with none of the card's guards on it. */
      say('This row has no voice control on it, so there is nothing for V to press here.',
          'refusal');
      return;
    }
    /* THE COST IS SAID BEFORE IT IS SPENT. The capture blocks for the recording plus the
     * transcription, which on whisper.cpp is roughly the length of the audio again, and a control
     * that goes quiet for half a minute with nothing on the screen reads as a hang. The receipt
     * that follows carries the measured round trip. */
    say('Recording. Speak now. The transcript posts as a note on this row when the engine '
        + 'finishes, and the audio is retained and hashed either way.', '', 60000);
    form.querySelector('button[type=submit]').click();
  }

  /* ------------------------------------------------------------------ the legend */

  /* A KEYBOARD NOBODY IS TOLD ABOUT IS A KEYBOARD NOBODY USES, and a permanent legend is 40px of
   * chrome above a queue that just spent a lane getting shorter. So it is a key, it costs
   * nothing until it is pressed, and it counts nothing. */
  /* AND THE OTHER HALF OF THE SAME RULE: WHAT THE KEYS SAY WHEN THERE IS NO ROW.
   *
   * MEASURED 2026-08-30 on live `brain`: `/queue` defaults to the `decide` tier and his decide
   * tier is 0 of 0, so THE FIRST PAGE HE OPENS HAS NO ROWS ON IT. The guard below returned
   * before the switch, so `?`, space, all four arrows and Escape produced no observable effect
   * at all: 9 of 9 keys silent, 2 of 2 themes, measured. He had just been told the keyboard
   * model is real, and the natural conclusion from a key that does nothing is that it is not.
   *
   * The sentence is READ OFF THE PAGE rather than composed here, so it cannot drift from what
   * the screen says: the same `.empty` block already names the tier, counts the others and
   * carries the links to them. A second copy of those numbers in this file is the "two counts
   * of one obligation, disagreeing, on one screen" defect that queue.html's own comment
   * records being repaired.
   */
  function nothingHere() {
    var box = document.querySelector('.empty');
    var big = box ? box.querySelector('.big') : null;
    var sub = box ? box.querySelector('.sub') : null;
    var where = big ? (big.textContent || '').replace(/\s+/g, ' ').trim().replace(/\.$/, '') : '';
    var other = sub ? (sub.textContent || '').replace(/\s+/g, ' ').trim() : '';
    /* THE CLAUSE ABOUT LINKS IS ONLY SAID WHERE THERE ARE LINKS. The queue-zero branch of the
     * same `.empty` block renders agent counts and no anchors at all, and a sentence promising
     * him a door that is not on the screen is the defect this whole night is about. */
    var linked = !!(sub && sub.querySelector('a'));
    say((where || 'No rows are on this screen')
        + ', so there is no row for that key to act on.'
        + (other ? ' ' + other + '.' : '')
        + (linked ? ' Those tier names are links.' : '')
        + ' Press ? for the keys.', 'refusal');
  }

  function legend() {
    say('↑↓ move  ·  space open and close  ·  → deeper, ← back  ·  '
        + 'esc close  ·  1-9 pick an option  ·  ↑↓ move between options while a row '
        + 'with options is open  ·  ctrl+z undo the last thing you did  ·  v voice note  ·  '
        /* ROW 0434. Two keys added to this string and nothing else in this module changed. A
         * keyboard nobody is told about is a keyboard nobody uses, and this legend is the only
         * place the keys are named; a filter reachable only with a mouse, listed nowhere, is
         * half built. The keys themselves are bound in the 0434 module below, never here. */
        + 'f filter  ·  / search  ·  '
        + '? this line', '', 12000);
  }

  /* ------------------------------------------------------------------ the keys */

  /* WHERE THE CARET IS, AND A RADIO IS NOT A CARET. A checked radio holds focus after `pick()`
   * puts it there, deliberately, so the poll cannot repaint the selection away; if that counted
   * as typing, every key in this module would stop working the moment he picked an option. A
   * radio and a checkbox take no text, so no keystroke here can destroy anything he has written
   * by firing over one. */
  function typing(t) {
    if (!t) return false;
    var tag = (t.tagName || '').toLowerCase();
    if (tag === 'input') {
      var ty = (t.getAttribute('type') || 'text').toLowerCase();
      return ty !== 'radio' && ty !== 'checkbox';
    }
    return tag === 'textarea' || tag === 'select' || t.isContentEditable;
  }

  document.addEventListener('keydown', function (e) {
    /* Ctrl+Z FIRST AND ON ITS OWN TERMS: it is the one key here that is a modifier chord, and it
     * must work from wherever he is, including with the caret in a reason box he has decided not
     * to send. Everything below it is a bare key and must never fire while he is typing. */
    var z = (e.key === 'z' || e.key === 'Z');
    if (z && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey) {
      /* A text field has its own undo and it is the browser's. Taking it away to reverse a
       * QUEUE act would lose him the sentence he is writing, which is the data-loss shape 0431
       * closed rather than one to reopen. */
      if (typing(e.target)) return;
      e.preventDefault();
      undo();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (typing(e.target)) return;

    /* THE KEYS THAT ARE NOT ABOUT A ROW ARE ANSWERED BEFORE THE EMPTY-TIER GUARD, and that
     * ordering is the whole fix. `?` is the key the operator is TOLD to press first, and the
     * legend it prints is a statement about the keyboard rather than about anything on the
     * screen; `v` says what V is not, which is equally true of an empty tier. Both used to sit
     * inside the switch below, on the far side of a return, so both were dead on the one page
     * he opens. They are handled here and REMOVED from the switch rather than left in it: a
     * case that can never be reached is worse than an absent one, because it reads as live. */
    if (e.key === '?') { e.preventDefault(); legend(); return; }
    if (e.key === 'v' || e.key === 'V') { e.preventDefault(); voice(); return; }

    var c = cursor(), rs = rows();
    /* THE GUARD STAYS, AND IT SPEAKS. It is not decoration: `rs[0]`, `rs.indexOf` and the
     * arrow clamps below all assume a non-empty list, so deleting it would trade a silent key
     * for a thrown one. What changes is that the keys which genuinely need a row now SAY they
     * need one, in the page's own words, instead of doing nothing. Escape is deliberately not
     * in this set: it is silent when nothing is open on a tier that HAS rows too (measured),
     * so making it speak only here would make the key mean two different things. */
    if (!rs.length) {
      if (/^(ArrowUp|ArrowDown|ArrowLeft|ArrowRight| |Spacebar|[1-9])$/.test(e.key)) {
        e.preventDefault();
        nothingHere();
      }
      return;
    }
    var i = c.row ? rs.indexOf(c.row) : -1;

    switch (e.key) {
      case 'ArrowDown':
      case 'ArrowUp': {
        var down = e.key === 'ArrowDown';
        /* BETWEEN OPTIONS WHEN A ROW WITH OPTIONS IS OPEN, BETWEEN ITEMS OTHERWISE, which is his
         * sentence read literally: "arrows between items and between options". The inner list
         * only exists while the row holding it is open, so there is no mode to remember and no
         * mode to be stuck in: close the row and the arrows are back on the queue. */
        var opts = c.level > 0 ? optionsOf(c.row) : [];
        if (opts.length) {
          var at = -1;
          for (var k = 0; k < opts.length; k++) { if (opts[k].checked) at = k; }
          var nxt = at < 0 ? (down ? 0 : opts.length - 1)
                           : Math.min(opts.length - 1, Math.max(0, at + (down ? 1 : -1)));
          e.preventDefault();
          pick(c.row, opts[nxt].value);
          return;
        }
        e.preventDefault();
        var j = i < 0 ? (down ? 0 : rs.length - 1)
                      : Math.min(rs.length - 1, Math.max(0, i + (down ? 1 : -1)));
        /* ROW 0433: IN DEEP WORK THE FOCUSED ITEM IS IN THE RIGHT PANEL, which is his ask 9 read
         * literally. Outside deep work an arrow moves the cursor and opens nothing, because the
         * queue is a list he is scanning; inside it there is one item and the panel is where it
         * lives, so moving the cursor moves what the panel shows. Space still closes it and
         * Escape still collapses to the bare cursor, so the panel is not a mode he is stuck in. */
        var lvl = rs[j] === c.row ? c.level : (DEEP ? 2 : 0);
        go(rs[j].id.replace(/^card-/, ''), lvl);
        return;
      }
      case ' ':
      case 'Spacebar': {
        e.preventDefault();
        if (!c.row) { go(rs[0].id.replace(/^card-/, ''), 1); return; }
        go(c.id, c.level > 0 ? 0 : 1);
        return;
      }
      case 'ArrowRight': {
        if (!c.row) { e.preventDefault(); go(rs[0].id.replace(/^card-/, ''), 1); return; }
        if (c.level >= 2) return;                     /* already the deepest level: nothing moves */
        e.preventDefault();
        go(c.id, c.level + 1);
        return;
      }
      case 'ArrowLeft': {
        if (!c.row || c.level <= 0) return;
        e.preventDefault();
        go(c.id, c.level - 1);
        return;
      }
      case 'Escape': {
        if (!c.row || c.level <= 0) return;
        e.preventDefault();
        go(c.id, 0);
        return;
      }
      /* `?` and `v` were two cases here. They are answered above the empty-tier guard now,
       * because neither one is about a row and both were dead on a tier with none. */
      default: break;
    }

    /* NUMBER KEYS PICK AN OPTION, and a number pressed on a row that has none says so rather
     * than doing nothing. `e.key` and not `e.keyCode`, so the numeric keypad and the row of
     * digits both arrive as the digit they printed. */
    if (/^[1-9]$/.test(e.key)) {
      if (!c.row) {
        say('Nothing is selected. Press ↓ to put the cursor on a row, then a number to pick '
            + 'one of its options.', 'refusal');
        e.preventDefault();
        return;
      }
      if (c.level === 0) { go(c.id, 1); c = cursor(); }               /* a pick implies a look */
      var opts2 = optionsOf(c.row);
      e.preventDefault();
      if (!opts2.length) {
        say(c.id + ' has no drafted options, so there is nothing for ' + e.key + ' to pick. Its '
            + 'verbs are in the row.', 'refusal');
        return;
      }
      if (!pick(c.row, e.key)) {
        say(c.id + ' has ' + opts2.length + ' option'
            + (opts2.length === 1 ? '' : 's') + ', so there is no ' + e.key + '.', 'refusal');
      }
    }
  });

  /* ROW 0433: DEEP WORK ARRIVES WITH ONE ITEM ALREADY IN THE PANEL. Landing in deep work on a
   * list where nothing is focused is the state he described as not giving off the vibe of going
   * deep: it is the same list with fewer things around it. `scroll:false` because this runs on
   * load, and a page that scrolls itself before he has touched it is the 0409 shape. It writes
   * the hash with `replaceState`, so it costs no history entry and no navigation. */
  (function () {
    if (!DEEP) return;
    if (cursor().row) return;                          /* he came back to a row he already had */
    var rs0 = rows();
    if (rs0.length) go(rs0[0].id.replace(/^card-/, ''), 2, false);
  })();

  paintCursor();
  window.addEventListener('hashchange', paintCursor);
  if (window.MutationObserver) new MutationObserver(paintCursor).observe(host, { childList: true });

  /* The same shape `window.__optionRail` above already uses: a small handle so a suite can drive
   * the model rather than synthesise key events at a browser it does not own. */
  window.__keyboard = { cursor: cursor, go: go, undo: undo, pick: pick };
})();

/* ==========================================================================================
 * ROW 0434 -- THE DEFAULT ORDER, SAID OUT LOUD, AND FILTERS OVER IT
 * ==========================================================================================
 *
 * His ask 4, in his words: *"default order by urgency x importance, with Notion-style filtering
 * by project, client, task, priority and custom fields."*
 *
 * A SIBLING MODULE, exactly as the 0431 and 0432 blocks above are. It edits neither of them and
 * it never touches the core IIFE's `patch()` or `wire()`. It shares one thing with each: the list
 * host, which it observes, and `window.__keyboard`, which 0432 published and which this module
 * CALLS rather than reimplements when a filter hides the row the cursor was on.
 *
 * ------------------------------------------------------------------------------------------
 * WHAT THIS DOES NOT DO, AND IT IS THE HALF THAT IS A DECISION RATHER THAN A BUILD
 * ------------------------------------------------------------------------------------------
 *
 * IT DOES NOT SORT. `MUST-NOT-BUILD` item 2 -- score editing and drag-to-reorder, pin and defer
 * only -- is KEPT. No control here writes `priority`, there is no drag handle, and the column
 * header is still undeliverable to a click: row 0431 gave it `pointer-events:none`, no hover, no
 * cursor and no caret, and this lane did not take any of that away. Making a header clickable is
 * one CSS rule and a ruling that is his, not a lane's.
 *
 * FILTERING IS NOT SORTING AND THAT IS WHY IT IS PERMITTED HERE. A filter changes WHICH rows are
 * on the screen. The rows that remain arrive in the order `human_queue.ranking.rank` put them in,
 * every time, and nothing in this module reorders a single node. Measured: `apply()` writes
 * `style.display` and nothing else, and the check that pins it walks the ids before and after a
 * filter and asserts the surviving sequence is a SUBSEQUENCE of the original.
 *
 * ------------------------------------------------------------------------------------------
 * WHAT IT ORDERS BY TODAY, WHICH IS NOT WHAT HE ASKED FOR, AND WHY THE STRIP SAYS THE FIRST
 * ------------------------------------------------------------------------------------------
 *
 * The sentence in `#qfilter` is rendered by the server out of `human_queue.rank.DEFAULT_WEIGHTS`.
 * It is an ADDITIVE sum of six terms -- urgency, stakes, charter alignment, what it unblocks, age
 * and the operator's decaying bumps -- and NOT a product of urgency and importance. There is no
 * signal called importance in the vocabulary at all. Changing the sum into a product is a change
 * to the maths and row 0434's own brief scopes that row to the surface, so this module states the
 * order truthfully and the argument for or against changing it is in the lane report with the
 * measurement attached.
 *
 * THE TIE NOTE IS WHY THAT MATTERS ON HIS OWN BOARD. Measured on live `brain` 2026-08-29: the
 * Shape tier holds 30 items, the window renders 7, and all 7 carry the score 5.335. The order he
 * was looking at was a seven-way tie broken by id, and nothing on the screen said so. When the
 * rendered rows share a score this strip says it, and it says what broke the tie.
 *
 * ------------------------------------------------------------------------------------------
 * WHERE THE STATE LIVES, AND WHY IT IS NOT THE HASH
 * ------------------------------------------------------------------------------------------
 *
 * The QUERY STRING, written with `replaceState`, never `location.search =` (which navigates).
 * `location.hash` is already spoken for three times over -- `#row-`, `#card-` and `#pan-` are
 * 0431's and 0432's cursor and two expansion levels -- and both modules read it by PREFIX, so a
 * filter appended to the fragment would stop those prefixes matching and close every open row.
 *
 * The query string costs nothing and buys two things. The poll already sends `location.search`
 * to `/api/patch/<room>`, and `_queue_ctx` reads only `tier`, `deep` and `open`, so the filter
 * params ride along and are ignored, which is exactly right: the SERVER must not filter, because
 * a server-side filter over a window is a second window. And a filtered queue is now a URL he can
 * keep.
 *
 * ------------------------------------------------------------------------------------------
 * WHAT THE FILTER CAN AND CANNOT SEE, AND IT PRINTS ITS OWN DENOMINATOR
 * ------------------------------------------------------------------------------------------
 *
 * It filters THE ROWS THAT WERE RENDERED, which is one tier's window: seven items, not the tier.
 * It cannot see what is below the window, what a blocker is holding, or what carries a wake
 * condition. Those three numbers are already on this page and are handed to this module as data
 * so that the count it prints says what it is a count OF. A filter reporting `0 of 0` over a tier
 * of twenty would be this console's own "8 items counted and unreachable" defect wearing a new
 * control, and this is the same board that was already caught telling him there were more and
 * giving him no way there.
 *
 * ------------------------------------------------------------------------------------------
 * WHY THE CONTROLS ARE BUILT HERE AND NOT SERVED, AND WHY NOT BELOW 900px
 * ------------------------------------------------------------------------------------------
 *
 * Served controls with JavaScript off are six affordances that cannot work, which is item 2's own
 * sentence and the reason 0431 made the header unclickable rather than merely inert. Nothing here
 * exists until the script that drives it is running.
 *
 * And nothing here exists below 900px. `console.css` puts a 44px floor under every `button`,
 * `select` and `input` below 899px, which is the phone tap-target sweep and is correct; five
 * dropdowns and a search box under it is 264px of a 390px screen, and even a collapsed control is
 * a 44px button. That is the surface lane B4 spent a section reclaiming 47 pixels of and the one
 * he says his 7am pass happens on. The SENTENCE still renders there, because it is text.
 */
(function () {
  var host = document.querySelector('[data-region="list"]');
  var bar = document.getElementById('qfilter');
  if (!host || !bar) return;             /* not the queue: /scope, /stack and /terminal have none */
  var slot = document.getElementById('qfilter-controls');
  var said = document.getElementById('qfilter-said');
  if (!slot || !said) return;

  var WIDE = 900;                        /* the same boundary `console.css` puts the 44px floor at */

  /* THE FIVE FACETS, AND EVERY ONE OF THEM IS A COLUMN THAT ALREADY EXISTS.
   *
   * `key`   the query-string parameter, so a filtered queue is a URL.
   * `attr`  the `data-` attribute `macros.html` stamps on the row.
   * `label` what the dropdown says. It is the field's own name and never a borrowed one: there
   *         is no client column in `brain`, so nothing here is labelled "client".
   * `fmt`   how a value is spelled in the dropdown. `priority` is ascending-urgent -- 0 is P0 --
   *         so it renders as `P0` and never as a bare integer a reader would take for a size.
   */
  var FACETS = [
    { key: 'f_lane', attr: 'lane', label: 'lane' },
    { key: 'f_kind', attr: 'kind', label: 'what it is' },
    { key: 'f_prio', attr: 'prio', label: 'priority',
      fmt: function (v) { return 'P' + v; } },
    { key: 'f_project', attr: 'project', label: 'project' },
    { key: 'f_gate', attr: 'gate', label: 'gate' }
  ];
  var SEARCH = 'f_q';

  function num(name, dflt) {
    var v = parseInt(bar.getAttribute('data-' + name) || '', 10);
    return isNaN(v) ? dflt : v;
  }

  /* ------------------------------------------------------------------ the state, in the URL */

  function state() {
    var p = new URLSearchParams(location.search), out = {};
    FACETS.forEach(function (f) { out[f.key] = p.get(f.key) || ''; });
    out[SEARCH] = p.get(SEARCH) || '';
    return out;
  }

  function active(st) {
    var n = 0;
    Object.keys(st).forEach(function (k) { if (st[k]) n++; });
    return n;
  }

  /* `replaceState` AND NOT `pushState`, which is 0431's ruling on the same question and for its
   * reason: one history entry per keystroke in a search box would make Back unusable. It is also
   * not `location.search =`, which navigates and would throw away the open row, the cursor and
   * every panel on the page to change a dropdown. */
  function write(key, value) {
    var p = new URLSearchParams(location.search);
    if (value) { p.set(key, value); } else { p.delete(key); }
    var q = p.toString();
    if (window.history && history.replaceState) {
      history.replaceState(null, '', location.pathname + (q ? '?' + q : '') + location.hash);
    }
    apply();
  }

  function clearAll() {
    var p = new URLSearchParams(location.search);
    FACETS.forEach(function (f) { p.delete(f.key); });
    p.delete(SEARCH);
    var q = p.toString();
    if (window.history && history.replaceState) {
      history.replaceState(null, '', location.pathname + (q ? '?' + q : '') + location.hash);
    }
    apply();
  }

  /* ------------------------------------------------------------------ reading the rows */

  function rows() {
    return Array.prototype.slice.call(host.querySelectorAll('.qrow'));
  }

  function attr(row, name) {
    return (row.getAttribute('data-f-' + name) || '');
  }

  function matches(row, st) {
    for (var i = 0; i < FACETS.length; i++) {
      var f = FACETS[i], want = st[f.key];
      if (!want) continue;
      var have = attr(row, f.attr);
      if (f.attr === 'gate') {
        /* A row carries zero, one or two gates in one attribute, so this one is a membership
         * test and not an equality. `(none)` is a real answer to "which gate" and is the only
         * value in this module that is not a value of the column. */
        if (want === '(none)') { if (have) return false; }
        else if ((' ' + have + ' ').indexOf(' ' + want + ' ') < 0) return false;
      } else if (want === '(none)') {
        if (have) return false;
      } else if (have !== want) {
        return false;
      }
    }
    var q = (st[SEARCH] || '').trim().toLowerCase();
    /* PRE-LOWERCASED ON THE ROW by `macros.html`, so this is one `indexOf` per row per keystroke
     * rather than a `toLowerCase()` of every title on every one. It searches the id, the title
     * and the task the row blocks, which are the three things a person types. */
    if (q && attr(row, 'text').indexOf(q) < 0) return false;
    return true;
  }

  /* ------------------------------------------------------------------ painting */

  /* WHY `style.display` AND NOT A CLASS. `console.css` is lane C2's file tonight and this lane
   * may not write a rule in it, so a class would have nothing behind it. An inline `display:none`
   * needs no stylesheet, out-specifies every rule in one without touching any, and is removed by
   * assigning the empty string rather than by remembering what was there. The exact rule this
   * would prefer to be is written to `outputs/2026-08-29-commander/crosstalk-C3.md`.
   *
   * `display:none` AND NOT `visibility` OR AN OPACITY: a filtered-out row must have no box, take
   * no tap target and take no focus. A row that is invisible and still arrowable is the
   * affordance-that-cannot-work failure with the ends swapped. */
  function apply() {
    var st = state(), rs = rows(), shown = 0, kept = [];
    for (var i = 0; i < rs.length; i++) {
      var ok = matches(rs[i], st);
      rs[i].style.display = ok ? '' : 'none';
      if (ok) { shown++; kept.push(rs[i]); }
    }
    describe(st, shown, rs.length, kept);
    syncControls(st);
    rescueCursor(kept);
    return { shown: shown, of: rs.length };
  }

  /* THE CURSOR AND THE OPEN ROW ARE 0432'S AND 0431'S, AND A FILTER MUST NOT STRAND EITHER.
   *
   * Hiding the row the cursor is on leaves the cursor pointing at a `display:none` element: the
   * next arrow starts from a row that is not on the screen, and `space` opens something invisible.
   * So when the filter hides the current row, the cursor moves to the first row that survived,
   * through `window.__keyboard.go` -- 0432's own published handle -- so the hash, the scroll
   * behaviour and the paint are all that module's and there is no second copy of them here. When
   * NOTHING survives, the cursor is left exactly where it is rather than invented somewhere: a
   * filter matching nothing has no row to be on, and clearing the filter must put him back. */
  function rescueCursor(kept) {
    if (!window.__keyboard || !window.__keyboard.cursor) return;
    var c = window.__keyboard.cursor();
    if (!c.row) return;
    if (c.row.style.display !== 'none') return;
    if (!kept.length) return;
    window.__keyboard.go(kept[0].id.replace(/^card-/, ''), 0, false);
  }

  /* ------------------------------------------------------------------ the sentence */

  /* THE TIE, MEASURED ON THE ROWS THAT ARE ACTUALLY ON THE SCREEN. Every term of the score is
   * printed per row by `queue why` and in the panel's `why` line; what nothing said until this
   * one is that several rows carry the SAME score, so the order between them is `rank`'s id
   * tiebreak and not a judgement. It appears only when it is true, and it names the tiebreak
   * rather than implying a defect: ties are how an additive score behaves on rows a producer
   * declared identically, and the bump is the sanctioned way to disagree with one. */
  /* THE LARGEST TIED RUN AND NOT ONLY THE TOP ONE, because a six-way tie at positions two to
   * seven is exactly as uninformative as one at the top and is the shape this board actually
   * produces. The rows are already in rank order, so a tie is a contiguous run and one pass
   * finds the longest. It names the POSITIONS, because "rows 2 to 7" is what he is looking at
   * and a score on its own does not say where. */
  function tieNote(kept) {
    if (kept.length < 2) return '';
    var best = 0, bestAt = 0, bestScore = null, run = 1, prev = null;
    for (var i = 0; i < kept.length; i++) {
      var s = parseFloat(kept[i].getAttribute('data-f-score'));
      if (isNaN(s)) return '';
      if (prev !== null && s === prev) { run++; }
      else { run = 1; }
      if (run > best) { best = run; bestAt = i - run + 2; bestScore = s; }
      prev = s;
    }
    if (best < 2) return '';
    return ' · rows ' + bestAt + '-' + (bestAt + best - 1) + ' tied at '
      + bestScore.toFixed(2) + ', then by id';
  }

  function describe(st, shown, total, kept) {
    var base = said.getAttribute('data-base');
    if (base === null) { base = said.textContent; said.setAttribute('data-base', base); }
    /* THE SERVER'S LONG FORM IS THE BASE OF THE TOOLTIP AND IS NEVER OVERWRITTEN. It is the full
     * decomposition of the ordering, which is the thing this strip exists to make available; a
     * window-denominator note appended below is an addition to it and not a replacement. */
    var baseTitle = said.getAttribute('data-base-title');
    if (baseTitle === null) {
      baseTitle = said.getAttribute('title') || '';
      said.setAttribute('data-base-title', baseTitle);
    }
    said.title = baseTitle;
    var below = num('below-window', 0), blocked = num('blocked', 0), deferred = num('deferred', 0);
    var tier = bar.getAttribute('data-tier') || 'this tier';
    var txt = base + tieNote(kept);
    if (active(st)) {
      txt += ' · ' + shown + ' of ' + total + ' shown';
      /* THE DENOMINATOR, AND IT IS THE WHOLE REASON THIS SAYS MORE THAN "4 of 7". The filter runs
       * over the rendered window. What is below it, blocked or deferred was never on this page
       * and is therefore not filtered OUT, it is simply not here, and a control that did not say
       * so would let him read `0 of 7` as "nothing matches" on a tier holding twenty. It is kept
       * SHORT so the strip stays one line: the long form is on the element's own `title`. */
      var unseen = [];
      if (below) unseen.push(below + ' below the window');
      if (blocked) unseen.push(blocked + ' blocked');
      if (deferred) unseen.push(deferred + ' deferred');
      if (unseen.length) {
        txt += ' · not searched: ' + unseen.join(', ');
        said.title = baseTitle + '  ---  the filter runs in the browser over the rows this tier '
          + 'RENDERED, which is a window on it. ' + unseen.join(', ') + ' in ' + tier
          + ' were never on this page, so they are not hidden by the filter, they are absent '
          + 'from what it can see.';
      }
      if (!shown) txt += ' · nothing here matches, clear to see ' + tier + ' again';
    }
    said.textContent = txt;
  }

  /* ------------------------------------------------------------------ the controls */

  /* THE OPTION LISTS ARE READ OFF THE ROWS ON EVERY REPAINT, never cached beside the DOM. The
   * poll replaces this list wholesale, so a lane arriving on a new row appears in the dropdown by
   * the next paint, and a lane that left stops being offered. A cached universe would go stale in
   * the one direction that matters: offering a value that matches nothing. */
  function values(f, rs) {
    var seen = Object.create(null), out = [], none = false;
    for (var i = 0; i < rs.length; i++) {
      var v = attr(rs[i], f.attr);
      if (f.attr === 'gate') {
        var parts = v ? v.split(' ') : [];
        if (!parts.length) { none = true; }
        for (var k = 0; k < parts.length; k++) {
          if (parts[k] && !seen[parts[k]]) { seen[parts[k]] = 1; out.push(parts[k]); }
        }
        continue;
      }
      if (!v) { none = true; continue; }
      if (!seen[v]) { seen[v] = 1; out.push(v); }
    }
    out.sort();
    if (none && out.length) out.push('(none)');
    return out;
  }

  /* ------------------------------------------------------------------ open, and why it is shut
   *
   * THE BAR IS SHUT BY DEFAULT AND THE REASON IS 79 PIXELS. Measured at 1440x1200 with it always
   * open: the strip was 79px tall and row one began at y=432, on a page row 0431 had just spent a
   * lane bringing from y=506 to y=329. Shut, the strip is one line and row one is where 0431 left
   * it. A filter bar is a thing he opens on purpose, once, when he is looking for something; the
   * order sentence beside it is a thing he reads at a glance, so that stays.
   *
   * AND IT CANNOT BE SILENTLY ON. If any filter is active the bar OPENS ITSELF and stays open,
   * because a queue that is hiding rows behind a collapsed control is a queue lying about what is
   * in it, and that is this board's own "8 items counted and unreachable" shape with a lid on it.
   */
  var opened = false;

  function isOpen(st) { return opened || active(st) > 0; }

  /* ------------------------------------------------------------------ when NOT to rebuild
   *
   * THE BAR IS REBUILT ONLY WHEN WHAT IT WOULD SAY HAS CHANGED, and never while the caret is in
   * it. This is the same lesson row 0431 wrote about the reason box, arriving through a different
   * door: `build()` empties `#qfilter-controls` and makes new elements, and the list repaints
   * every three seconds on any board where anything is moving, so an unconditional rebuild
   * destroys and recreates the dropdown the operator is holding open, twenty times a minute.
   *
   * WATCHED HAPPENING, 2026-08-29, and it was found by the suite rather than reasoned about: a
   * `click` on the bar's own opener timed out for its full 30 seconds while `document.
   * getElementById` had answered a millisecond earlier that the button was there. The button was
   * there. It was a DIFFERENT button by the time the click landed.
   *
   * The signature is the facet universe plus the open state plus the width bucket, which is
   * exactly the set of things that change what the bar RENDERS. The selected values are not in it
   * -- those are painted by `syncControls`, which writes a `value` and never a node. */
  var built = null;

  function signature(rs, st) {
    var parts = [isOpen(st) ? 'open' : 'shut', window.innerWidth >= WIDE ? 'wide' : 'narrow'];
    for (var i = 0; i < FACETS.length; i++) {
      parts.push(FACETS[i].key + '=' + values(FACETS[i], rs).join(','));
    }
    return parts.join('|');
  }

  function build(force) {
    var rs = rows(), st = state();
    /* THE CARET WINS, ALWAYS. The core IIFE's `focusedRegion()` makes the same call about the
     * regions it patches; this bar is not a region, so it makes it for itself. */
    if (!force && slot.contains(document.activeElement)) return;
    var sig = signature(rs, st);
    if (!force && sig === built) return;
    built = sig;
    slot.textContent = '';
    if (window.innerWidth < WIDE) return;                /* the phone: the sentence, and no more */
    if (!isOpen(st)) {
      var link = document.createElement('button');
      link.type = 'button';
      link.id = 'qf-open';
      link.textContent = 'filter';
      link.title = 'filter these rows by lane, kind, priority, project or gate, or search them. '
                 + 'Keyboard: f';
      link.style.marginRight = '.6rem';
      link.style.fontFamily = 'var(--font-mono)';
      link.style.fontSize = '.68rem';
      link.addEventListener('click', function () { opened = true; build(true); apply(); });
      slot.appendChild(link);
      return;
    }
    var wrap = document.createElement('span');
    wrap.id = 'qfilter-wrap';
    wrap.style.marginRight = '.6rem';
    FACETS.forEach(function (f) {
      var vals = values(f, rs);
      var sel = document.createElement('select');
      sel.id = 'qf-' + f.attr;
      sel.setAttribute('data-facet', f.key);
      sel.setAttribute('aria-label', 'filter by ' + f.label);
      sel.style.marginRight = '.35rem';
      sel.style.fontFamily = 'var(--font-mono)';
      sel.style.fontSize = '.68rem';
      var any = document.createElement('option');
      any.value = '';
      /* A FACET WITH NOTHING BEHIND IT SAYS WHY RATHER THAN OFFERING AN EMPTY LIST. `project` is
       * the one that will hit this on his own board: live `brain` is at ledger 42, migration 44
       * is unapplied, and `brain.work_item.project` does not exist there at all. The server
       * measured which of the three cases this store is in and the sentence is its words. */
      any.textContent = f.label + ': any';
      sel.appendChild(any);
      if (!vals.length) {
        sel.disabled = true;
        if (f.attr === 'project') {
          any.textContent = 'project: ' + (bar.getAttribute('data-project-column') === '1'
            ? 'none set' : 'not on this store');
          sel.title = bar.getAttribute('data-project-note') || '';
        } else {
          any.textContent = f.label + ': none';
          sel.title = 'no row on this tier carries a ' + f.label + '.';
        }
      }
      vals.forEach(function (v) {
        var o = document.createElement('option');
        o.value = v;
        o.textContent = f.label + ': ' + (f.fmt && v !== '(none)' ? f.fmt(v) : v);
        sel.appendChild(o);
      });
      sel.value = st[f.key] || '';
      if (sel.value !== (st[f.key] || '')) { sel.value = ''; }   /* a value the tier no longer has */
      sel.addEventListener('change', function () { write(f.key, sel.value); });
      wrap.appendChild(sel);
    });

    var q = document.createElement('input');
    q.type = 'text';
    q.id = 'qf-q';
    q.placeholder = 'search id or title';
    q.value = st[SEARCH] || '';
    q.setAttribute('aria-label', 'search the rendered rows by id, title or the task they block');
    q.style.fontFamily = 'var(--font-mono)';
    q.style.fontSize = '.68rem';
    q.style.marginRight = '.35rem';
    /* A WIDTH, BECAUSE THE DEFAULT ONE COST A WHOLE LINE. `.chipnote` is `width:100%` and a
     * bare text input takes its own size box, so the search field wrapped onto a line of its
     * own and pushed `clear` and `hide` onto a third: measured, the open bar was three lines
     * and 79px. 12rem fits an id and a couple of words, which is what is typed into it. */
    q.style.width = '12rem';
    /* `input` AND NOT `change`: a power user expects the list to narrow as he types, and it costs
     * one `indexOf` per rendered row per keystroke over at most a tier's window. No debounce, no
     * request, and measured below a millisecond. */
    q.addEventListener('input', function () { write(SEARCH, q.value); });
    wrap.appendChild(q);

    var clr = document.createElement('button');
    clr.type = 'button';
    clr.id = 'qf-clear';
    clr.textContent = 'clear';
    clr.style.fontFamily = 'var(--font-mono)';
    clr.style.fontSize = '.68rem';
    clr.addEventListener('click', function () {
      clearAll();
      build(true);
    });
    wrap.appendChild(clr);

    /* A SEPARATE CONTROL FROM `clear`, AND NOT THE SAME BUTTON WEARING TWO LABELS. `clear` drops
     * the filter and `hide` puts the bar away, and they are different acts: a bar that said
     * "clear" while a filter was on and "hide" when it was not would be one control whose meaning
     * changes under the cursor, which is MUST-NOT-BUILD item 1's own failure at a smaller scale.
     * Hiding while a filter is ON is refused by `isOpen`, because a collapsed active filter is a
     * queue lying about what is in it. */
    var hide = document.createElement('button');
    hide.type = 'button';
    hide.id = 'qf-hide';
    hide.textContent = 'hide';
    hide.title = 'put the filter bar away. It will not hide while a filter is on.';
    hide.style.marginLeft = '.35rem';
    hide.style.fontFamily = 'var(--font-mono)';
    hide.style.fontSize = '.68rem';
    hide.addEventListener('click', function () {
      /* IT REFUSES OUT LOUD WHEN A FILTER IS ON. `isOpen` keeps the bar open while anything is
       * filtering, so without this sentence the button would be pressed and nothing would happen,
       * which is the bound-key-that-does-nothing defect MUST-NOT-BUILD item 10 is about, wearing a
       * mouse. Refusing and saying why is the shape row 0399's disabled `done` control already
       * uses on this surface. */
      if (active(state())) {
        say('The filter bar stays open while a filter is on, because a queue hiding rows behind a '
            + 'closed control is a queue lying about what is in it. Press clear first.', 'refusal');
        return;
      }
      opened = false; build(true); apply();
    });
    wrap.appendChild(hide);
    slot.appendChild(wrap);
  }

  /* The dropdowns follow the URL and never the other way round, so a filter arrived at by
   * pasting a link paints the same as one arrived at by clicking. */
  function syncControls(st) {
    FACETS.forEach(function (f) {
      var sel = document.getElementById('qf-' + f.attr);
      if (sel && sel.value !== (st[f.key] || '')) {
        var ok = false;
        for (var i = 0; i < sel.options.length; i++) {
          if (sel.options[i].value === (st[f.key] || '')) ok = true;
        }
        if (ok) sel.value = st[f.key] || '';
      }
    });
    var q = document.getElementById('qf-q');
    if (q && document.activeElement !== q && q.value !== (st[SEARCH] || '')) {
      q.value = st[SEARCH] || '';
    }
  }

  /* ------------------------------------------------------------------ the keyboard */

  /* HIS FRAMING IS *"our people are going to be power users, not iPad babies"*, so a filter that
   * can only be reached with a mouse is half built. Two keys, and both of them move the focus to
   * a control that is already on the screen rather than doing anything a click could not:
   *
   *   f    the first dropdown
   *   /    the search box
   *
   * A NATIVE `<select>` AND A NATIVE `<input>` ARE WHY THERE ARE ONLY TWO KEYS. Once focus is in
   * one, the browser's own keyboard model takes over: arrows change the value, type-ahead jumps
   * to an option, Tab moves along the bar, Escape leaves the dropdown. Rebinding any of that here
   * would be this module inventing a worse copy of behaviour every reader already knows, and
   * 0432's `typing()` already treats a `select` and a text input as typing, so no queue key fires
   * over either.
   *
   * ON A PHONE THERE IS NOTHING TO FOCUS, so the key says so instead of failing silently, naming
   * the width and the reason. A bound key that does nothing and an unbound key look identical
   * from his chair, which is the defect MUST-NOT-BUILD item 10 is about. */
  function typing(t) {
    if (!t) return false;
    var tag = (t.tagName || '').toLowerCase();
    if (tag === 'input') {
      var ty = (t.getAttribute('type') || 'text').toLowerCase();
      return ty !== 'radio' && ty !== 'checkbox';
    }
    return tag === 'textarea' || tag === 'select' || t.isContentEditable;
  }

  function say(msg, kind) {
    var b = document.getElementById('saybar');
    if (!b) return;
    b.innerHTML = '';
    var d = document.createElement('div');
    d.className = 'banner ' + (kind || '');
    d.textContent = msg;
    b.appendChild(d);
    try { window.dispatchEvent(new Event('resize')); } catch (e) { /* placed on the next resize */ }
  }

  function focusFilter(which) {
    var el = document.getElementById(which);
    if (!el) {
      say('The filter bar is not on this screen. It is built above 900px only, because '
          + 'console.css puts a 44px floor under every dropdown below 899px and five of them '
          + 'plus a search box is 264px of a phone screen. This window is '
          + window.innerWidth + 'px wide.', 'refusal');
      return false;
    }
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    if (el.select) { try { el.select(); } catch (e2) { /* a select element has no select() */ } }
    return true;
  }

  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (typing(e.target)) return;
    if (e.key === 'f' || e.key === 'F') {
      e.preventDefault();
      /* `f` OPENS THE BAR AND THEN PUTS THE CARET IN IT, in one keystroke. A key that only worked
       * once a mouse had already opened the thing would be a keyboard route that depends on the
       * mouse, which is the opposite of what he asked for. */
      if (!opened) { opened = true; build(true); apply(); }
      if (focusFilter('qf-' + FACETS[0].attr)) {
        say('filter: ↑↓ or type to choose, tab along the bar, / for the search box, '
            + 'esc back to the queue. It filters the rows on this tier, and the strip says what '
            + 'it is not filtering.', '');
      }
      return;
    }
    if (e.key === '/') {
      e.preventDefault();
      if (!opened) { opened = true; build(true); apply(); }
      focusFilter('qf-q');
      return;
    }
  });

  /* ESCAPE OUT OF THE BAR IS BOUND HERE AND NOWHERE ELSE, on the controls themselves rather than
   * on `document`: the core IIFE owns Escape globally (it closes the door), 0432 owns Escape on
   * the queue (it collapses a row), and a third document-level handler for one key is how three
   * lanes come to disagree about what Escape means. Bound to the element, it fires only while the
   * caret is in this bar and it does exactly one thing: give the queue its keys back. */
  slot.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    e.preventDefault();
    e.stopPropagation();
    /* BLUR AND NOTHING ELSE. `.qrow` carries no `tabindex`, so there is nothing on the queue to
     * move the caret TO, and 0432's keys are on `document` and start working the moment the caret
     * leaves a control. An earlier version of this handler walked the rows calling `focus()` on
     * them, which did nothing at all and read as though it did. */
    if (e.target && e.target.blur) e.target.blur();
    say('back on the queue. Any filter you set is still on; press f to change it, or clear to '
        + 'drop it.', '');
  });

  /* ------------------------------------------------------------------ wiring */

  function paint() { build(); apply(); }

  paint();
  window.addEventListener('hashchange', function () { /* the cursor moved: the filter is unchanged
    and the rows did not, so there is nothing to repaint. Listed and empty on purpose, so the next
    reader does not add one. */ });
  window.addEventListener('resize', function () {
    var have = !!document.getElementById('qfilter-wrap');
    var want = window.innerWidth >= WIDE;
    if (have !== want) { build(true); apply(); }   /* crossing the boundary, and only then */
  });
  /* `childList` only, for the reason 0431's observer states: `patch()` swaps this host's children
   * in one assignment. `apply()` writes `style.display`, which is an ATTRIBUTE change on a child
   * and would be seen by an observer watching attributes or the subtree. This one watches neither,
   * so it cannot observe itself. */
  if (window.MutationObserver) new MutationObserver(paint).observe(host, { childList: true });

  /* The handle a suite drives, the same shape `window.__optionRail` and `window.__keyboard`
   * already publish: a check should exercise the model rather than synthesise events at a browser
   * it does not own. */
  window.__queueFilter = { state: state, apply: apply, write: write, clear: clearAll,
                           rows: rows, matches: matches, build: build };
})();
