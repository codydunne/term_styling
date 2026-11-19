from __future__ import annotations

import importlib.metadata
import io
import os
import re
import shutil
import subprocess
import sys
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
