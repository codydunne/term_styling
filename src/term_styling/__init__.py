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

import io
import os
import re
import shutil
import sys
import time
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from typing import NoReturn, TextIO, TypeVar

_T = TypeVar('_T')

# A literal, not importlib.metadata, so a vendored copy carries its own version;
# tests/ checks it against pyproject.toml.
__version__ = '0.2.1'
# Every tier-1 character the base SYMBOLS, divider(), table(), and progress() print. A stream whose encoding
# cannot hold them (a Latin-1 locale, PYTHONIOENCODING=ascii) gets tier 0.
_PROBE = '✓✗⚠→—✎≡─━'


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


def tier(stream: TextIO) -> int:
    """How much this stream can render: 0 plain ASCII, 1 unicode + color, 2 glyphs.

    A redirected or piped stdout has to stay copy-pasteable and CI logs render escapes as
    garbage, while stderr can still be a terminal. Glyphs are opt-in only -- the Nerd Font
    lives in the terminal emulator and is invisible from here, so it is never assumed.
    """
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError):
        return 0
    if (
        not tty
        or os.environ.get('TERM') == 'dumb'
        or os.environ.get('NO_COLOR')
        # Some tools export CI=false to mean "not CI"; only those two spellings are off.
        or os.environ.get('CI', '').lower() not in ('', '0', 'false')
    ):
        return 0
    if not _encodes(stream):
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
}
# ponytail: 16-color SGR only. A handful of semantic states do not need truecolor, and
# raw 24-bit escapes are only safe where COLORTERM proves them.
COLORS = {
    'ok': '32',
    'fail': '31',
    'warn': '33',
    'info': '36',
    'new': '32',
    'head': '1',
    'dim': '2',
}
# Targets a run could not reach at all, in the order skip() saw them. Not failures -- the
# rest of the run is still worth doing -- but printed again at the end by the caller,
# because a skipped target reads like "nothing to do".
SKIPPED: list[str] = []


def link(url: str, label: str | None = None, tier_: int | None = None) -> str:
    """`label` as a clickable OSC 8 hyperlink, degrading to plain text + the URL.

    A file:// target needs `wslpath -w` first under WSL, since the terminal opens links on
    the Windows side.
    """
    t = TIER if tier_ is None else tier_
    if t:
        return f'\033]8;;{url}\033\\{label or url}\033]8;;\033\\'
    return f'{label}  {url}' if label else url


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

    A state with no symbol at this tier (`edit` and `file` at 0) gives the text alone,
    not the text after a stray space.
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


# What paint() and link() wrap around text: zero columns wide on screen.
_ESCAPE = re.compile(r'\033\[[0-9;]*m|\033\]8;;[^\033]*\033\\')


def _width(text: str) -> int:
    """Terminal columns `text` fills, so a painted or linked cell still lines up.

    ponytail: stdlib only, so escapes and combining marks count 0 and East Asian wide
    characters 2, but a multi-codepoint emoji is not measured as one glyph.
    """
    width = 0
    for ch in _ESCAPE.sub('', text):
        if unicodedata.category(ch) in ('Mn', 'Me', 'Cf'):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
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
    empty cell cannot shift the columns after it; a newline in a cell becomes a space.

    ponytail: cells are never wrapped or truncated, because a wrapped row is no longer
    one record. A table wider than the terminal wraps there; use rich for layout.
    """
    if set(align) - {'l', 'r'}:
        # At every tier, like paint(): a typo fails in a piped test run too.
        raise ValueError(f"align must be only 'l' and 'r', not {align!r}")
    t = TIER if tier_ is None else tier_
    none = SYMBOLS['none'][t]

    def texts(cells: Sequence[object]) -> list[str]:
        return [none if c is None else str(c).replace('\n', ' ') for c in cells]

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
        lines.append(paint('  '.join('─' * w for w in widths), 'dim', t))
    lines += [line(row) for row in body]
    return '\n'.join(lines)


def _bar(done: int, total: int | None, label: str, t: int, columns: int) -> str:
    """One progress line: `label ━━━━━━ 3/10`, the bar dropped when it cannot fit."""
    count = f'{done}/{total}' if total else str(done)
    parts = [label] if label else []
    if total:
        # One column spare: a line that reaches the last column wraps on some terminals,
        # and then \r no longer returns to its start.
        size = min(30, columns - 2 - _width(' '.join([*parts, count])))
        if size >= 5:
            fill = min(size, size * done // total)
            rest = paint('━' * (size - fill), 'dim', t) if fill < size else ''
            parts.append('━' * fill + rest)
    parts.append(count)
    return ' '.join(parts)


def progress(
    items: Iterable[_T],
    label: str = '',
    total: int | None = None,
    tier_: int | None = None,
) -> Iterator[_T]:
    """Yield `items`, redrawing one progress line on stderr; silent at tier 0.

    On stderr and at ERR_TIER, so a piped stdout stays data and a log never collects
    redraws. The line is erased when the loop ends, breaks, or raises: print the outcome
    yourself with mark(). `total` defaults to len(items); without either, it counts.

    ponytail: one bar, no ETA, and nothing guards the line, so text printed inside the
    loop lands on it. Use rich for several bars or for logging under a live bar.
    """
    t = ERR_TIER if tier_ is None else tier_
    if not t:
        yield from items
        return
    if total is None:
        try:
            total = len(items)  # ty: ignore[invalid-argument-type]
        except TypeError:
            pass
    done, drawn = 0, float('-inf')
    try:
        for item in items:
            now = time.monotonic()
            # Ten redraws a second at most: a tight loop should not spend its time here.
            if now - drawn >= 0.1:
                drawn = now
                columns = shutil.get_terminal_size((80, 24)).columns or 80
                sys.stderr.write(f'\r{_bar(done, total, label, t, columns)}\033[K')
                sys.stderr.flush()
            yield item
            done += 1
    finally:
        sys.stderr.write('\r\033[K')
        sys.stderr.flush()


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
    _same(mark('warn', 'x', 0), '[!] x')
    _same(paint('x', 'dim', 0), 'x')
    _same(change('due', 'a', 'b', '', 0), 'due: a -> b')
    _same(mark('ok', 'x', 2), '\033[32m\uf00c x\033[0m')  # glyph tier is opt-in
    _same(mark('file', 'x', 0), 'x')  # no tier 0 symbol, no stray space
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
    _same(
        _bar(1, 2, 'sync', 1, 80),
        'sync ' + '━' * 15 + paint('━' * 15, 'dim', 1) + ' 1/2',
    )
    _same(_bar(3, None, '', 1, 80), '3')
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
