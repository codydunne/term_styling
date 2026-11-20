from __future__ import annotations

import encodings.aliases
import importlib.metadata
import io
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

import term_styling

REPO = Path(__file__).resolve().parent.parent


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class AsciiTTY(FakeTTY):
    encoding = 'ascii'


class Latin1TTY(FakeTTY):
    encoding = 'latin-1'


class Utf8TTY(FakeTTY):
    encoding = 'utf-8'


class ClosedStream(io.StringIO):
    def isatty(self) -> bool:
        raise ValueError('I/O operation on closed file')


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    # A CI runner sets CI and a developer may set NO_COLOR; tier() must be tested from a
    # known environment, not whichever one pytest happens to run in.
    for var in ('TERM', 'NO_COLOR', 'CI', 'NERD_FONT'):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(term_styling, 'TIER', 0)
    monkeypatch.setattr(term_styling, 'ERR_TIER', 0)


def test_selftest(capsys: pytest.CaptureFixture[str]) -> None:
    assert term_styling.selftest() == 0
    assert capsys.readouterr().out == 'term_styling selftest ok\n'


def test_tier_terminal(clean_env: pytest.MonkeyPatch) -> None:
    assert term_styling.tier(FakeTTY()) == 1
    clean_env.setenv('TERM', 'xterm-256color')
    assert term_styling.tier(FakeTTY()) == 1


def test_tier_glyphs_are_opt_in(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv('NERD_FONT', '1')
    assert term_styling.tier(FakeTTY()) == 2
    clean_env.setenv('NERD_FONT', 'yes')  # only the exact value opts in
    assert term_styling.tier(FakeTTY()) == 1


@pytest.mark.parametrize(
    ('var', 'value'), [('NO_COLOR', '1'), ('CI', 'true'), ('TERM', 'dumb')]
)
def test_tier_plain_env(clean_env: pytest.MonkeyPatch, var: str, value: str) -> None:
    clean_env.setenv('NERD_FONT', '1')  # plain wins over glyphs
    clean_env.setenv(var, value)
    assert term_styling.tier(FakeTTY()) == 0


@pytest.mark.parametrize('value', ['false', 'FALSE', '0', ''])
def test_tier_ci_off_spellings(clean_env: pytest.MonkeyPatch, value: str) -> None:
    clean_env.setenv('CI', value)
    assert term_styling.tier(FakeTTY()) == 1


@pytest.mark.parametrize('stream', [AsciiTTY, Latin1TTY])
def test_tier_encoding_cannot_print_symbols(
    clean_env: pytest.MonkeyPatch, stream: type[FakeTTY]
) -> None:
    clean_env.setenv('NERD_FONT', '1')
    assert term_styling.tier(stream()) == 0


def test_tier_utf8_terminal(clean_env: pytest.MonkeyPatch) -> None:
    assert term_styling.tier(Utf8TTY()) == 1


def test_tier_empty_no_color_is_unset(clean_env: pytest.MonkeyPatch) -> None:
    # no-color.org: only a non-empty NO_COLOR disables color.
    clean_env.setenv('NO_COLOR', '')
    assert term_styling.tier(FakeTTY()) == 1


def test_tier_not_a_terminal(clean_env: pytest.MonkeyPatch) -> None:
    assert term_styling.tier(io.StringIO()) == 0
    assert term_styling.tier(ClosedStream()) == 0


def test_pinned_tier_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Callers' selftests pin the module global; every default has to follow it.
    monkeypatch.setattr(term_styling, 'TIER', 1)
    assert term_styling.paint('x', 'ok') == '\033[32mx\033[0m'
    monkeypatch.setattr(term_styling, 'TIER', 0)
    assert term_styling.paint('x', 'ok') == 'x'
    assert term_styling.link('http://x', 'A1') == 'A1  http://x'
    assert term_styling.mark('fail', 'x') == '[x] x'


def test_paint_stringifies_at_tier_0() -> None:
    assert term_styling.paint(3, 'dim', 0) == '3'


def test_paint_unknown_color_fails_at_every_tier() -> None:
    for t in range(3):
        with pytest.raises(KeyError):
            term_styling.paint('x', 'gren', t)


@pytest.mark.parametrize('kind', ['arrow', 'none', 'edit', 'file'])
def test_mark_uncolored_state_is_plain(kind: str) -> None:
    for t in (1, 2):
        assert term_styling.mark(kind, 'x', t) == f'{term_styling.SYMBOLS[kind][t]} x'


@pytest.mark.parametrize('kind', ['edit', 'file'])
def test_mark_without_tier_0_symbol_has_no_stray_space(kind: str) -> None:
    assert term_styling.mark(kind, 'x', 0) == 'x'
    assert term_styling.mark(kind, 3, 0) == '3'


def test_tolerate_escapes_unencodable_text() -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding='ascii')
    term_styling._tolerate(stream)
    assert stream.errors == 'backslashreplace'
    stream.write('in-class programming—vega-altair')
    stream.flush()
    assert stream.buffer.getvalue() == b'in-class programming\\u2014vega-altair'


def test_tolerate_leaves_capable_or_chosen_streams() -> None:
    utf8 = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
    term_styling._tolerate(utf8)
    assert utf8.errors == 'strict'
    chosen = io.TextIOWrapper(io.BytesIO(), encoding='ascii', errors='ignore')
    term_styling._tolerate(chosen)
    assert chosen.errors == 'ignore'
    term_styling._tolerate(None)  # pythonw: no stdout at all
    term_styling._tolerate(io.StringIO())


def test_import_makes_latin1_stdout_survive_caller_text() -> None:
    # The crash the encoding check alone did not prevent: tier 0, then a caller's own
    # em dash on the next print().
    code = "import term_styling; print(term_styling.mark('ok', 'a\u2014b'))"
    env = {**os.environ, 'PYTHONIOENCODING': 'latin-1'}
    run = subprocess.run(
        [sys.executable, '-c', code], capture_output=True, env=env, check=False
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == b'[OK] a\\u2014b'


def test_mark_unknown_kind_fails_at_every_tier() -> None:
    for t in range(3):
        with pytest.raises(KeyError):
            term_styling.mark('okk', 'x', t)


def test_link_without_label_at_tier_1() -> None:
    assert term_styling.link('http://x', tier_=1) == (
        '\033]8;;http://x\033\\http://x\033]8;;\033\\'
    )


def test_change_default_indent() -> None:
    assert term_styling.change('pts', 1, 2, tier_=0) == '      pts: 1 -> 2'


def test_divider(plain: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('COLUMNS', '12')
    assert term_styling.divider() == '-' * 12
    assert term_styling.divider(1) == '\033[2m' + '─' * 12 + '\033[0m'
    monkeypatch.setattr(term_styling, 'TIER', 1)
    assert term_styling.divider() == '\033[2m' + '─' * 12 + '\033[0m'


def test_divider_zero_width_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, 'get_terminal_size', lambda _: os.terminal_size((0, 0)))
    assert term_styling.divider(0) == '-' * 80


def test_selftest_checks_caller_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(term_styling.SYMBOLS, 'held', ('held', '◐'))
    with pytest.raises(AssertionError):
        term_styling.selftest()


def test_selftest_checks_color_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(term_styling.COLORS, 'held', 'yellow')
    with pytest.raises(AssertionError):
        term_styling.selftest()


def test_selftest_survives_optimize() -> None:
    # A bare assert vanishes under -O; the selftest must still fail on a broken table.
    code = "import term_styling; term_styling.SYMBOLS['held'] = ('a', 'b'); term_styling.selftest()"
    run = subprocess.run(
        [sys.executable, '-O', '-c', code], capture_output=True, text=True, check=False
    )
    assert run.returncode != 0
    assert 'AssertionError' in run.stderr


def test_extended_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(term_styling.SYMBOLS, 'live', ('on', '●', '\uf06e'))
    monkeypatch.setitem(term_styling.COLORS, 'live', '32')
    assert term_styling.mark('live', 'page', 0) == 'on page'
    assert term_styling.mark('live', 'page', 1) == '\033[32m● page\033[0m'


def test_table_plain_is_records_only() -> None:
    out = term_styling.table(
        [('A1', 10, 'in sync'), ('A22', 5), ('A3', None, 'two\nlines')],
        headers=('id', 'pts', 'state'),
        align='lr',
        tier_=0,
    )
    assert out.splitlines() == [
        'id   pts  state',
        'A1    10  in sync',
        'A22    5',  # a short row is padded, and no line ends in spaces
        'A3     -  two lines',
    ]
    assert out.isascii()


def test_table_terminal_has_bold_header_and_rule() -> None:
    out = term_styling.table([('A1', 10)], ('id', 'pts'), 'lr', tier_=1)
    assert out.splitlines() == [
        '\033[1mid\033[0m  \033[1mpts\033[0m',
        '\033[2m──  ───\033[0m',
        'A1   10',
    ]


def test_table_styled_and_wide_cells_stay_aligned() -> None:
    rows = [
        (term_styling.mark('ok', 'A1', 1), 'x'),
        (term_styling.link('http://example.org', 'A22', 1), 'x'),
        ('日本', 'x'),
        ('e\u0301', 'x'),  # combining accent: one column
    ]
    lines = term_styling.table(rows, tier_=1).splitlines()
    assert {term_styling._width(line) for line in lines} == {7}
    assert all(line.endswith('  x') for line in lines)


def test_table_without_headers_or_rows() -> None:
    assert term_styling.table([('a', 'b')], tier_=1) == 'a  b'
    assert term_styling.table([], tier_=0) == ''
    assert term_styling.table([], ('id',), tier_=0) == 'id'


def test_table_follows_pinned_tier(plain: None) -> None:
    assert term_styling.table([(None,)], ('x',)) == 'x\n-'


def test_table_bad_align_fails_at_every_tier() -> None:
    for t in range(3):
        with pytest.raises(ValueError, match='align'):
            term_styling.table([('a',)], align='c', tier_=t)


class Clock:
    """time.monotonic under the test's control."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    monkeypatch.setenv('COLUMNS', '80')
    fake = Clock()
    monkeypatch.setattr(term_styling.time, 'monotonic', fake)
    return fake


DIM = '\033[2m{}\033[0m'
ERASE = '\r\033[J'


@pytest.mark.parametrize(
    'glyph',
    [
        '👨\u200d👩\u200d👧',  # ZWJ family
        '🏳\ufe0f\u200d🌈',  # ZWJ after a variation selector
        '🇺🇸',  # two regional indicators
        '👍🏽',  # skin tone modifier
        '1\ufe0f\u20e3',  # keycap
        '⚠\ufe0f',  # narrow symbol with emoji presentation
        '🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f',  # tag flag
        '日',
    ],
)
def test_width_counts_one_glyph_as_two_columns(glyph: str) -> None:
    assert term_styling._width(glyph) == 2
    assert term_styling.table([(glyph, 'x'), ('ab', 'x')], tier_=0) == (
        f'{glyph}  x\nab  x'
    )


def test_width_of_plain_and_narrow_text() -> None:
    assert term_styling._width('⚠ áb') == 4
    assert term_styling._width('\u200d') == 0  # a joiner with nothing to join
    assert term_styling._width('a\u200db') == 2  # between letters it hides nothing
    assert term_styling._width('ᄀ\u1161\u11a8') == 2  # one Hangul syllable block


@pytest.mark.parametrize(
    'styled',
    [
        '\033[38;2;255;0;0mab\033[0m',  # 24-bit color
        '\033[38:2::255:0:0mab\033[m',  # ...in the colon form
        '\033]8;id=1;http://x\033\\ab\033]8;;\033\\',  # a link with parameters
        '\033]8;;http://x\aab\033]8;;\a',  # ...terminated by BEL
    ],
)
def test_width_ignores_other_libraries_escapes(styled: str) -> None:
    assert term_styling._width(styled) == 2


@pytest.mark.parametrize(
    ('text', 'width'),
    [
        ('\033(B\033[mab', 2),  # tput sgr0
        ('\033[>4;1mab\033[2~', 2),  # private parameters, a non-letter final byte
        ('\033Mab', 2),  # a two-byte escape
        ('\033^hidden\033\\x', 1),  # a privacy message: a hidden string
        ('\033Pq#0\033\\ab', 2),  # ...and a device control string
        ('a\ufe0f', 1),  # a letter has no emoji form
        ('\xa9\ufe0f', 2),  # ...but the copyright sign has
        ('\u2122\ufe0f\u2139\ufe0f\u203c\ufe0f', 6),
        # A control character ends an OSC string: what follows is on screen.
        ('\033]0;t\x18shown\a', 12),
        ('\U0001f44d\u200dabc', 5),  # a joiner hides a pictograph, not a letter
        ('日\u200d本', 4),
        ('가\ud7cb', 2),  # an archaic Hangul final
    ],
)
def test_width_edge_cases(text: str, width: int) -> None:
    assert term_styling._width(text) == width


def test_table_cell_stays_on_one_line() -> None:
    out = term_styling.table([('a\r\nb', 'x'), ('c\td', 'y'), ('e\rf', 'z')], tier_=0)
    assert out == 'a b  x\nc d  y\ne f  z'
    out = term_styling.table([('a\x85b\u2028c\x9bd\u2029e', 'x')], tier_=0)
    assert out.splitlines() == ['a b c d e  x']


def test_table_keeps_another_librarys_escapes_whole() -> None:
    # The BEL ending this hyperlink is part of the escape, not a control to scrub.
    bel = '\033]8;;http://x\aab\033]8;;\a'
    assert term_styling.table([(bel, 'x'), ('abcd', 'y')], tier_=1) == (
        f'{bel}    x\nabcd  y'
    )


def test_table_scrubs_a_broken_escape() -> None:
    # Cancelled before its end: what is left would hold the terminal in string mode
    # and swallow the cells and rows after it.
    out = term_styling.table([('\033]0;t\x18shown\a', 'x')], tier_=1)
    assert out == ' ]0;t shown   x'
    assert term_styling.table([('\033[31', 'x')], tier_=1) == ' [31  x'
    # CAN cancels a privacy message too: a terminal shows what follows it.
    cancelled = '\033^hidden\x18shown\033\\'
    lines = term_styling.table([(cancelled, 'x'), ('12345', 'y')], tier_=1).splitlines()
    assert lines[0].startswith(' ^hidden shown')
    assert [term_styling._width(line) for line in lines] == [17, 17]


def test_table_none_cell_follows_the_tier() -> None:
    assert term_styling.table([(None, 'x')], tier_=0) == '-  x'
    assert term_styling.table([(None, 'x')], tier_=1) == '—  x'


def test_progress_is_silent_when_plain(
    plain: None, capsys: pytest.CaptureFixture[str]
) -> None:
    stderr = sys.stderr
    for _ in term_styling.progress(range(3), 'sync'):
        assert sys.stderr is stderr  # nothing is wrapped either
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ('', '')


def test_progress_draws_on_stderr_and_erases(
    clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = []
    for item in term_styling.progress(['a', 'b'], 'sync', tier_=1):
        seen.append(item)
        clock.now += 2
    assert seen == ['a', 'b']
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == (
        '\rsync ' + DIM.format('━' * 30) + ' 0/2\033[K'
        '\rsync ' + '━' * 15 + DIM.format('━' * 15) + ' 1/2 eta 0:02\033[K'
        '\rsync ' + '━' * 30 + ' 2/2\033[K' + ERASE
    )


def test_progress_throttles_redraws(
    clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sum(term_styling.progress(range(1000), tier_=1)) == 499500
    assert capsys.readouterr().err.count('\r') == 2  # the first draw and the erase


def test_progress_without_a_length_counts_and_times(
    clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in term_styling.progress(iter('ab'), 'scan', tier_=1):
        clock.now += 61
    assert capsys.readouterr().err == (
        '\rscan 0 0:00\033[K\rscan 1 1:01\033[K\rscan 2 2:02\033[K' + ERASE
    )


def test_progress_erases_and_unwraps_on_break_and_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stderr = sys.stderr
    for item in term_styling.progress(range(5), tier_=1):
        assert sys.stderr is not stderr
        if item == 1:
            break
    assert sys.stderr is stderr
    assert capsys.readouterr().err.endswith(ERASE)
    with pytest.raises(ZeroDivisionError):
        for item in term_styling.progress(range(5), tier_=1):
            1 / item
    assert sys.stderr is stderr
    assert capsys.readouterr().err.endswith(ERASE)


def test_text_printed_in_the_loop_lands_above_the_bar(
    plain: None, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One terminal behind both streams, as on a real screen.
    screen = FakeTTY()
    monkeypatch.setattr(sys, 'stdout', screen)
    monkeypatch.setattr(sys, 'stderr', screen)
    for item in term_styling.progress(['a', 'b'], tier_=1):
        if item == 'a':
            print('to stdout')
            term_styling.warn('to stderr')
            print('half', end='')  # no bar may cut an unfinished line
            print(' line')
    assert sys.stdout is screen
    assert sys.stderr is screen
    bar0 = '\r' + DIM.format('━' * 30) + ' 0/2\033[K'
    assert screen.getvalue() == (
        bar0
        + ERASE + 'to stdout\n' + bar0
        + ERASE + '[!] to stderr\n' + bar0
        + ERASE + 'half line\n' + bar0
        + ERASE
    )  # fmt: skip


def test_piped_stdout_is_not_wrapped(capsys: pytest.CaptureFixture[str]) -> None:
    stdout = sys.stdout
    for _ in term_styling.progress(range(2), tier_=1):
        assert sys.stdout is stdout
        print('data')
    assert capsys.readouterr().out == 'data\ndata\n'


def test_nested_progress_draws_a_line_each(
    clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in term_styling.progress(range(1), 'outer', tier_=1):
        for _ in term_styling.progress(range(1), 'inner', tier_=1):
            clock.now += 1
        clock.now += 1
    outer0 = 'outer ' + DIM.format('━' * 30) + ' 0/1\033[K'
    inner0 = 'inner ' + DIM.format('━' * 30) + ' 0/1\033[K'
    inner1 = 'inner ' + '━' * 30 + ' 1/1\033[K'
    outer1 = 'outer ' + '━' * 30 + ' 1/1\033[K'
    up = '\r\033[1A'
    assert capsys.readouterr().err == (
        '\r' + outer0
        + '\r' + outer0 + '\n' + inner0  # the inner loop starts: a second line
        + up + outer0 + '\n' + inner1
        + up + '\033[J' + '\r' + outer0  # the inner loop ends: back to one
        + '\r' + outer1
        + ERASE
    )  # fmt: skip


def test_progress_in_threads_keeps_every_line_whole(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def work(name: str) -> None:
        for item in term_styling.progress(range(20), name, tier_=1):
            # One write per record: print() sends its newline separately, and two
            # threads' halves may interleave, as they may without progress().
            sys.stderr.write(f'{name} {item}\n')

    stderr = sys.stderr
    threads = [threading.Thread(target=work, args=(n,)) for n in ('a', 'b', 'c')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sys.stderr is stderr
    assert not term_styling._LIVE.bars
    text = re.sub(r'\r|\033\[\d*[AJK]|\033\[\d+m', '\n', capsys.readouterr().err)
    printed = [line for line in text.split('\n') if re.fullmatch(r'[abc] \d+', line)]
    assert sorted(printed) == sorted(f'{n} {i}' for n in 'abc' for i in range(20))


class HungUpTTY(FakeTTY):
    """A terminal that accepts one write and then is gone."""

    failed = 0

    def write(self, text: str) -> int:
        if self.getvalue():
            self.failed += 1
            raise OSError(5, 'Input/output error')
        return super().write(text)


def test_progress_survives_a_terminal_that_hangs_up(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = HungUpTTY()
    monkeypatch.setattr(sys, 'stderr', screen)
    # The loop runs to the end, with a redraw due after every item...
    seen = []
    for item in term_styling.progress(range(3), tier_=1):
        seen.append(item)
        clock.now += 1
    assert seen == [0, 1, 2]
    assert screen.failed == 1  # ...and a terminal that failed is not tried again
    # Its own exception is the one that surfaces.
    with pytest.raises(ZeroDivisionError):
        for item in term_styling.progress(range(3), tier_=1):
            1 / item
    assert sys.stderr is screen
    assert not term_styling._LIVE.bars


def test_progress_through_a_wrapper_around_its_own_stand_in(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Tee:
        def __init__(self, stream: object) -> None:
            self.stream = stream

        def write(self, text: str) -> int:
            return self.stream.write(text)  # ty: ignore[unresolved-attribute]

        def flush(self) -> None:
            pass

    screen = FakeTTY()
    monkeypatch.setattr(sys, 'stderr', screen)
    for _ in term_styling.progress(range(1), tier_=1):
        tee = Tee(sys.stderr)  # another library wraps whatever stderr is right now
        monkeypatch.setattr(sys, 'stderr', tee)
    assert sys.stderr is tee  # not ours to undo
    before = screen.getvalue()
    for _ in term_styling.progress(range(1), 'again', tier_=1):
        print('above', file=sys.stderr)
    assert 'again' in screen.getvalue()[len(before) :]
    assert 'above\n' in screen.getvalue()
    assert not term_styling._LIVE.bars


class Recorder(FakeTTY):
    """A terminal that notes each write and flush, in order."""

    def __init__(self, name: str, log: list) -> None:
        super().__init__()
        self.name, self.log = name, log

    def write(self, text: str) -> int:
        self.log.append((self.name, 'write', text))
        return super().write(text)

    def flush(self) -> None:
        self.log.append((self.name, 'flush', ''))


def test_stand_in_is_the_stream_for_everything_but_writing(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = Utf8TTY()
    monkeypatch.setattr(sys, 'stderr', screen)
    for _ in term_styling.progress(range(1), tier_=1):
        assert sys.stderr is not screen
        assert sys.stderr.isatty()
        assert sys.stderr.encoding == 'utf-8'
        assert term_styling.tier(sys.stderr) == term_styling.tier(screen)
        sys.stderr.writelines(['a\n', 'b\n'])
    bar0 = '\r' + DIM.format('━' * 30) + ' 0/1\033[K'
    # writelines goes above the bar like any other write.
    assert screen.getvalue() == (
        bar0 + ERASE + 'a\n' + bar0 + ERASE + 'b\n' + bar0 + ERASE
    )


def test_stand_in_writelines_keeps_lines_written_before_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def lines() -> Iterator[str]:
        yield 'started\n'
        raise RuntimeError('source failed')

    screen = FakeTTY()
    monkeypatch.setattr(sys, 'stderr', screen)
    for _ in term_styling.progress(range(1), tier_=1):
        with pytest.raises(RuntimeError, match='source failed'):
            sys.stderr.writelines(lines())
    assert 'started\n' in screen.getvalue()


def test_terminal_stdout_and_stderr_are_both_wrapped(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    log: list = []
    out, err = Recorder('out', log), Recorder('err', log)
    monkeypatch.setattr(sys, 'stdout', out)
    monkeypatch.setattr(sys, 'stderr', err)
    for _ in term_styling.progress(range(1), tier_=1):
        print('result')
    assert (sys.stdout, sys.stderr) == (out, err)
    assert out.getvalue() == 'result\n'
    bar0 = '\r' + DIM.format('━' * 30) + ' 0/1\033[K'
    # The bar goes before stdout prints and comes back after its line ends.
    assert err.getvalue() == bar0 + ERASE + bar0 + ERASE
    # One screen behind two buffers: stdout's line has to be flushed before the bar is
    # drawn again on stderr, or the two would land out of order.
    newline = log.index(('out', 'write', '\n'))
    redraw = log.index(('err', 'write', bar0), newline)
    assert ('out', 'flush', '') in log[newline:redraw]


def test_progress_without_a_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'stderr', None)  # pythonw
    assert list(term_styling.progress(range(2), tier_=1)) == [0, 1]


def test_progress_label_stays_on_one_line(
    clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in term_styling.progress(range(1), 'week\n1\tsync', tier_=1):
        pass
    assert capsys.readouterr().err.startswith('\rweek 1 sync ')


def test_bar_width_is_the_stderr_terminals(monkeypatch: pytest.MonkeyPatch) -> None:
    # `script > log` in a 40-column terminal: stdout is a file, and its "width" is not
    # the width of the screen the bar is drawn on.
    class Narrow(FakeTTY):
        def fileno(self) -> int:
            return 99

    monkeypatch.delenv('COLUMNS', raising=False)
    monkeypatch.setattr(
        os,
        'get_terminal_size',
        lambda fd: os.terminal_size((40 if fd == 99 else 200, 24)),
    )
    assert term_styling._columns(Narrow()) == 40
    assert term_styling._columns(io.StringIO()) == 80  # no terminal behind it
    monkeypatch.setenv('COLUMNS', '55')
    assert term_styling._columns(Narrow()) == 55
    monkeypatch.setenv('COLUMNS', 'wide')
    assert term_styling._columns(Narrow()) == 40
    screen = Narrow()
    monkeypatch.setattr(sys, 'stderr', screen)
    for _ in term_styling.progress(range(4), 'syncing', tier_=1):
        pass
    frames = [f for f in screen.getvalue().split('\r') if f]
    assert frames
    assert all(term_styling._width(frame) < 40 for frame in frames)


def test_bar_falls_back_when_the_stream_lacks_the_heavy_rule(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # cp437 has the light rule (which _PROBE requires) but not the heavy one.
    screen = term_styling._TTY('cp437')
    monkeypatch.setattr(sys, 'stderr', screen)
    for _ in term_styling.progress(range(1), tier_=1):
        pass
    assert '─' * 30 in screen.getvalue()
    assert '━' not in screen.getvalue()


def test_probe_is_unchanged_from_0_2() -> None:
    # A new symbol must not take tier 1 away from a stream that had it.
    assert term_styling._PROBE == '✓✗⚠→—✎≡─'


def test_probe_stands_for_every_base_symbol() -> None:
    # The probe is eight characters, not the whole table: any encoding that holds them
    # has to hold every other tier 1 and tier 2 symbol too, or mark() could still crash.
    symbols = ''.join(forms[1] + forms[2] for forms in term_styling.SYMBOLS.values())
    capable = []
    for name in sorted(set(encodings.aliases.aliases.values())):
        try:
            term_styling._PROBE.encode(name)
        except (LookupError, UnicodeEncodeError):
            continue
        symbols.encode(name)
        ('━' + '─').encode(name)
        capable.append(name)
    assert 'utf_8' in capable


@pytest.mark.parametrize(
    ('kind', 'plain', 'unicode'),
    [
        ('published', '[pub] x', '\033[32m● x\033[0m'),
        ('unpublished', '[unpub] x', '\033[2m○ x\033[0m'),
        ('held', '[held] x', '\033[35m◐ x\033[0m'),
    ],
)
def test_document_states(kind: str, plain: str, unicode: str) -> None:
    assert term_styling.mark(kind, 'x', 0) == plain
    assert term_styling.mark(kind, 'x', 1) == unicode
    glyph = term_styling.SYMBOLS[kind][2]
    assert 0xF000 <= ord(glyph) <= 0xF2E0  # Font Awesome, as the other glyphs are
    assert glyph in term_styling.mark(kind, 'x', 2)


@pytest.mark.parametrize(
    'kind',
    ['folder', 'branch', 'time', 'date', 'user', 'lock', 'cloud', 'tag', 'bug'],
)
def test_noun_icons_appear_only_with_a_nerd_font(kind: str) -> None:
    assert term_styling.mark(kind, 'x', 0) == 'x'
    assert term_styling.mark(kind, 'x', 1) == 'x'
    glyph = term_styling.SYMBOLS[kind][2]
    assert 0xF000 <= ord(glyph) <= 0xF2E0
    assert term_styling.mark(kind, 'x', 2) == f'{glyph} x'


def test_transfer_icons_have_arrows_without_a_nerd_font() -> None:
    assert term_styling.mark('download', 'x', 0) == 'x'
    assert term_styling.mark('download', 'x', 1) == '↓ x'
    assert term_styling.mark('upload', 'x', 1) == '↑ x'


def test_icon_cells_keep_a_table_aligned() -> None:
    rows = [
        (term_styling.mark('folder', 'lectures/', 2), 'x'),
        (term_styling.mark('held', 'a2.md', 2), 'x'),
        ('syllabus.md', 'x'),
    ]
    lines = term_styling.table(rows, tier_=2).splitlines()
    assert {term_styling._width(line) for line in lines} == {14}


CAPPED = ('\uee03', '\uee04', '\uee05', '\uee00', '\uee01', '\uee02')


def test_tier_2_bar_is_the_nerd_font_one_with_end_caps() -> None:
    assert term_styling.BARS[2] == CAPPED
    full_l, full, full_r, empty_l, empty, empty_r = CAPPED
    # Framed glyphs: nothing is dimmed, and each end is a cap whatever its state.
    assert term_styling._cells(CAPPED, 6, 0, 2) == empty_l + empty * 4 + empty_r
    assert (
        term_styling._cells(CAPPED, 6, 3, 2) == full_l + full * 2 + empty * 2 + empty_r
    )
    assert term_styling._cells(CAPPED, 6, 6, 2) == full_l + full * 4 + full_r
    line = term_styling._bar(1, 2, 'sync', 2, 80, 3, CAPPED)
    assert line == 'sync ' + full_l + full * 14 + empty * 14 + empty_r + ' 1/2 eta 0:03'


def test_progress_draws_this_tiers_bar(
    clock: Clock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in term_styling.progress(range(1), tier_=2):
        pass
    assert '\r\uee00' + '\uee01' * 28 + '\uee02 0/1' in capsys.readouterr().err
    # A Nerd Font older than 3.3 has no such glyphs: the documented way back.
    monkeypatch.setitem(term_styling.BARS, 2, term_styling.BARS[1])
    for _ in term_styling.progress(range(1), tier_=2):
        pass
    assert '\r' + DIM.format('━' * 30) + ' 0/1' in capsys.readouterr().err


def test_progress_with_a_bar_of_wide_glyphs(
    clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    goose = '\U0001fabf'
    seen = []
    for item in term_styling.progress(range(3), 'migrating', bar=(goose, '·'), tier_=1):
        seen.append(item)
        clock.now += 1
    frames = [
        f for f in capsys.readouterr().err.split('\r') if f.startswith('migrating')
    ]
    # Fifteen two-column cells: a goose each, or a dot and a space, so the length holds.
    assert frames[0] == 'migrating ' + '· ' * 15 + ' 0/3\033[K'
    assert frames[1] == 'migrating ' + goose * 5 + '· ' * 10 + ' 1/3 eta 0:02\033[K'
    assert frames[3] == 'migrating ' + goose * 15 + ' 3/3\033[K'
    assert term_styling._width(frames[3]) == term_styling._width(frames[0])
    assert term_styling._width(goose) == 2  # also where this Python predates the goose


@pytest.mark.parametrize(
    'bar', [(), ('#',), ('#', '.', '-'), ('#', ''), ('#', 'a\nb'), ('#', 5), '#.-']
)
def test_progress_bad_bar_fails_at_every_tier(bar: object) -> None:
    for t in range(3):
        with pytest.raises(ValueError, match='bar'):
            list(term_styling.progress(range(1), bar=bar, tier_=t))  # ty: ignore[invalid-argument-type]


def test_progress_bar_may_be_a_string_of_glyphs(
    clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in term_styling.progress(range(1), bar='#.', tier_=1):
        pass
    assert '\r' + '.' * 30 + ' 0/1' in capsys.readouterr().err


def test_bar_without_total_or_room() -> None:
    assert term_styling._bar(7, None, 'scan', 1, 80, 65) == 'scan 7 1:05'
    # An empty run does not divide.
    assert term_styling._bar(0, 0, '', 1, 80) == '0 0:00'
    # No room: no bar.
    assert term_styling._bar(1, 2, 'sync', 1, 12, 4) == 'sync 1/2'
    # An estimate that would exactly fill the line wraps: the bar takes its place.
    assert term_styling._bar(1, 2, 'sync', 1, 17, 4) == (
        'sync ' + '━' * 3 + DIM.format('━' * 4) + ' 1/2'
    )
    assert term_styling._bar(1, 2, 'sync', 1, 18, 4) == 'sync 1/2 eta 0:04'
    # A wrong total neither overflows the bar nor estimates.
    assert term_styling._bar(9, 2, '', 1, 80) == '━' * 30 + ' 9/2'
    assert term_styling._bar(0, 2, '', 1, 80, 9) == DIM.format('━' * 30) + ' 0/2'
    assert term_styling._width(term_styling._bar(1, 2, 'sync', 1, 30)) == 29


def test_skip_records_first_line(
    plain: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(term_styling, 'SKIPPED', [])
    term_styling.skip('no session\n  firefox: none\n  chrome: none')
    term_styling.skip('no token', 'upload skipped')
    assert term_styling.SKIPPED == [
        'no session -- skipping',
        'no token -- upload skipped',
    ]
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == (
        '[x] no session -- skipping\n'
        '  firefox: none\n  chrome: none\n'
        '[x] no token -- upload skipped\n'
    )


def test_error_and_warn_go_to_stderr(
    plain: None, capsys: pytest.CaptureFixture[str]
) -> None:
    term_styling.error('bad')
    term_styling.warn('odd')
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == '[x] bad\n[!] odd\n'


def test_die(plain: None, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        term_styling.die('gone', 3)
    assert exc.value.code == 3
    assert capsys.readouterr().err == '[x] gone\n'


def test_version_matches_package_metadata():
    # __version__ is a literal for vendored copies; the bump to pyproject.toml must reach it too.
    assert term_styling.__version__ == importlib.metadata.version('term-styling')


def _release_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r'\d+', version.split('+')[0])[:3])


def test_changelog_leads_with_current_version():
    # Newest entry first, so a bump without a changelog entry (or one added below an
    # older entry) fails here instead of shipping.
    changelog = (REPO / 'CHANGELOG.md').read_text(encoding='utf-8')
    # Every level-2 heading must parse, so a malformed newest entry cannot be skipped in
    # favour of an older well-formed one.
    lines = re.findall(r'^## .*$', changelog, flags=re.MULTILINE)
    assert lines, 'CHANGELOG.md has no "## X.Y.Z — YYYY-MM-DD" entry'
    headings: list[tuple[str, str]] = []
    for line in lines:
        match = re.fullmatch(r'## (\S+) — (\S+)', line)
        assert match, f'malformed CHANGELOG.md heading: {line!r}'
        date.fromisoformat(match[2])
        headings.append((match[1], match[2]))
    assert headings[0][0] == term_styling.__version__
    versions = [v for v, _ in headings]
    assert len(versions) == len(set(versions)), 'duplicate CHANGELOG.md entries'


def test_readme_names_current_version():
    readme = (REPO / 'README.md').read_text(encoding='utf-8')
    current = _release_tuple(term_styling.__version__)

    tags = re.findall(r'@v(\d+\.\d+\.\d+)', readme)
    assert tags, 'README.md no longer shows a release tag example'
    assert set(tags) == {term_styling.__version__}

    # The install snippets cap the range; a bump past the cap must move them.
    caps = re.findall(r'term-styling\s*>=\s*([\d.]+)\s*,\s*<\s*([\d.]+)', readme)
    assert caps, 'README.md no longer shows a capped install requirement'
    for low, high in caps:
        assert _release_tuple(low) <= current < _release_tuple(high), (low, high)


class FakeKernel32:
    """The two Win32 console calls _vt() makes, over a console mode held here."""

    def __init__(self, mode: int | None, settable: bool = True) -> None:
        self.mode, self.settable = mode, settable

    def GetConsoleMode(self, handle: object, mode: object) -> int:
        if self.mode is None:  # not a console
            return 0
        mode._obj.value = self.mode  # ty: ignore[unresolved-attribute]
        return 1

    def SetConsoleMode(self, handle: object, mode: int) -> int:
        if self.settable:
            self.mode = mode
        return int(self.settable)


class ConsoleTTY(Utf8TTY):
    def fileno(self) -> int:
        return 1


@pytest.fixture
def windows(clean_env: pytest.MonkeyPatch) -> Iterator[FakeKernel32]:
    ctypes = pytest.importorskip('ctypes')
    wintypes = pytest.importorskip('ctypes.wintypes')
    kernel32 = FakeKernel32(0x3)
    clean_env.setattr(sys, 'platform', 'win32')
    clean_env.setattr(
        ctypes, 'windll', type('windll', (), {'kernel32': kernel32}), raising=False
    )
    clean_env.setattr(ctypes, 'wintypes', wintypes, raising=False)
    msvcrt = type(sys)('msvcrt')
    msvcrt.get_osfhandle = lambda fd: 0x7FF000000000 + fd  # ty: ignore[unresolved-attribute]
    clean_env.setitem(sys.modules, 'msvcrt', msvcrt)
    yield kernel32


def test_windows_console_is_switched_to_escapes(windows: FakeKernel32) -> None:
    assert term_styling.tier(ConsoleTTY()) == 1
    assert windows.mode == 0x3 | 0x4  # the other mode bits are kept
    assert term_styling.tier(ConsoleTTY()) == 1  # already on: nothing to do


def test_windows_console_that_refuses_is_plain(windows: FakeKernel32) -> None:
    windows.settable = False
    assert term_styling.tier(ConsoleTTY()) == 0


def test_windows_console_is_left_alone_when_plain(windows: FakeKernel32) -> None:
    # No escapes will be sent, so the console's mode is not this module's to change.
    os.environ['NO_COLOR'] = '1'
    assert term_styling.tier(ConsoleTTY()) == 0
    assert windows.mode == 0x3


def test_windows_terminal_without_a_console_keeps_its_tier(
    windows: FakeKernel32,
) -> None:
    windows.mode = None  # GetConsoleMode fails: a pty, not a console
    assert term_styling.tier(ConsoleTTY()) == 1
    assert term_styling.tier(Utf8TTY()) == 1  # no fileno at all


@pytest.mark.skipif(sys.platform != 'win32', reason='needs a real Windows console')
def test_real_windows_console_reads_escapes() -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    try:
        console = open('CONOUT$', 'w', encoding='utf-8')
    except OSError:
        pytest.skip('this process has no console')  # ty: ignore[too-many-positional-arguments]
    with console:
        assert term_styling._vt(console)
        mode = wintypes.DWORD()
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(console.fileno()))  # ty: ignore[unresolved-attribute]
        assert ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode))  # ty: ignore[unresolved-attribute]
        assert mode.value & 0x4


def test_tier_of_a_stream_that_cannot_answer(clean_env: pytest.MonkeyPatch) -> None:
    class Replaced(io.StringIO):
        def isatty(self) -> bool:
            raise NotImplementedError

    assert term_styling.tier(Replaced()) == 0

    class NoDescriptor(FakeTTY):
        def fileno(self) -> int:
            raise NotImplementedError

    assert term_styling.tier(NoDescriptor()) == 1
    clean_env.delenv('COLUMNS', raising=False)
    assert term_styling._columns(NoDescriptor()) == 80


def test_tier_idle_shell_is_plain(clean_env: pytest.MonkeyPatch) -> None:
    shell = type('StdOutputFile', (FakeTTY,), {'__module__': 'idlelib.run'})
    assert term_styling.tier(shell()) == 0


def test_link_escapes_control_characters_in_the_url() -> None:
    out = term_styling.link('http://x/a\033\\b\ac\x9cd\ne', 'A1', 1)
    assert out == '\033]8;;http://x/a%1B\\b%07c%C2%9Cd%0Ae\033\\A1\033]8;;\033\\'
    assert term_styling._width(out) == 2
    # Tier 0 is the caller's text, byte for byte.
    assert term_styling.link('http://x/a\nb', tier_=0) == 'http://x/a\nb'


def test_link_label_equal_to_the_url_prints_once() -> None:
    assert term_styling.link('http://x', 'http://x', 0) == 'http://x'
    assert term_styling.link('http://x', '', 0) == 'http://x'


@pytest.mark.parametrize(
    ('text', 'width'),
    [
        ('co\xadop', 5),  # a soft hyphen is drawn
        ('\u0600\u0661', 2),  # Arabic number sign
        ('a\u200bb\u2060c\ufeff', 3),  # zero width space, word joiner, BOM: not drawn
    ],
)
def test_width_of_format_characters(text: str, width: int) -> None:
    assert term_styling._width(text) == width


def test_table_rule_has_no_trailing_spaces() -> None:
    lines = term_styling.table([('a', '')], ('id', ''), tier_=1).splitlines()
    assert lines[1] == '\033[2m──\033[0m'


@pytest.mark.parametrize(
    ('text', 'columns', 'clipped'),
    [
        ('uploading', 9, 'uploading'),
        ('uploading', 8, 'uploadi…'),
        ('日本語のラベル', 6, '日本…'),  # a wide character is never cut in half
        ('e\u0301e\u0301e\u0301', 2, 'e\u0301…'),  # an accent stays with its letter
        ('a b c', 3, 'a…'),  # no space before the ellipsis
        ('\033[1mbold label\033[0m', 5, 'bold…'),  # no half escape, no style left on
        ('\033[1mbold\033[0m', 4, '\033[1mbold\033[0m'),  # fits: untouched
        ('label', 1, '…'),
        ('label', 0, ''),
        ('label', -3, ''),
    ],
)
def test_clip(text: str, columns: int, clipped: str) -> None:
    assert term_styling._clip(text, columns) == clipped
    assert term_styling._width(clipped) <= max(columns, 0)


@pytest.mark.parametrize('columns', range(1, 60))
def test_progress_line_never_reaches_the_last_column(columns: int) -> None:
    label = 'uploading lecture recordings 日本語'
    for done, total in ((0, 40), (12, 40), (40, 40), (7, None)):
        line = term_styling._bar(done, total, label, 1, columns, 65)
        tail = f'{done}/{total}' if total else f'{done} 1:05'
        assert line.endswith(tail) or ' eta ' in line
        # Where even the count does not fit there is nothing left to cut.
        assert term_styling._width(line) < max(columns, len(tail) + 1)


def test_empty_write_does_not_redraw(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = FakeTTY()
    monkeypatch.setattr(sys, 'stderr', screen)
    for _ in term_styling.progress(range(1), tier_=1):
        before = screen.getvalue()
        assert sys.stderr.write('') == 0
        assert screen.getvalue() == before
        print('half', end='', file=sys.stderr)
        sys.stderr.write('')  # ...and does not end a half-printed line
        assert term_styling._LIVE.midline
        print(file=sys.stderr)


def test_repository_urls_name_this_repository() -> None:
    urls = importlib.metadata.metadata('term-styling').get_all('Project-URL') or []
    assert 'Repository, https://github.com/codydunne/term_styling' in urls
    readme = (REPO / 'README.md').read_text(encoding='utf-8')
    assert set(re.findall(r'github\.com/codydunne/(\w+)', readme)) == {'term_styling'}
