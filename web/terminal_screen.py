"""A terminal screen, in Python, with no I/O in it at all.

WHY THE EMULATOR IS ON THE SERVER AND NOT IN THE BROWSER. `web/MUST-NOT-BUILD.md` item 11 was
overruled on 2026-08-28 for a typable pane, which PERMITS a websocket; it does not require one.
Measured on this host before the design was chosen: there is no websocket library here at all --
`flask_sock`, `simple_websocket`, `wsproto`, `gevent`, `eventlet` and `ptyprocess` are all absent,
and this repo has no `requirements.txt` and no `pyproject.toml`, so a new pip dependency has
nowhere to be declared and `web/bin/console` would stop booting on any host that lacked it.

That is the cheap reason. The load-bearing one is that **a screen is idempotent and a byte stream
is not.** A client that misses one poll of a screen is one frame behind and then correct; a client
that misses one frame of a raw PTY stream is corrupt until the next full repaint, which a TUI on
the alternate screen may never send. The screen also makes the transport free: the pane can be
polled by the same plain `fetch` the rest of this console uses, so `pw_prohibitions.py` still
measures `EventSource` and `WebSocket` NEVER CONSTRUCTED, and the overrule stays unspent.

WHAT THIS SUPPORTS, stated so a reader knows what it does not. Enough of DEC VT100/VT220 plus
xterm's common extensions to run a shell and a full-screen TUI: SGR including 256-colour and
truecolour, cursor movement and addressing, erase and delete, insert/delete line and character,
scrolling regions, the alternate screen (`?1049`), autowrap with DEFERRED wrap, bracketed paste
(`?2004`), cursor visibility (`?25`), device status reports, and OSC 0/2 titles. Mouse tracking
modes are PARSED AND DISCARDED on purpose: a pane that reports mouse mode ON and never sends a
mouse event makes a TUI wait for input that will never arrive.

Sixel, ReGIS, DCS payloads and NRCS character sets are consumed and dropped. Nothing here writes
to a file, opens a socket or calls a verb.
"""

from __future__ import annotations

import codecs
from collections import deque

# ---------------------------------------------------------------------------- attributes

#: Flag bits carried beside the two colours. Packed rather than named-per-cell because a cell is
#: allocated `rows * cols` times and compared on every render to build the runs.
BOLD, DIM, ITALIC, UNDER, BLINK, REVERSE, HIDDEN, STRIKE = (1, 2, 4, 8, 16, 32, 64, 128)

_FLAG_CLASS = ((BOLD, "b"), (DIM, "d"), (ITALIC, "i"), (UNDER, "u"),
               (BLINK, "k"), (REVERSE, "r"), (HIDDEN, "h"), (STRIKE, "s"))

#: (fg, bg, flags). `None` means "the surface's default", which is the theme's ink and paper --
#: NOT white on black. A pane that hardcodes its own default is a pane that ignores the light
#: theme this console ships.
DEFAULT_ATTR = (None, None, 0)

#: The xterm 256-colour cube, resolved SERVER SIDE into a hex string, because the client should
#: never have to carry a palette to know what `38;5;173` was.
def _cube() -> list[str]:
    steps = (0, 95, 135, 175, 215, 255)
    out = []
    for r in range(6):
        for g in range(6):
            for b in range(6):
                out.append(f"#{steps[r]:02x}{steps[g]:02x}{steps[b]:02x}")
    return out


_CUBE = _cube()
_GREYS = [f"#{v:02x}{v:02x}{v:02x}" for v in range(8, 239, 10)]


def colour_css(spec) -> str:
    """One colour -> one CSS value. The 16 ANSI slots stay THEMEABLE, the rest are literal.

    `0..15` become `var(--t0)`..`var(--t15)`, which `web/static/terminal.css` binds per theme, so
    a red from `ls` is the theme's red in light mode and in dark mode. Anything above 15 was asked
    for by number and is resolved to that exact number's hex; a theme cannot honestly reinterpret
    `38;2;120;200;40`.
    """
    if spec is None:
        return ""
    if isinstance(spec, tuple):
        r, g, b = spec
        return f"#{r:02x}{g:02x}{b:02x}"
    if spec < 16:
        return f"var(--t{spec})"
    if spec < 232:
        return _CUBE[spec - 16]
    return _GREYS[min(spec - 232, len(_GREYS) - 1)]


class Screen:
    """`rows` x `cols` of cells, plus the scrollback the primary screen pushes off the top."""

    def __init__(self, rows: int = 30, cols: int = 100, scrollback: int = 2000):
        self.rows = max(1, rows)
        self.cols = max(2, cols)
        self.title = ""
        self.bracketed_paste = False
        self.cursor_visible = True
        self.app_cursor_keys = False
        self.app_keypad = False
        #: Bytes the emulator OWES the program: cursor reports, device attributes. `terminal.py`
        #: drains this into the pty. A DSR that is never answered is a shell that never prompts.
        self.pending_response = b""
        self.scrollback: deque = deque(maxlen=max(0, scrollback))
        #: Every line ever pushed off the top, counted rather than inferred from the deque, so a
        #: client can tell "nothing scrolled" from "2000 lines scrolled and the deque is full".
        self.scrolled_total = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._reset(hard=True)

    # ------------------------------------------------------------------ buffers and state

    def _blank_line(self) -> list:
        return [(" ", DEFAULT_ATTR)] * self.cols

    def _blank(self) -> list:
        return [self._blank_line() for _ in range(self.rows)]

    def _reset(self, hard: bool = False) -> None:
        self.buf = self._blank()
        self.alt_buf = None
        self.x = 0
        self.y = 0
        self.attr = DEFAULT_ATTR
        self.top = 0
        self.bottom = self.rows - 1
        self.autowrap = True
        self._pending_wrap = False
        self._saved = None
        self._alt_saved = None
        self.state = "ground"
        self._params = ""
        self._inter = ""
        self._osc = ""
        if hard:
            self.title = ""
            self.cursor_visible = True
            self.bracketed_paste = False
            self.app_cursor_keys = False
            self.app_keypad = False

    @property
    def on_alt(self) -> bool:
        return self.alt_buf is not None

    def resize(self, rows: int, cols: int) -> None:
        """Reflow by TRUNCATE AND PAD, never by rewrapping.

        Rewrapping a screen a TUI drew is not a service to it: the program is about to redraw on
        SIGWINCH anyway, and a guess at where its lines wanted to break is a guess that shows.
        """
        rows = max(1, min(200, int(rows)))
        cols = max(20, min(400, int(cols)))
        if rows == self.rows and cols == self.cols:
            return

        def fit(buf):
            out = []
            for line in buf[-rows:]:
                line = list(line[:cols])
                line += [(" ", DEFAULT_ATTR)] * (cols - len(line))
                out.append(line)
            while len(out) < rows:
                out.append([(" ", DEFAULT_ATTR)] * cols)
            return out

        dropped = max(0, len(self.buf) - rows)
        if not self.on_alt:
            for line in self.buf[:dropped]:
                self._push_scrollback(line)
        self.buf = fit(self.buf)
        if self.alt_buf is not None:
            self.alt_buf = fit(self.alt_buf)
        self.rows, self.cols = rows, cols
        self.top, self.bottom = 0, rows - 1
        self.x = min(self.x, cols - 1)
        self.y = min(self.y, rows - 1)
        self._pending_wrap = False

    def _push_scrollback(self, line) -> None:
        self.scrollback.append(line)
        self.scrolled_total += 1

    # ------------------------------------------------------------------ the feed

    def feed(self, data: bytes) -> None:
        """Push raw pty output through. Partial UTF-8 at the tail is HELD, never replaced.

        The incremental decoder is the whole reason this is safe to call on arbitrary read()
        boundaries: a 3-byte glyph split across two reads renders as one glyph, not as two
        replacement characters that never repair.
        """
        for ch in self._decoder.decode(data):
            self._char(ch)

    def _char(self, ch: str) -> None:
        st = self.state
        if st == "ground":
            self._ground(ch)
        elif st == "esc":
            self._esc(ch)
        elif st == "csi":
            self._csi(ch)
        elif st == "osc":
            self._osc_char(ch)
        elif st == "dcs":
            # Consumed and dropped, to the string terminator. Sixel and ReGIS land here.
            if ch == "\x07" or (ch == "\\" and self._last_was_esc):
                self.state = "ground"
                self._last_was_esc = False
            else:
                self._last_was_esc = (ch == "\x1b")
        elif st == "charset":
            self.state = "ground"

    def _ground(self, ch: str) -> None:
        o = ord(ch)
        if o == 0x1B:
            self.state = "esc"
            self._params = self._inter = ""
            return
        if o == 0x0D:
            self.x = 0
            self._pending_wrap = False
            return
        if o == 0x0A or o == 0x0B or o == 0x0C:
            self._linefeed()
            return
        if o == 0x08:
            if self._pending_wrap:
                self._pending_wrap = False
            elif self.x > 0:
                self.x -= 1
            return
        if o == 0x09:
            self.x = min(self.cols - 1, ((self.x // 8) + 1) * 8)
            self._pending_wrap = False
            return
        if o == 0x07 or o < 0x20 or o == 0x7F:
            return                                  # BEL and the rest of C0: no bell, no beep
        self._put(ch)

    def _put(self, ch: str) -> None:
        if self._pending_wrap and self.autowrap:
            self.x = 0
            self._linefeed()
            self._pending_wrap = False
        line = self.buf[self.y]
        if self.x < self.cols:
            line[self.x] = (ch, self.attr)
        if self.x + 1 >= self.cols:
            # DEFERRED WRAP, and it is not a nicety. A character written INTO the last column
            # leaves the cursor there rather than on the next line: a shell that then prints a
            # backspace, or a TUI that addresses the cursor, must not have already lost a line.
            self._pending_wrap = True
        else:
            self.x += 1

    def _linefeed(self) -> None:
        self._pending_wrap = False
        if self.y == self.bottom:
            self._scroll_up(1)
        elif self.y < self.rows - 1:
            self.y += 1

    def _scroll_up(self, n: int) -> None:
        for _ in range(n):
            line = self.buf.pop(self.top)
            # Only a line leaving the TOP of a full-height primary screen is history. A line
            # pushed out of a scrolling region, or off the alternate screen, is not: keeping it
            # would interleave a TUI's repaints into the shell's transcript.
            if not self.on_alt and self.top == 0 and self.bottom == self.rows - 1:
                self._push_scrollback(line)
            self.buf.insert(self.bottom, self._blank_line())

    def _scroll_down(self, n: int) -> None:
        for _ in range(n):
            self.buf.pop(self.bottom)
            self.buf.insert(self.top, self._blank_line())

    # ------------------------------------------------------------------ ESC

    def _esc(self, ch: str) -> None:
        if ch == "[":
            self.state = "csi"
            self._params = self._inter = ""
            return
        if ch == "]":
            self.state = "osc"
            self._osc = ""
            return
        if ch == "P" or ch == "X" or ch == "^" or ch == "_":
            self.state = "dcs"
            self._last_was_esc = False
            return
        self.state = "ground"
        if ch in "()*+%":
            self.state = "charset"                  # NRCS designator: consume the next byte
        elif ch == "7":
            self._saved = (self.x, self.y, self.attr, self.autowrap)
        elif ch == "8":
            if self._saved:
                self.x, self.y, self.attr, self.autowrap = self._saved
                self.x = min(self.x, self.cols - 1)
                self.y = min(self.y, self.rows - 1)
        elif ch == "D":
            self._linefeed()
        elif ch == "E":
            self.x = 0
            self._linefeed()
        elif ch == "M":
            if self.y == self.top:
                self._scroll_down(1)
            elif self.y > 0:
                self.y -= 1
        elif ch == "c":
            self._reset(hard=True)
        elif ch == "=":
            self.app_keypad = True
        elif ch == ">":
            self.app_keypad = False

    # ------------------------------------------------------------------ OSC

    def _osc_char(self, ch: str) -> None:
        if ch == "\x07" or (ch == "\\" and self._osc.endswith("\x1b")):
            body = self._osc[:-1] if self._osc.endswith("\x1b") else self._osc
            self.state = "ground"
            self._osc = ""
            head, _, rest = body.partition(";")
            if head in ("0", "2"):
                self.title = rest[:200]
            return
        if len(self._osc) < 4096:
            self._osc += ch

    # ------------------------------------------------------------------ CSI

    def _csi(self, ch: str) -> None:
        o = ord(ch)
        if 0x30 <= o <= 0x3F:
            self._params += ch
            return
        if 0x20 <= o <= 0x2F:
            self._inter += ch
            return
        self.state = "ground"
        priv = self._params.startswith("?")
        raw = self._params[1:] if priv else self._params
        nums = []
        for part in raw.split(";"):
            part = part.strip()
            try:
                nums.append(int(part) if part else 0)
            except ValueError:
                nums.append(0)
        if not nums:
            nums = [0]
        self._dispatch_csi(ch, nums, priv, raw)

    def _arg(self, nums, i, default=1):
        v = nums[i] if i < len(nums) else 0
        return default if v == 0 else v

    def _dispatch_csi(self, ch: str, nums, priv: bool, raw: str) -> None:
        self._pending_wrap = False
        if priv:
            if ch in "hl":
                self._private_mode(nums, ch == "h")
            return
        if self._inter:
            return                                   # DECSCUSR and friends: shape only, ignored

        if ch == "A":
            self.y = max(self.top, self.y - self._arg(nums, 0))
        elif ch == "B" or ch == "e":
            self.y = min(self.bottom, self.y + self._arg(nums, 0))
        elif ch == "C" or ch == "a":
            self.x = min(self.cols - 1, self.x + self._arg(nums, 0))
        elif ch == "D":
            self.x = max(0, self.x - self._arg(nums, 0))
        elif ch == "E":
            self.x = 0
            self.y = min(self.bottom, self.y + self._arg(nums, 0))
        elif ch == "F":
            self.x = 0
            self.y = max(self.top, self.y - self._arg(nums, 0))
        elif ch == "G" or ch == "`":
            self.x = min(self.cols - 1, self._arg(nums, 0) - 1)
        elif ch == "d":
            self.y = min(self.rows - 1, self._arg(nums, 0) - 1)
        elif ch == "H" or ch == "f":
            self.y = min(self.rows - 1, self._arg(nums, 0) - 1)
            self.x = min(self.cols - 1, self._arg(nums, 1) - 1)
        elif ch == "J":
            self._erase_display(nums[0] if nums else 0)
        elif ch == "K":
            self._erase_line(nums[0] if nums else 0)
        elif ch == "L":
            self._insert_lines(self._arg(nums, 0))
        elif ch == "M":
            self._delete_lines(self._arg(nums, 0))
        elif ch == "P":
            self._delete_chars(self._arg(nums, 0))
        elif ch == "@":
            self._insert_chars(self._arg(nums, 0))
        elif ch == "X":
            n = self._arg(nums, 0)
            line = self.buf[self.y]
            for i in range(self.x, min(self.cols, self.x + n)):
                line[i] = (" ", self.attr)
        elif ch == "S":
            self._scroll_up(self._arg(nums, 0))
        elif ch == "T":
            self._scroll_down(self._arg(nums, 0))
        elif ch == "m":
            self._sgr(nums if raw else [0])
        elif ch == "r":
            top = self._arg(nums, 0) - 1
            bottom = (self._arg(nums, 1, self.rows)) - 1
            if 0 <= top < bottom < self.rows:
                self.top, self.bottom = top, bottom
                self.x = self.y = 0
        elif ch == "n":
            if nums and nums[0] == 6:
                self.pending_response += f"\x1b[{self.y + 1};{self.x + 1}R".encode()
            elif nums and nums[0] == 5:
                self.pending_response += b"\x1b[0n"
        elif ch == "c":
            self.pending_response += b"\x1b[?6c"     # "I am a VT102", the safe minimum
        elif ch == "s":
            self._saved = (self.x, self.y, self.attr, self.autowrap)
        elif ch == "u":
            if self._saved:
                self.x, self.y, self.attr, self.autowrap = self._saved

    def _private_mode(self, nums, on: bool) -> None:
        for n in nums:
            if n == 1:
                self.app_cursor_keys = on
            elif n == 7:
                self.autowrap = on
            elif n == 25:
                self.cursor_visible = on
            elif n == 2004:
                self.bracketed_paste = on
            elif n in (47, 1047, 1049):
                self._alt_screen(on, save_cursor=(n == 1049))
            # 1000/1002/1003/1006/1015: mouse tracking. PARSED AND DROPPED, see the module
            # docstring: accepting the mode without ever sending an event is the honest half.

    def _alt_screen(self, on: bool, save_cursor: bool) -> None:
        if on:
            if self.alt_buf is None:
                if save_cursor:
                    self._alt_saved = (self.x, self.y, self.attr)
                self.alt_buf = self.buf
                self.buf = self._blank()
                self.x = self.y = 0
                self.attr = DEFAULT_ATTR
                self.top, self.bottom = 0, self.rows - 1
        else:
            if self.alt_buf is not None:
                self.buf = self.alt_buf
                self.alt_buf = None
                self.top, self.bottom = 0, self.rows - 1
                if self._alt_saved:
                    self.x, self.y, self.attr = self._alt_saved
                    self.x = min(self.x, self.cols - 1)
                    self.y = min(self.y, self.rows - 1)

    def _erase_display(self, mode: int) -> None:
        blank = (" ", self.attr)
        if mode == 0:
            for i in range(self.x, self.cols):
                self.buf[self.y][i] = blank
            for r in range(self.y + 1, self.rows):
                self.buf[r] = [blank] * self.cols
        elif mode == 1:
            for i in range(0, min(self.x + 1, self.cols)):
                self.buf[self.y][i] = blank
            for r in range(0, self.y):
                self.buf[r] = [blank] * self.cols
        else:
            # `ED 3` clears the scrollback too, which is what `clear` sends. `ED 2` does not:
            # a screen clear that ate the transcript would be a shell you cannot scroll back in.
            if mode == 3:
                self.scrollback.clear()
            self.buf = [[blank] * self.cols for _ in range(self.rows)]

    def _erase_line(self, mode: int) -> None:
        blank = (" ", self.attr)
        line = self.buf[self.y]
        if mode == 0:
            for i in range(self.x, self.cols):
                line[i] = blank
        elif mode == 1:
            for i in range(0, min(self.x + 1, self.cols)):
                line[i] = blank
        else:
            self.buf[self.y] = [blank] * self.cols

    def _insert_lines(self, n: int) -> None:
        if not (self.top <= self.y <= self.bottom):
            return
        for _ in range(min(n, self.bottom - self.y + 1)):
            self.buf.pop(self.bottom)
            self.buf.insert(self.y, self._blank_line())

    def _delete_lines(self, n: int) -> None:
        if not (self.top <= self.y <= self.bottom):
            return
        for _ in range(min(n, self.bottom - self.y + 1)):
            self.buf.pop(self.y)
            self.buf.insert(self.bottom, self._blank_line())

    def _delete_chars(self, n: int) -> None:
        line = self.buf[self.y]
        for _ in range(min(n, self.cols - self.x)):
            line.pop(self.x)
            line.append((" ", self.attr))

    def _insert_chars(self, n: int) -> None:
        line = self.buf[self.y]
        for _ in range(min(n, self.cols - self.x)):
            line.insert(self.x, (" ", self.attr))
            line.pop()

    def _sgr(self, nums) -> None:
        fg, bg, flags = self.attr
        i = 0
        while i < len(nums):
            n = nums[i]
            if n == 0:
                fg, bg, flags = DEFAULT_ATTR
            elif n == 1:
                flags |= BOLD
            elif n == 2:
                flags |= DIM
            elif n == 3:
                flags |= ITALIC
            elif n == 4:
                flags |= UNDER
            elif n in (5, 6):
                flags |= BLINK
            elif n == 7:
                flags |= REVERSE
            elif n == 8:
                flags |= HIDDEN
            elif n == 9:
                flags |= STRIKE
            elif n == 21 or n == 22:
                flags &= ~(BOLD | DIM)
            elif n == 23:
                flags &= ~ITALIC
            elif n == 24:
                flags &= ~UNDER
            elif n == 25:
                flags &= ~BLINK
            elif n == 27:
                flags &= ~REVERSE
            elif n == 28:
                flags &= ~HIDDEN
            elif n == 29:
                flags &= ~STRIKE
            elif 30 <= n <= 37:
                fg = n - 30
            elif 40 <= n <= 47:
                bg = n - 40
            elif 90 <= n <= 97:
                fg = n - 90 + 8
            elif 100 <= n <= 107:
                bg = n - 100 + 8
            elif n == 39:
                fg = None
            elif n == 49:
                bg = None
            elif n in (38, 48):
                # `38;5;n` and `38;2;r;g;b`. The colon form (`38:2::r:g:b`) arrives as one
                # unparsed parameter and is dropped rather than misread as a colour 0.
                if i + 1 < len(nums) and nums[i + 1] == 5 and i + 2 < len(nums):
                    val = max(0, min(255, nums[i + 2]))
                    if n == 38:
                        fg = val
                    else:
                        bg = val
                    i += 2
                elif i + 1 < len(nums) and nums[i + 1] == 2 and i + 4 < len(nums):
                    rgb = tuple(max(0, min(255, nums[i + 2 + k])) for k in range(3))
                    if n == 38:
                        fg = rgb
                    else:
                        bg = rgb
                    i += 4
            i += 1
        self.attr = (fg, bg, flags)

    # ------------------------------------------------------------------ render

    @staticmethod
    def _runs(line) -> list:
        """One line -> `[text, css, classes]` runs, trailing blanks dropped.

        Dropping the trailing default-attribute blanks is most of the payload: a 100-column
        screen holding a two-word prompt is two runs and not a hundred cells.
        """
        end = len(line)
        while end > 0 and line[end - 1][0] == " " and line[end - 1][1] == DEFAULT_ATTR:
            end -= 1
        out = []
        cur_attr = None
        cur = []
        for i in range(end):
            ch, attr = line[i]
            if attr != cur_attr:
                if cur:
                    out.append(_run(cur, cur_attr))
                cur = []
                cur_attr = attr
            cur.append(ch)
        if cur:
            out.append(_run(cur, cur_attr))
        return out

    def render(self) -> dict:
        """The whole visible screen, plus where the cursor is. No history here: `history()` has it."""
        return {
            "rows": self.rows,
            "cols": self.cols,
            "lines": [self._runs(line) for line in self.buf],
            "cursor": {"x": self.x, "y": self.y, "visible": bool(self.cursor_visible)},
            "alt": self.on_alt,
            "title": self.title,
            "bracketed_paste": self.bracketed_paste,
            # The client needs BOTH of these to encode a key correctly. An arrow key is
            # `ESC O A` under DECCKM and `ESC [ A` without it, and a shell in one mode sent the
            # other spelling prints a literal `^[OA` into the line the operator is editing.
            "app_cursor": self.app_cursor_keys,
            "app_keypad": self.app_keypad,
        }

    def history(self, have: int) -> dict:
        """Scrollback the client does not have yet, and an honest count of what was DROPPED.

        `have` is how many lines the client already holds. `dropped` is how many fell out of the
        bounded deque before it ever asked, which is a number the pane prints rather than a gap it
        hides: a scrollback that silently loses its head is a transcript that lies about itself.
        """
        total = self.scrolled_total
        kept = len(self.scrollback)
        first_kept = total - kept
        dropped = max(0, first_kept)
        start = max(have, first_kept)
        lines = [self._runs(self.scrollback[i - first_kept]) for i in range(start, total)]
        return {"from": start, "total": total, "dropped": dropped, "lines": lines}


def _run(chars: list, attr) -> list:
    """One run of same-attribute characters -> `[text, inline-css, class-string]`.

    REVERSE IS RESOLVED HERE, not in CSS, and the two defaults are the reason. `\x1b[7m` on a run
    that names no colours has to become the theme's ink on the theme's paper, and a client-side
    `filter:invert()` would also invert the 256-colour cells beside it. Swapping the two values --
    substituting `var(--tfg)`/`var(--tbg)` where a side was default -- is the only version that is
    correct for both cases.
    """
    fg, bg, flags = attr
    if flags & REVERSE:
        fg, bg = bg, fg
        fg_css = colour_css(fg) if fg is not None else "var(--tbg)"
        bg_css = colour_css(bg) if bg is not None else "var(--tfg)"
    else:
        fg_css = colour_css(fg) if fg is not None else ""
        bg_css = colour_css(bg) if bg is not None else ""
    css = []
    if fg_css:
        css.append("color:" + fg_css)
    if bg_css:
        css.append("background:" + bg_css)
    cls = "".join(c for bit, c in _FLAG_CLASS if (flags & bit) and c != "r")
    return ["".join(chars), ";".join(css), cls]
