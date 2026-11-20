"""Terminal output tiers, symbols, colors, and links for command-line scripts.

Three tiers, decided once per stream (TIER for stdout, ERR_TIER for stderr), because a run
is often half piped: 0 is plain ASCII, 1 is unicode plus color, 2 adds Nerd Font glyphs
and only with NERD_FONT=1. Tier 0 whenever the stream is not a terminal or any of
NO_COLOR, CI, or TERM=dumb is set (an unset TERM is not dumb: Windows leaves it unset;
CI=false and CI=0 are not CI), and whenever the stream's encoding cannot print the
symbols. Importing this module also switches such a stdout/stderr to
errors='backslashreplace', since the caller's own text (a title with an em dash) would
otherwise still crash the run one print() later; it prints as \u2014 instead.

table() and progress() cover the two layouts scripts keep needing, and stop there: a
piped table is plain records and a piped progress bar is nothing at all.

SYMBOLS and COLORS hold the general states; a script with states of its own adds them
with SYMBOLS.update()/COLORS.update(), so mark() and paint() know them too. A state needs
a symbol to be marked but not a color: mark() leaves an uncolored one plain. Both are
module-level and shared by every importer in the process.

The tiers are module globals rather than arguments so a script's own selftest can pin
them (`term_styling.TIER = 0`) and every function here follows; read them as
`term_styling.TIER`, not `from term_styling import TIER`, or the pin is invisible.
Stdlib only, so importing it adds nothing to a script's dependencies.
"""

from __future__ import annotations

import functools
import io
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from typing import NoReturn, TextIO, TypeVar

_T = TypeVar('_T')

# A literal, not importlib.metadata, so a vendored copy carries its own version;
# tests/ checks it against pyproject.toml.
__version__ = '0.3.1'
# Tier-1 characters the base SYMBOLS, divider(), and table() print. A stream whose
# encoding cannot hold them (a Latin-1 locale, PYTHONIOENCODING=ascii) gets tier 0. Not
# every one: only Unicode encodings hold these eight, and those hold the rest too, which
# tests/ checks.
_PROBE = '✓✗⚠→—✎≡─'


def _encodes(stream: object) -> bool:
    """Whether `stream` can print the tier-1 symbols."""
    encoding = getattr(stream, 'encoding', None)
    if (
        not encoding
    ):  # None for an in-memory text stream, which holds str and cannot fail
        return True
    try:
        _PROBE.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _tolerate(stream: object) -> None:
    """Make a stream that cannot print the symbols escape what it cannot encode.

    Tier 0 keeps this module's own output ASCII, but not the caller's text, so without
    this a Latin-1 terminal or a cp1252 pipe still dies on the first non-ASCII title.
    A stream whose errors handler someone already chose is left alone.
    """
    if _encodes(stream) or getattr(stream, 'errors', None) != 'strict':
        return
    try:
        stream.reconfigure(errors='backslashreplace')  # ty: ignore[unresolved-attribute]
    except (AttributeError, ValueError, io.UnsupportedOperation):
        pass


# ENABLE_VIRTUAL_TERMINAL_PROCESSING: the console output mode that makes a Windows
# console read escapes instead of printing them.
_VT = 0x0004
_INVALID_HANDLE = 6  # ERROR_INVALID_HANDLE: what GetConsoleMode says of a non-console


def _vt(stream: object) -> bool:
    """Whether the terminal behind `stream` reads escapes, asking a Windows console to.

    Everywhere else a terminal does. A Windows console does only in that mode: Windows
    Terminal is always in it, and a legacy conhost window is put into it here, as the
    Python 3.13 REPL and rich do, and left in it. False for a console that refuses
    (before Windows 10 1511) and for a character device that is no console: Windows
    says isatty() of NUL and of a serial port too. A stream that cannot be asked keeps
    the terminal's word: one with no fileno, or one that says isatty() over a pipe on
    purpose (an IDE's output pane), whose reader is the terminal.
    """
    if sys.platform != 'win32':
        return True
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        fd = stream.fileno()
        # Its own handle on the library, not ctypes.windll's shared one, where another
        # package may have set argument types that reject these calls.
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        # A HANDLE, not the default C int: a 64-bit handle must not be truncated.
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # Not a console at all (NUL), as opposed to a pipe, or a console handle
            # this process may write to but not query.
            return not (os.isatty(fd) and ctypes.get_last_error() == _INVALID_HANDLE)
        if mode.value & _VT:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | _VT))
    except Exception:
        # No fileno (an in-memory stream) or no console API: not a console to ask,
        # and never worth an exception at import.
        return True


def tier(stream: TextIO) -> int:
    """How much this stream can render: 0 plain ASCII, 1 unicode + color, 2 glyphs.

    A redirected or piped stdout has to stay copy-pasteable and CI logs render escapes as
    garbage, while stderr can still be a terminal. Glyphs are opt-in only -- the Nerd Font
    lives in the terminal emulator and is invisible from here, so it is never assumed.
    """
    try:
        tty = stream.isatty()
    except (AttributeError, NotImplementedError, OSError, ValueError):
        return 0
    if (
        not tty
        # IDLE's shell says it is a terminal and prints every escape literally.
        or type(stream).__module__.startswith('idlelib')
        or os.environ.get('TERM') == 'dumb'
        or os.environ.get('NO_COLOR')
        # Some tools export CI=false to mean "not CI"; only those two spellings are off.
        or os.environ.get('CI', '').lower() not in ('', '0', 'false')
    ):
        return 0
    # Last, so a console is only switched to escapes when they are about to be sent.
    if not _encodes(stream) or not _vt(stream):
        return 0
    return 2 if os.environ.get('NERD_FONT') == '1' else 1


_tolerate(sys.stdout)
_tolerate(sys.stderr)
TIER, ERR_TIER = tier(sys.stdout), tier(sys.stderr)
# (plain, unicode, glyph) per state. Font Awesome codepoints, stable across Nerd Fonts
# v2/v3 -- not the Material range, which v3 remapped.
SYMBOLS = {
    'ok': ('[OK]', '✓', '\uf00c'),
    'fail': ('[x]', '✗', '\uf00d'),
    'warn': ('[!]', '⚠', '\uf071'),
    'info': ('[i]', 'i', '\uf05a'),
    'new': ('+', '+', '\uf055'),
    'arrow': ('->', '→', '→'),
    'none': ('-', '—', '—'),
    # Edit link, after the id. No tier 0 form: a word with nothing to click.
    'edit': ('', '✎', '\uf044'),
    # Source-file link. Same: nothing to click at 0.
    'file': ('', '≡', '\uf15c'),
    # Where a document stands. A word at 0, because there the word is all there is.
    'published': ('[pub]', '●', '\uf06e'),
    'unpublished': ('[unpub]', '○', '\uf070'),
    'held': ('[held]', '◐', '\uf04c'),
    # Things, not outcomes: what the text beside them is. Unicode has no narrow,
    # one-color sign for most, so below tier 2 the text stands alone -- these are
    # what a Nerd Font adds.
    'folder': ('', '', '\uf07b'),
    'branch': ('', '', '\uf126'),
    'time': ('', '', '\uf017'),
    'date': ('', '', '\uf073'),
    'user': ('', '', '\uf007'),
    'lock': ('', '', '\uf023'),
    'cloud': ('', '', '\uf0c2'),
    'tag': ('', '', '\uf02b'),
    'bug': ('', '', '\uf188'),
    'download': ('', '↓', '\uf019'),
    'upload': ('', '↑', '\uf093'),
}
# ponytail: 16-color SGR only. A handful of semantic states do not need truecolor, and
# raw 24-bit escapes are only safe where COLORTERM proves them.
COLORS = {
    'ok': '32',
    'fail': '31',
    'warn': '33',
    'info': '36',
    'new': '32',
    'published': '32',
    'unpublished': '2',
    'held': '35',
    'head': '1',
    'dim': '2',
}
# What progress() draws its bar with at each tier that draws one: (done, to do), or
# (done left, middle, right, to do left, middle, right) for a bar with end caps. The
# tier 2 set is Fira Code's progress glyphs, which every Nerd Font carries from 3.3
# (and the Fira Code one from 3.1); with an older font, set BARS[2] = BARS[1].
BARS = {
    1: ('━', '━'),
    2: ('\uee03', '\uee04', '\uee05', '\uee00', '\uee01', '\uee02'),
}
# Targets a run could not reach at all, in the order skip() saw them. Not failures -- the
# rest of the run is still worth doing -- but printed again at the end by the caller,
# because a skipped target reads like "nothing to do".
SKIPPED: list[str] = []


def _percent(match: re.Match[str]) -> str:
    return ''.join(f'%{byte:02X}' for byte in match[0].encode('utf-8'))


def link(url: str, label: str | None = None, tier_: int | None = None) -> str:
    """`label` as a clickable OSC 8 hyperlink, degrading to plain text + the URL.

    A file:// target needs `wslpath -w` first under WSL, since the terminal opens links on
    the Windows side. A control character in `url` is percent-encoded on a terminal,
    in the escape and in the text shown when there is no label: raw, it would end the
    escape early and spill the rest of the URL onto the screen. A label is the caller's.
    """
    t = TIER if tier_ is None else tier_
    if t:
        target = _CONTROL.sub(_percent, url)
        return f'\033]8;;{target}\033\\{label or target}\033]8;;\033\\'
    # A label that is the URL says it once.
    return f'{label}  {url}' if label and label != url else url


def paint(text: object, color: str, tier_: int | None = None) -> str:
    """SGR color at tier 1+, untouched at tier 0. `tier_` defaults to stdout's.

    Presentation edge only: nothing styled here should be compared, POSTed, or written to
    a file.
    """
    # Looked up at every tier, so a misspelled color fails in a piped test run too rather
    # than only in someone's terminal.
    code = COLORS[color]
    t = TIER if tier_ is None else tier_
    return f'\033[{code}m{text}\033[0m' if t else str(text)


def mark(kind: str, text: object, tier_: int | None = None) -> str:
    """`✓ text` -- this tier's symbol for `kind`, colored when `kind` has a color.

    A state with no symbol at this tier (`edit` at 0, `folder` below 2) gives the text
    alone, not the text after a stray space.
    """
    t = TIER if tier_ is None else tier_
    symbol = SYMBOLS[kind][t]
    line = f'{symbol} {text}' if symbol else str(text)
    return paint(line, kind, t) if kind in COLORS else line


def change(
    field: str,
    old: object,
    new: object,
    indent: str = '      ',
    tier_: int | None = None,
) -> str:
    """One `field: old -> new` line, the value being replaced dimmed."""
    t = TIER if tier_ is None else tier_
    return f'{indent}{field}: {paint(old, "dim", t)} {SYMBOLS["arrow"][t]} {new}'


def divider(tier_: int | None = None) -> str:
    """A full-width dim rule for stdout.

    Terminal width when there is a terminal; get_terminal_size falls back to COLUMNS and
    then 80, which is the right answer for a piped run anyway.
    """
    t = TIER if tier_ is None else tier_
    # Before 3.11 a TTY reporting 0x0 (docker exec -t, serial consoles) returns 0 columns
    # rather than the fallback.
    width = shutil.get_terminal_size((80, 24)).columns or 80
    return paint(('─' if t else '-') * width, 'dim', t)


# Categories that fill no column: combining marks and format characters.
_NO_WIDTH = ('Mn', 'Me', 'Cf')
# ...but not these format characters, which terminals (and wcwidth) give a column:
# the soft hyphen and the Arabic, Syriac, and Kaithi signs that sit before a number.
_FORMAT_WIDE = frozenset(
    map(chr, (0xAD, *range(0x600, 0x606), 0x6DD, 0x70F, 0x890, 0x891, 0x8E2))
) | {'\U000110bd', '\U000110cd'}
# Escapes fill no columns: any CSI sequence, any OSC or other control string (DCS,
# SOS, PM, APC, whose text a terminal hides), and the two- and three-byte escapes
# (`tput sgr0` sends ESC ( B), so a cell another library styled measures right too.
# A control character ends an OSC string early on a terminal, so one inside is no
# part of the escape here either.
_ESCAPE = re.compile(
    r'\033\[[0-?]*[ -/]*[@-~]'
    r'|\033\][^\x00-\x1f\x7f-\x9f]*(?:\033\\|\a)'
    r'|\033[PX^_][^\x00-\x1f\x7f-\x9f]*\033\\'
    r'|\033[()*+][0-9A-Za-z]'
    # The string introducers are no escape alone: one left unmatched above was cut
    # short, and passing it through would hide everything after it.
    r'|\033[@-OQ-WYZ\\]'
)
# Every C0 and C1 control and the two Unicode line breaks. ESC too: _one_line()
# applies this only between whole escapes, where an ESC is the start of a broken
# one that would leave the terminal waiting for its end.
_CONTROL = re.compile(r'\r\n|[\x00-\x1f\x7f-\x9f\u2028\u2029]')


def _one_line(text: object) -> str:
    """`text` with each line break, tab, and other control character as a space.

    A table row or progress line has to stay one line, and a tab or carriage return
    moves the cursor by an amount nothing here can measure. Escapes are kept whole:
    the BEL that ends another library's hyperlink is not a stray control character.
    """
    text = str(text)
    out, end = [], 0
    for escape in _ESCAPE.finditer(text):
        out += [_CONTROL.sub(' ', text[end : escape.start()]), escape[0]]
        end = escape.end()
    return ''.join(out) + _CONTROL.sub(' ', text[end:])


def _pictograph(code: int) -> bool:
    """Whether a zero-width joiner can make this codepoint part of the emoji before it:
    the arrows-to-symbols blocks (♂, ❤, ⚠) and the emoji planes, but no letter."""
    return 0x2190 <= code <= 0x2BFF or 0x1F000 <= code <= 0x1FAFF


def _width(text: str) -> int:
    """Terminal columns `text` fills, so a painted or linked cell still lines up.

    Escapes and combining marks fill none and East Asian wide characters two. An emoji
    built from several codepoints is measured as the one glyph a terminal draws: a
    zero-width joiner between two pictographs hides the second, U+FE0F widens a narrow
    symbol to two, a skin tone adds nothing, and a flag's two regional indicators make
    two. Hangul vowels and finals join the syllable before them.
    """
    width = last = prev = 0
    chars = _ESCAPE.sub('', text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        code = ord(ch)
        i += 1
        if code == 0x200D:
            # Only an emoji sequence: between letters a joiner shapes, it hides nothing.
            if last == 2 and i < len(chars) and _pictograph(ord(chars[i])):
                i += 1
        elif code == 0xFE0F:
            # Any symbol may have an emoji form (the copyright sign does), and so does
            # a keycap's digit; an ASCII letter has none to select.
            if last == 1 and (prev > 0x7F or chr(prev) in '#*0123456789'):
                width += 1
                last = 2
        elif (
            0x1F3FB <= code <= 0x1F3FF
            or 0x1160 <= code <= 0x11FF
            or 0xD7B0 <= code <= 0xD7FF
            or (unicodedata.category(ch) in _NO_WIDTH and ch not in _FORMAT_WIDE)
        ):
            continue
        else:
            # Regional indicators by range, not east_asian_width: one each makes a
            # flag two whichever Unicode version this Python carries.
            wide = unicodedata.east_asian_width(ch) in ('W', 'F')
            last = 2 if wide and not 0x1F1E6 <= code <= 0x1F1FF else 1
            width += last
            prev = code
    return width


def table(
    rows: Iterable[Sequence[object]],
    headers: Sequence[object] | None = None,
    align: str = '',
    tier_: int | None = None,
) -> str:
    """`rows` as aligned columns, one line per row at every tier.

    Tier 0 is the header line and the rows, padded with spaces: no borders and no rule,
    so every line of a piped table is a record grep and awk can use. Tier 1 makes the
    header bold and adds a dim rule under it. `align` is one `l` or `r` per column
    (`'lr'`), left where it runs out. A None cell prints as the `none` symbol, so an
    empty cell cannot shift the columns after it; a line break, tab, or other control
    character in a cell becomes a space.

    ponytail: cells are never wrapped or truncated, because a wrapped row is no longer
    one record. A table wider than the terminal wraps there; use rich for layout.
    """
    if set(align) - {'l', 'r'}:
        # At every tier, like paint(): a typo fails in a piped test run too.
        raise ValueError(f"align must be only 'l' and 'r', not {align!r}")
    t = TIER if tier_ is None else tier_
    none = SYMBOLS['none'][t]

    def texts(cells: Sequence[object]) -> list[str]:
        return [none if c is None else _one_line(c) for c in cells]

    head = texts(headers) if headers else []
    body = [texts(row) for row in rows]
    widths = [0] * max((len(r) for r in (head, *body)), default=0)
    for row in (head, *body):
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _width(cell))

    def line(cells: list[str], color: str | None = None) -> str:
        out = []
        for i, width in enumerate(widths):
            cell = cells[i] if i < len(cells) else ''
            pad = ' ' * (width - _width(cell))
            # Padding stays outside the escapes, so a styled cell is not a styled gap.
            if color and cell:
                cell = paint(cell, color, t)
            out.append(pad + cell if align[i : i + 1] == 'r' else cell + pad)
        return '  '.join(out).rstrip()

    lines = [line(head, 'head')] if head else []
    if head and t:
        # rstrip: a last column of empty cells would leave the rule ending in spaces.
        lines.append(paint('  '.join('─' * w for w in widths).rstrip(), 'dim', t))
    lines += [line(row) for row in body]
    return '\n'.join(lines)


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02}:{secs:02}' if hours else f'{minutes}:{secs:02}'


# Cached: a loop's label and its terminal's width are the same at every redraw.
@functools.lru_cache(maxsize=32)
def _clip(text: str, columns: int) -> str:
    """`text` cut to `columns` columns at most, ending in `…` where it was cut.

    A progress line wider than the terminal wraps, and a wrapped line cannot be redrawn
    in place: every redraw would leave a copy behind. Escapes are dropped from a text
    that is cut, since half of one would style or swallow the rest of the line. With
    room for the `…` alone there is nothing worth showing. `text` is one line, as
    _one_line() leaves it.
    """
    if columns < 2:
        return text if _width(text) <= columns else ''
    if _width(text) <= columns:
        return text
    # Taking escapes out can leave the two halves of a new one side by side.
    text = _CONTROL.sub(' ', _ESCAPE.sub('', text))
    # The longest prefix that leaves a column for the ellipsis. A prefix never gets
    # narrower as it grows, so halving finds it, and the longest of equal width keeps
    # an accent or a skin tone with the character it belongs to.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _width(text[:middle]) < columns:
            low = middle
        else:
            high = middle - 1
    cut = text[:low]
    # Half a flag is one regional indicator, which terminals draw as wide as the flag.
    flags = len(cut) - len(cut.rstrip(''.join(map(chr, range(0x1F1E6, 0x1F200)))))
    if flags % 2:
        cut = cut[:-1]
    cut = cut.rstrip()
    return cut + '…' if cut else ''


def _checked(bar: Sequence[str]) -> tuple[str, ...]:
    """`bar` as BARS holds one, or ValueError: two glyphs, or six for end caps."""
    glyphs = tuple(bar)
    if len(glyphs) not in (2, 6):
        raise ValueError(f'bar must be 2 or 6 glyphs, not {bar!r}')
    for glyph in glyphs:
        # Each fills its own columns on the one line: no empty one, no line break.
        if not isinstance(glyph, str) or not _width(glyph) or _one_line(glyph) != glyph:
            raise ValueError(f'bar glyphs must each be one-line text, not {bar!r}')
    return glyphs


def _cells(bar: Sequence[str], count: int, filled: int, t: int) -> str:
    """A bar `count` glyphs long with the first `filled` done.

    Each glyph is padded to the widest, so a bar of geese and dots keeps its length
    as it fills. The part to do is dimmed only when it is the same glyph as the part
    done, where nothing else tells them apart.
    """
    done, todo = (bar[:3], bar[3:]) if len(bar) == 6 else ([bar[0]] * 3, [bar[1]] * 3)
    width = max(_width(glyph) for glyph in bar)

    def cell(i: int) -> str:
        glyph = (done if i < filled else todo)[
            0 if i == 0 else 2 if i == count - 1 else 1
        ]
        return glyph + ' ' * (width - _width(glyph))

    head = ''.join(cell(i) for i in range(filled))
    tail = ''.join(cell(i) for i in range(filled, count))
    if tail and list(done) == list(todo):
        tail = paint(tail, 'dim', t)
    return head + tail


def _bar(
    done: int,
    total: int | None,
    label: str,
    t: int,
    columns: int,
    elapsed: float = 0.0,
    bar: Sequence[str] = ('━', '━'),
) -> str:
    """One progress line: `label ━━━━━━ 3/10 eta 0:07`, or `label 3 0:02` uncounted.

    The bar is dropped when it cannot fit, and before it the estimate; a label that
    still does not fit is cut short. The estimate is the mean pace so far, shown once
    an item has finished.
    """
    tail = f'{done}/{total}' if total else f'{done} {_clock(elapsed)}'
    # The count is kept whole, and one column spare (see below).
    label = _clip(label, columns - 2 - len(tail))
    parts = [label] if label else []
    if total:
        if 0 < done < total:
            eta = f'{tail} eta {_clock(elapsed * (total - done) / done)}'
            # A line that wraps cannot be redrawn in place: the count matters more.
            if _width(' '.join([*parts, eta])) < columns:
                tail = eta
        # One column spare: a line that reaches the last column wraps on some terminals,
        # and then \r no longer returns to its start.
        size = min(30, columns - 2 - _width(' '.join([*parts, tail])))
        # A glyph may be two columns wide (an emoji): the bar is counted in glyphs.
        count = size // max(_width(glyph) for glyph in bar)
        if size >= 5 and count >= 2:
            parts.append(_cells(bar, count, min(count, count * done // total), t))
    parts.append(tail)
    return ' '.join(parts)


class _Bar:
    """One running progress() loop."""

    def __init__(
        self, label: str, total: int | None, t: int, bar: Sequence[str]
    ) -> None:
        self.label, self.total, self.tier = _one_line(label), total, t
        self.bar = bar
        self.done = 0
        self.start = time.monotonic()


class _Above:
    """Stands in for a terminal stream while bars are live: text prints above them.

    Without it a print() inside the loop would land on the bar's own line. Everything
    but writing is the wrapped stream's.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        with _LIVE.lock:
            # No bars: a stand-in something kept a reference to. Busy: the bars' own
            # write, coming back through a wrapper someone put around this stand-in.
            # Empty: a probe (click sends one to learn the stream's type), nothing
            # to move the bars for.
            if not _LIVE.bars or _LIVE.busy or not text:
                return self._stream.write(text)
            _LIVE.erase()
            written = self._stream.write(text)
            # stdout and stderr share the screen, so neither may sit in a buffer while
            # the other draws.
            self._stream.flush()
            # print() writes its text and its newline separately; the bars wait for
            # the line to end rather than cut it in two.
            _LIVE.midline = not text.endswith('\n')
            _LIVE.draw()
            return written

    def writelines(self, lines: Iterable[str]) -> None:
        # One at a time, as a stream does: lines already written stay written if
        # the iterable then raises.
        for line in lines:
            self.write(line)

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)


def _columns(stream: object) -> int:
    """The width of the terminal behind `stream`, for a line drawn on stderr.

    Not shutil.get_terminal_size, which measures stdout: that is a file, or another
    width entirely, exactly when stdout is redirected and the bar is all there is.
    """
    try:
        columns = int(os.environ.get('COLUMNS', ''))
    except ValueError:
        columns = 0
    if columns <= 0:
        try:
            columns = os.get_terminal_size(stream.fileno()).columns  # ty: ignore[unresolved-attribute]
        except (AttributeError, NotImplementedError, OSError, ValueError):
            columns = 0
    return columns if columns > 0 else 80


class _Live:
    """The progress lines on screen now, innermost loop last.

    Shared by every progress() in the process, because they share one terminal: each
    redraw moves up over all of the lines and rewrites them.
    """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.bars: list[_Bar] = []
        self.out: TextIO = sys.stderr
        self.drawn = 0  # lines on screen
        self.midline = False
        self.busy = False
        self.dead = False
        self.last = float('-inf')

    def add(self, bar: _Bar) -> None:
        with self.lock:
            if not self.bars:
                out = sys.stderr
                # A stand-in left behind (its loop's stream was swapped under it) is
                # not the terminal: drawing through it would come straight back here.
                while isinstance(out, _Above):
                    out = out._stream
                self.out = out
                self.midline = self.dead = False
                self.drawn = 0
                if not isinstance(sys.stderr, _Above):
                    sys.stderr = _Above(out)
                try:
                    shared = sys.stdout.isatty()
                except (AttributeError, NotImplementedError, OSError, ValueError):
                    shared = False
                # A piped stdout is not on the screen: left alone.
                if shared and not isinstance(sys.stdout, _Above):
                    sys.stdout = _Above(sys.stdout)
            try:
                ''.join(bar.bar).encode(getattr(self.out, 'encoding', None) or 'utf-8')
            except (LookupError, UnicodeEncodeError):
                # The bar's glyphs are not in _PROBE, so they took tier 1 from no stream
                # that had it; '─' is in _PROBE.
                bar.bar = ('─', '─')
            self.bars.append(bar)
            self.draw()

    def remove(self, bar: _Bar) -> None:
        with self.lock:
            try:
                self.erase()
            finally:
                # Whatever the terminal did, the loop is over: a bar or a stand-in
                # left behind would haunt every later print in the process.
                if bar in self.bars:
                    self.bars.remove(bar)
                if self.bars:
                    self.draw()
                else:
                    # Only stand-ins still in place: a stream someone swapped
                    # meanwhile is theirs.
                    if isinstance(sys.stdout, _Above):
                        sys.stdout = sys.stdout._stream
                    if isinstance(sys.stderr, _Above):
                        sys.stderr = sys.stderr._stream

    def _write(self, text: str) -> None:
        """To the terminal; one that fails (hung up, closed) is not written to again.

        A progress line is never worth an exception in the caller's loop.
        """
        if self.dead:
            return
        self.busy = True
        try:
            self.out.write(text)
            self.out.flush()
        except (OSError, ValueError):
            self.dead = True
        finally:
            self.busy = False

    def _home(self) -> str:
        """To the start of the first line drawn."""
        return '\r' + (f'\033[{self.drawn - 1}A' if self.drawn > 1 else '')

    def erase(self) -> None:
        with self.lock:
            if self.drawn:
                self._write(self._home() + '\033[J')
                self.drawn = 0

    def draw(self, now: float | None = None) -> None:
        """Redraw every bar; given `now`, only if a tenth of a second has passed."""
        with self.lock:
            if not self.bars or self.midline or self.dead:
                return
            if now is not None and now - self.last < 0.1:
                return
            self.last = time.monotonic() if now is None else now
            columns = _columns(self.out)
            lines = [
                _bar(
                    b.done,
                    b.total,
                    b.label,
                    b.tier,
                    columns,
                    self.last - b.start,
                    b.bar,
                )
                + '\033[K'
                for b in self.bars
            ]
            self._write(self._home() + '\n'.join(lines))
            self.drawn = len(lines)


_LIVE = _Live()


def progress(
    items: Iterable[_T],
    label: str = '',
    total: int | None = None,
    bar: Sequence[str] | None = None,
    tier_: int | None = None,
) -> Iterator[_T]:
    """Yield `items`, redrawing one progress line on stderr; silent at tier 0.

    On stderr and at ERR_TIER, so a piped stdout stays data and a log never collects
    redraws. The line is erased when the loop runs to its end (after a break or an
    exception, see below): print the outcome yourself with mark(). `total` defaults to
    len(items); without either, it counts and shows the time elapsed, not an estimate.

    While a loop runs, sys.stderr (and sys.stdout, when it is a terminal too) is wrapped
    so that anything printed appears above the line. Loops may nest or run in threads:
    each gets a line, in the order they started. A line starts at the left margin, so
    finish any half-printed line before the loop. A terminal that stops accepting
    writes ends the drawing, not the loop.

    `bar` replaces the glyphs BARS gives this tier: (done, to do), as in ('#', '.'), or
    six with end caps as BARS[2] has. A glyph may be two columns wide.

    This is a generator, so after a break or an exception the line goes when the
    generator is closed. CPython closes one nothing else refers to at once; the language
    does not promise that, and an iterator you keep holds its line until `.close()`.
    Wrap the call in contextlib.closing() where the cleanup has to be certain.

    ponytail: no refresh thread, so the line redraws when an item finishes or a printed
    line ends (a loop printing dots with end='' shows no bar). Use rich for those.
    """
    t = ERR_TIER if tier_ is None else tier_
    # Checked at every tier, like a color: a typo fails in a piped test run too.
    glyphs = _checked(BARS[min(t, 2) or 1] if bar is None else bar)
    if not t or sys.stderr is None:  # pythonw: nowhere to draw
        yield from items
        return
    if total is None:
        try:
            total = len(items)  # ty: ignore[invalid-argument-type]
        except TypeError:
            pass
    line = _Bar(label, total, t, glyphs)
    try:
        _LIVE.add(line)
        for item in items:
            yield item
            line.done += 1
            # Ten redraws a second at most: a tight loop should not spend its time here.
            _LIVE.draw(time.monotonic())
    finally:
        _LIVE.remove(line)


def skip(text: str, outcome: str = 'skipping') -> None:
    """A target this run could not reach: printed as an error, kept in SKIPPED.

    The recap keeps only the first line, so detail lines after it (one per thing tried,
    say) print once here and stay out of the end-of-run summary.
    """
    head, _, rest = text.partition('\n')
    print(mark('fail', f'{head} -- {outcome}', ERR_TIER), file=sys.stderr)
    if rest:
        print(rest, file=sys.stderr)
    SKIPPED.append(f'{head} -- {outcome}')


def error(text: str) -> None:
    """One red error line on stderr."""
    print(mark('fail', text, ERR_TIER), file=sys.stderr)


def warn(text: str) -> None:
    """One yellow warning line on stderr."""
    print(mark('warn', text, ERR_TIER), file=sys.stderr)


def die(text: str, code: int = 1) -> NoReturn:
    """A red error line on stderr, then exit."""
    error(text)
    sys.exit(code)


class _TTY(io.StringIO):
    """An in-memory stream that claims to be a terminal, in a chosen encoding."""

    def __init__(self, encoding: str | None = None) -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str | None:  # ty: ignore[invalid-property-type-override]
        return self._encoding

    def isatty(self) -> bool:
        return True


def _tier_under(env: dict[str, str], encoding: str | None = 'utf-8') -> int:
    """tier() of a terminal with exactly `env` set among the variables it reads."""
    names = ('TERM', 'NO_COLOR', 'CI', 'NERD_FONT')
    saved = {n: os.environ.get(n) for n in names}
    try:
        for n in names:
            os.environ.pop(n, None)
        os.environ.update(env)
        return tier(_TTY(encoding))
    finally:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


def _same(got: object, want: object) -> None:
    # Not `assert`: python -O strips those, and a selftest that cannot fail is worse than
    # none.
    if got != want:
        raise AssertionError(f'{got!r} != {want!r}')


def selftest() -> int:
    """The exact escapes and symbols, so callers' tests can build theirs from these.

    Kept in the module rather than only in tests/ so a script that extends SYMBOLS and
    COLORS can call it from its own selftest: the states it added are checked too.
    """
    # Nothing styled may reach a value that is compared or written, so the plain tier has
    # to be byte-identical to the unstyled text.
    _same(link('http://x', 'A1', 1), '\033]8;;http://x\033\\A1\033]8;;\033\\')
    _same(mark('warn', 'x', 1), '\033[33m⚠ x\033[0m')
    _same(mark('arrow', 'x', 1), '→ x')  # a state with no color stays plain
    _same(paint('x', 'dim', 1), '\033[2mx\033[0m')
    _same(change('due', 'a', 'b', '', 1), 'due: \033[2ma\033[0m → b')
    _same(link('http://x', 'A1', 0), 'A1  http://x')
    _same(link('http://x', tier_=0), 'http://x')
    _same(link('http://x', 'http://x', 0), 'http://x')
    # A control character cannot end the link escape early.
    _same(link('http://x/\033\\a\n', 'A1', 1), link('http://x/%1B\\a%0A', 'A1', 1))
    _same(link('http://x/\n', tier_=1), link('http://x/%0A', tier_=1))
    _same(mark('warn', 'x', 0), '[!] x')
    _same(paint('x', 'dim', 0), 'x')
    _same(change('due', 'a', 'b', '', 0), 'due: a -> b')
    _same(mark('ok', 'x', 2), '\033[32m\uf00c x\033[0m')  # glyph tier is opt-in
    _same(mark('file', 'x', 0), 'x')  # no tier 0 symbol, no stray space
    _same(mark('held', 'x', 0), '[held] x')
    _same(mark('published', 'x', 1), '\033[32m● x\033[0m')
    _same(mark('folder', 'x', 1), 'x')  # a thing has no sign below tier 2
    _same(mark('folder', 'x', 2), '\uf07b x')
    # A piped table is records only; styling a cell must not move the columns.
    rows = [('A1', 10), ('A22', None)]
    _same(table(rows, ('id', 'pts'), 'lr', 0), 'id   pts\nA1    10\nA22    -')
    _same(
        table([(paint('A1', 'ok', 1), 'x'), ('A22', 'y')], tier_=1),
        '\033[32mA1\033[0m   x\nA22  y',
    )
    _same(
        table(rows, ('id', 'pts'), tier_=1).splitlines()[:2],
        ['\033[1mid\033[0m   \033[1mpts\033[0m', '\033[2m───  ───\033[0m'],
    )
    _same(_width(link('http://x', '日本', 1)), 4)
    # One glyph each, however many codepoints: family, flag, skin tone, keycap, U+FE0F.
    for glyph in ('👨\u200d👩\u200d👧', '🇺🇸', '👍🏽', '1\ufe0f\u20e3', '⚠\ufe0f'):
        _same((glyph, _width(glyph)), (glyph, 2))
    _same(
        _bar(1, 2, 'sync', 1, 80, 3),
        'sync ' + '━' * 15 + paint('━' * 15, 'dim', 1) + ' 1/2 eta 0:03',
    )
    _same(_bar(3, None, '', 1, 80, 3725), '3 1:02:05')
    _same(_bar(1, 9, 'a long label', 1, 10), 'a lo… 1/9')  # never as wide as the screen
    # Every tier's bar is drawable, the caller's included, and an end-capped one caps.
    for glyphs in BARS.values():
        _checked(glyphs)
    _same(_cells('<=>[.]', 4, 2, 1), '<=.]')
    _same(_cells(('ab', '.'), 3, 1, 1), 'ab. . ')
    _same(list(progress(iter('ab'), tier_=0)), ['a', 'b'])
    _same({'ok', 'fail', 'warn', 'info', 'new', 'dim', 'head'} - set(COLORS), set())
    # Every state, the caller's included: three string forms, and markable at each tier.
    for kind, forms in SYMBOLS.items():
        _same(
            (kind, len(forms), all(isinstance(f, str) for f in forms)), (kind, 3, True)
        )
        for t in range(3):
            mark(kind, 'x', t)
    for kind, code in COLORS.items():
        _same((kind, all(p.isdigit() for p in code.split(';'))), (kind, True))
    # A stream that is not a terminal never gets an escape, whatever the terminal is.
    with open(os.devnull, 'w') as devnull:
        _same(tier(devnull), 0)
    _same(tier(object()), 0)  # ty: ignore[invalid-argument-type]  # no isatty at all
    # Every branch of tier(), here and not only in tests/, because a copied file carries
    # no tests: a terminal, glyphs on request, each plain switch, and an encoding that
    # cannot print the symbols.
    _same(_tier_under({}), 1)
    _same(_tier_under({'NERD_FONT': '1'}), 2)
    _same(_tier_under({'CI': 'false'}), 1)
    _same(_tier_under({'CI': '0'}), 1)
    _same(_tier_under({'CI': 'true', 'NERD_FONT': '1'}), 0)
    _same(_tier_under({'NO_COLOR': '1'}), 0)
    _same(_tier_under({'NO_COLOR': ''}), 1)  # no-color.org: only a non-empty value
    _same(_tier_under({'TERM': 'dumb'}), 0)
    _same(_tier_under({}, 'latin-1'), 0)
    _same(_tier_under({}, None), 1)
    # ...and such a stream escapes what it cannot encode instead of raising.
    latin = io.TextIOWrapper(io.BytesIO(), encoding='latin-1')
    _tolerate(latin)
    latin.write('a—b')
    latin.flush()
    _same(latin.buffer.getvalue(), b'a\\u2014b')
    utf8 = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
    _tolerate(utf8)
    _same(utf8.errors, 'strict')
    print('term_styling selftest ok')
    return 0


__all__ = [
    'BARS',
    'COLORS',
    'ERR_TIER',
    'SKIPPED',
    'SYMBOLS',
    'TIER',
    'change',
    'die',
    'divider',
    'error',
    'link',
    'mark',
    'paint',
    'progress',
    'selftest',
    'skip',
    'table',
    'tier',
    'warn',
]

# For a copy of this file saved as term_styling.py, `python3 term_styling.py` runs the
# selftest; in the package, __main__.py does the same for `python -m term_styling`.
if __name__ == '__main__':
    sys.exit(selftest())
