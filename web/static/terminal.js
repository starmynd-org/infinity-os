/* The typable pane's client. No framework, no bundler, no worker, and NO SOCKET.
 *
 * `web/MUST-NOT-BUILD.md` item 11 was overruled on 2026-08-28 for this pane and permits a
 * websocket. THIS FILE DOES NOT TAKE IT. The screen is drawn on the server
 * (`web/terminal_screen.py`) and fetched here, so the browser probe that measures
 * `EventSource` and `WebSocket` NEVER CONSTRUCTED is still true on every route of this console.
 *
 * The transport is one bounded long-poll: `GET /terminal/<key>/screen?wait=N` returns the moment
 * the screen moves, or after N seconds with `changed:false`. That is a plain `fetch` that always
 * completes -- the property `web/app.py` claims for poll-and-patch and the reason it chose it
 * over SSE -- and a laptop that sleeps mid-poll leaves nothing half-open behind.
 *
 * WHY A SCREEN AND NOT A BYTE STREAM: a client that misses one screen is one frame behind and
 * then correct. A client that misses one frame of a raw pty stream is corrupt until something
 * repaints, which a full-screen TUI may never do.
 */
(function () {
  'use strict';

  var CSRF = window.__TERMINAL_CSRF || '';
  var scr = document.getElementById('tscreen');
  if (!scr || !CSRF) return;

  var hist = document.getElementById('thist');
  var scroll = document.getElementById('tscroll');
  var cursor = document.getElementById('tcursor');
  var ruler = document.getElementById('truler');
  var tabs = document.getElementById('ttabs');
  var errbox = document.getElementById('terr');
  var status = document.getElementById('tstatus');
  var sizeEl = document.getElementById('tsize');
  var footEl = document.getElementById('tfoot-meta');
  var placeholder = document.getElementById('tplaceholder');
  var closeBtn = document.getElementById('tclose');

  var key = null;              /* the session this pane is attached to */
  var rev = -1;                /* the screen revision the DOM currently holds */
  var histCount = 0;           /* scrollback lines already in the DOM */
  var lineCache = [];          /* per-line serialised markup, so a repaint touches only what moved */
  var mode = { app_cursor: false, bracketed: false, alive: false };
  var polling = 0;             /* a generation counter: an old loop notices it was superseded */
  var known = [];              /* every session this browser has open, for the tabs */

  /* ------------------------------------------------------------------ plumbing */

  function show(msg) {
    if (!msg) { errbox.hidden = true; errbox.textContent = ''; return; }
    errbox.hidden = false;
    errbox.textContent = msg;
  }

  function post(url, fields) {
    var body = new URLSearchParams();
    body.set('csrf', CSRF);
    Object.keys(fields || {}).forEach(function (k) { body.set(k, fields[k]); });
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF': CSRF },
      body: body.toString()
    }).then(function (r) { return r.json().catch(function () { return { ok: false, error: 'HTTP ' + r.status }; }); });
  }

  /* ------------------------------------------------------------------ geometry
   *
   * Measured in the ACTUAL rendered font, never assumed. The mono stack falls back differently on
   * Windows, WSL and the phone, and a cursor placed with a guessed advance width drifts one
   * column further wrong for every column across the screen. Ten characters divided by ten,
   * because a sub-pixel error divided by ten is ten times smaller. */
  function metrics() {
    var cw = ruler.getBoundingClientRect().width / 10;
    var lh = 13 * 1.35;
    return { cw: cw > 0 ? cw : 7.8, lh: lh };
  }

  function fitSize() {
    var m = metrics();
    var box = scroll.getBoundingClientRect();
    var cols = Math.max(20, Math.min(400, Math.floor((box.width - 22) / m.cw)));
    var rows = Math.max(6, Math.min(120, Math.floor((Math.min(box.height, window.innerHeight * 0.72) - 16) / m.lh)));
    return { rows: rows, cols: cols };
  }

  /* ------------------------------------------------------------------ painting */

  function runsToHTML(runs) {
    if (!runs.length) return '';
    var out = '';
    for (var i = 0; i < runs.length; i++) {
      var text = runs[i][0], css = runs[i][1], cls = runs[i][2];
      var esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      if (!css && !cls) { out += esc; continue; }
      out += '<span' + (cls ? ' class="' + cls + '"' : '') +
        (css ? ' style="' + css.replace(/"/g, '') + '"' : '') + '>' + esc + '</span>';
    }
    return out;
  }

  function paintHistory(h) {
    if (!h) return;
    if (h.from > histCount && histCount === 0 && h.dropped > 0) {
      var mark = document.createElement('div');
      mark.className = 'tline d';
      /* THE GAP IS PRINTED, NEVER HIDDEN. The server's scrollback is a bounded deque; a pane that
       * silently dropped its head would be a transcript that lies about itself. */
      mark.textContent = h.dropped + ' earlier line(s) scrolled out of this pane\'s buffer ' +
        'and are not recoverable here.';
      hist.appendChild(mark);
    }
    for (var i = 0; i < h.lines.length; i++) {
      var d = document.createElement('div');
      d.className = 'tline';
      d.innerHTML = runsToHTML(h.lines[i]) || '&nbsp;';
      hist.appendChild(d);
    }
    histCount = h.total;
  }

  function paintScreen(s) {
    if (placeholder) { placeholder.remove(); placeholder = null; }
    var lines = s.lines;
    /* Grow or shrink the DOM to the row count first, so the diff below compares like with like. */
    while (scr.children.length > lines.length) scr.removeChild(scr.lastChild);
    while (scr.children.length < lines.length) {
      var d = document.createElement('div');
      d.className = 'tline';
      scr.appendChild(d);
    }
    lineCache.length = lines.length;
    for (var i = 0; i < lines.length; i++) {
      var html = runsToHTML(lines[i]) || '&nbsp;';
      if (lineCache[i] === html) continue;      /* untouched line: not re-parsed, not repainted */
      lineCache[i] = html;
      scr.children[i].innerHTML = html;
    }
    var m = metrics();
    if (s.cursor && s.cursor.visible) {
      cursor.hidden = false;
      cursor.style.left = (scr.offsetLeft + s.cursor.x * m.cw) + 'px';
      cursor.style.top = (scr.offsetTop + s.cursor.y * m.lh) + 'px';
      cursor.style.width = m.cw + 'px';
      cursor.style.height = m.lh + 'px';
    } else {
      cursor.hidden = true;
    }
    mode.app_cursor = !!s.app_cursor;
    mode.bracketed = !!s.bracketed_paste;
    sizeEl.textContent = s.cols + '×' + s.rows + (s.alt ? ' · alt screen' : '');
    document.title = (s.title || s.label || 'Terminal') + ' · runtime console';
  }

  function atBottom() {
    return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 40;
  }

  function apply(s) {
    var stick = atBottom();
    paintHistory(s.history);
    paintScreen(s);
    mode.alive = !!s.alive;
    rev = s.rev;
    closeBtn.disabled = false;
    status.textContent = s.alive
      ? (s.label + ' · pid ' + s.pid)
      : (s.label + ' · exited ' + s.exit_code);
    footEl.textContent = s.cwd + '  ·  ' + s.argv.join(' ') +
      '  ·  ' + s.bytes_in + 'B in / ' + s.bytes_out + 'B out';
    if (stick) scroll.scrollTop = scroll.scrollHeight;
    renderTabs();
  }

  function renderTabs() {
    tabs.innerHTML = '';
    known.forEach(function (t) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'ttab';
      b.setAttribute('aria-selected', t.key === key ? 'true' : 'false');
      b.textContent = t.label;
      if (t.dead) {
        var s = document.createElement('span');
        s.className = 'tdead';
        s.textContent = ' · exited';
        b.appendChild(s);
      }
      b.onclick = function () { attach(t.key); };
      tabs.appendChild(b);
    });
  }

  /* ------------------------------------------------------------------ the poll loop */

  function attach(k) {
    key = k;
    /* PUBLISHED ON PURPOSE, and it grants nothing: the key is useless without the room-scoped
     * CSRF token and the session cookie the server binds it to, and any script already running on
     * this origin could ask `/terminal` for the same list. What it buys is a pane that can be
     * measured from outside itself, which is how the round-trip numbers in
     * `web/tests/test_terminal_human_path.py` are taken from the browser rather than the server. */
    window.__tkey = k;
    rev = -1;
    histCount = 0;
    lineCache = [];
    hist.innerHTML = '';
    show('');
    polling += 1;
    loop(polling);
  }

  function loop(gen) {
    if (gen !== polling || !key) return;
    /* A hidden tab still polls, but slowly and with a long wait: the point is that a shell that
     * finishes while the operator is in another tab is finished when he comes back, not that the
     * pane is repainted at a screen nobody is looking at. */
    var wait = document.hidden ? 25 : 8;
    var url = '/terminal/' + key + '/screen?rev=' + rev + '&have=' + histCount + '&wait=' + wait;
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 404) throw new Error('this session is gone. Open a new one.');
        return r.json();
      })
      .then(function (j) {
        if (gen !== polling) return;
        if (!j.ok) throw new Error(j.error || 'the pane was refused');
        if (j.changed) apply(j.screen);
        else if (j.alive === false) markDead(j.exit_code);
        setTimeout(function () { loop(gen); }, 0);
      })
      .catch(function (e) {
        if (gen !== polling) return;
        show(String(e.message || e));
        /* A backoff rather than a stop: the console restarting under a pane is a normal event on
         * this host, and a client that gave up would need a reload to notice it came back. */
        setTimeout(function () { loop(gen); }, 2000);
      });
  }

  function markDead(code) {
    mode.alive = false;
    status.textContent = 'exited ' + code;
    known.forEach(function (t) { if (t.key === key) t.dead = true; });
    renderTabs();
  }

  /* ------------------------------------------------------------------ opening */

  function open(kind, task, engine) {
    var sz = fitSize();
    show('');
    post('/terminal/open', { kind: kind, task: task || '', engine: engine || 'claude',
                             rows: sz.rows, cols: sz.cols })
      .then(function (j) {
        if (!j.ok) { show(j.error || 'refused'); return; }
        known.push({ key: j.key, label: j.label, dead: false });
        attach(j.key);
        scr.focus();
      })
      .catch(function (e) { show(String(e)); });
  }

  document.getElementById('tnew').onclick = function () { open('shell'); };
  ['thelp', 'thelpx'].forEach(function (id) {
    document.getElementById(id).onclick = function () {
      var t = (document.getElementById('ttask').value || '').trim();
      if (!/^[0-9]{1,8}$/.test(t)) { show('a helper opens on a task ROW. Type its id, e.g. 0437.'); return; }
      open('helper', t, this.getAttribute('data-engine'));
    };
  });
  /* ARMED ONLY NOW, and the template shipped them disabled. A click landing between the markup
   * painting and this line used to do nothing at all: no request, no message, no visible change.
   * Measured 2026-08-29 with a real browser driving a real click, and it cost that run its whole
   * session. A control is either live or visibly not, never live-looking and inert. */
  ['tnew', 'thelp', 'thelpx'].forEach(function (id) {
    document.getElementById(id).disabled = false;
  });
  closeBtn.onclick = function () {
    if (!key) return;
    post('/terminal/' + key + '/close', {}).then(function () { markDead('closed'); });
  };

  /* ------------------------------------------------------------------ the keyboard
   *
   * Every key that is not for the browser goes to the pty, byte for byte. The list below is
   * xterm's, not an approximation of it: a shell sent `ESC [ A` while it is in DECCKM prints a
   * literal `^[OA` into the line being edited, so the mode the SERVER parsed decides the
   * spelling, never a guess made here. */

  /* ONE POST IN FLIGHT AT A TIME, AND THE REST OF THE TYPING WAITS IN A BUFFER.
   *
   * THE DEFECT THIS REPAIRS, measured 2026-08-29 end to end and not reasoned about. The pane
   * used to fire one independent `fetch` per keystroke. `fetch` gives no ordering guarantee
   * whatsoever: the browser opens several connections to one origin and whichever request the
   * server picks up first is the one the pty receives first. Typed into a real Claude Code
   * helper from a real browser, the sentence
   *     "Do not read, write or edit any file, and run no command."
   * arrived at the process, and was stored in `brain.session.stated_goal`, as
   *     "ot read, write or edit any file,a nd run no command."
   * Four characters lost at the head and two adjacent characters SWAPPED in the middle. A
   * terminal that silently transposes what you type is not a terminal, and it is the one defect
   * on this surface that no server-side test could ever see: every individual write was correct.
   *
   * COALESCING IS THE SIDE EFFECT, NOT THE POINT. Ten keys typed during one in-flight request
   * leave as one POST rather than ten, so fast typing gets FEWER round trips than slow typing
   * rather than more, which is the opposite of how the old shape degraded.
   *
   * AT LEAST ONCE, NOT AT MOST ONCE, and that is a deliberate choice between two bad failures.
   * A `fetch` that REJECTS on loopback means the request almost certainly never landed, so the
   * buffer is put back and retried. The cost is that a request which landed and lost only its
   * response would repeat a keystroke. The alternative is dropping input silently, which is the
   * defect above wearing a different hat. */
  var outbox = '';
  var sending = false;

  function send(data) {
    if (!key || !data) return;
    outbox += data;
    flush();
  }

  /* A CARRIAGE RETURN IS NEVER MERGED WITH ANYTHING AND IS ALWAYS SENT ALONE.
   *
   * Coalescing fixed the ordering defect and immediately created a smaller one: a full-screen TUI
   * decides whether a run of bytes is TYPING or a PASTE, and `hello\r` arriving as one write is a
   * paste, which puts a newline in the input box instead of submitting it. Measured 2026-08-29
   * against a real Claude Code helper: a whole line written to the pty in one `os.write` produced
   * NO `UserPromptSubmit` at all, while the same line typed key by key submitted in 2.5 s.
   * Splitting on the return keeps every Enter a single-byte write, exactly as a keypress is, and
   * costs one extra round trip per line at about 5 to 14 ms. */
  function nextChunk() {
    var i = outbox.indexOf('\r');
    var out;
    if (i === 0) { out = outbox.slice(0, 1); outbox = outbox.slice(1); return out; }
    if (i > 0) { out = outbox.slice(0, i); outbox = outbox.slice(i); return out; }
    out = outbox;
    outbox = '';
    return out;
  }

  function flush() {
    if (sending || !outbox || !key) return;
    var payload = nextChunk();
    sending = true;
    post('/terminal/' + key + '/input', { data: payload, have: histCount })
      .then(function (j) {
        sending = false;
        if (!j.ok) { show(j.error || 'refused'); return; }
        show('');
        if (j.screen) apply(j.screen);
        flush();
      })
      .catch(function (e) {
        /* Never landed: put it back at the FRONT, ahead of anything typed since. */
        outbox = payload + outbox;
        sending = false;
        show(String(e));
        setTimeout(flush, 300);
      });
  }

  var FKEYS = { F1: 'P', F2: 'Q', F3: 'R', F4: 'S' };
  var TILDE = { F5: '15', F6: '17', F7: '18', F8: '19', F9: '20', F10: '21', F11: '23', F12: '24',
                Insert: '2', Delete: '3', PageUp: '5', PageDown: '6' };
  var ARROW = { ArrowUp: 'A', ArrowDown: 'B', ArrowRight: 'C', ArrowLeft: 'D',
                Home: 'H', End: 'F' };

  function encode(e) {
    var k = e.key;
    if (ARROW[k]) {
      var mod = (e.ctrlKey ? 4 : 0) + (e.altKey ? 2 : 0) + (e.shiftKey ? 1 : 0);
      if (mod) return '\x1b[1;' + (mod + 1) + ARROW[k];
      return (mode.app_cursor ? '\x1bO' : '\x1b[') + ARROW[k];
    }
    if (FKEYS[k]) return '\x1bO' + FKEYS[k];
    if (TILDE[k]) return '\x1b[' + TILDE[k] + '~';
    if (k === 'Enter') return '\r';
    if (k === 'Tab') return e.shiftKey ? '\x1b[Z' : '\t';
    if (k === 'Backspace') return e.ctrlKey ? '\x08' : '\x7f';
    if (k === 'Escape') return '\x1b';
    if (e.ctrlKey && !e.altKey && k.length === 1) {
      var c = k.toUpperCase().charCodeAt(0);
      if (c >= 64 && c < 96) return String.fromCharCode(c - 64);   /* Ctrl-A..Ctrl-_ */
      if (k === ' ') return '\x00';
      if (k === '?') return '\x7f';
    }
    if (e.altKey && !e.ctrlKey && k.length === 1) return '\x1b' + k;
    if (k.length === 1 && !e.ctrlKey && !e.metaKey) return k;
    return null;
  }

  scr.addEventListener('keydown', function (e) {
    if (e.metaKey) return;                       /* the OS's, not ours */
    /* THREE ESCAPE HATCHES LEFT TO THE BROWSER ON PURPOSE, because a pane that swallows them is
     * a pane the operator cannot get out of: copy with a selection, and the Shift forms of copy
     * and paste. Ctrl-C with NOTHING selected is the interrupt, which is the common case. */
    if (e.ctrlKey && e.shiftKey && (e.key === 'C' || e.key === 'V' || e.key === 'c' || e.key === 'v')) return;
    if (e.ctrlKey && (e.key === 'c' || e.key === 'C') && String(window.getSelection()) !== '') return;
    var data = encode(e);
    if (data === null) return;
    e.preventDefault();
    e.stopPropagation();
    if (!key) { show('nothing is running in this pane. Open a shell first.'); return; }
    send(data);
  });

  scr.addEventListener('paste', function (e) {
    e.preventDefault();
    var text = (e.clipboardData || window.clipboardData).getData('text');
    if (!text) return;
    /* Bracketed paste when the program asked for it (`?2004`), raw when it did not. A multi-line
     * paste into a shell that never enabled it is a shell that runs every line, which is the
     * operator's own risk to take and not this pane's to silently change. */
    send(mode.bracketed ? '\x1b[200~' + text + '\x1b[201~' : text);
  });

  scroll.addEventListener('mousedown', function (e) {
    if (e.target === scroll || e.target === scr || scr.contains(e.target)) {
      setTimeout(function () { if (String(window.getSelection()) === '') scr.focus(); }, 0);
    }
  });

  /* ------------------------------------------------------------------ what a reload finds
   *
   * THE PANES THIS BROWSER ALREADY HAS OPEN, seeded from the server's own list rather than from
   * anything remembered here. Measured 2026-08-29 in a real browser before this block existed:
   * open a pane, press reload, and the page rendered 0 tabs and its placeholder while the pty was
   * still alive with a process in it. The pane was unreachable because the only copy of its key
   * was the variable the reload had just discarded.
   *
   * ATTACH TO THE NEWEST LIVE ONE, and to nothing when none is live. Reattaching to an exited
   * pane would repaint a dead screen over the placeholder that says how to start a live one. */
  (function restore() {
    var boot = window.__TERMINAL_SESSIONS || [];
    if (!boot.length) return;
    var newest = null;
    boot.forEach(function (s) {
      known.push({ key: s.key, label: s.label, dead: !s.alive });
      if (s.alive) newest = s;
    });
    renderTabs();
    if (!newest) return;
    attach(newest.key);
    /* The size is pushed AFTER the attach, because this browser window may not be the one that
     * opened the pane and the pty is still holding the geometry the other one asked for. */
    setTimeout(pushSize, 0);
    scr.focus();
  })();

  /* ------------------------------------------------------------------ resize */
  var resizeTimer = null;
  function pushSize() {
    if (!key) return;
    var sz = fitSize();
    post('/terminal/' + key + '/resize', sz).catch(function () {});
  }
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(pushSize, 200);
  });
})();
