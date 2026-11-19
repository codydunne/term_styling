# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow", "fonttools"]
# ///
"""Record the demo animations: `uv run docs/make_demo.py` (Linux/macOS, needs a pty).

    uv run docs/make_demo.py                      # all four, into docs/
    uv run docs/make_demo.py demo-tier2           # only the ones named
    uv run docs/make_demo.py --out /tmp/media     # somewhere else

One animation per tier, demo-tier{0,1,2}.webp, from demo.py, and demo-geese.webp from
demo_geese.py. They are not kept on main: .github/workflows/media.yml runs this on a
release tag and replaces the `media` branch, which the README links to.

Runs each script under a real pseudo-terminal once per command and replays the captured
output with its timing, so the progress line animates. Only what term_styling emits is
parsed: SGR codes, OSC 8 links, and the carriage return, cursor-up, and erase codes of
progress(). Measured gaps are rounded to STEP ms, so a run that prints the same thing
again writes the same bytes; the progress recordings can still differ, since their
estimates come from the real clock.

The NERD_FONT scenes need a Nerd Font: JetBrainsMono Nerd Font Mono is found via fc-list
or, under WSL, in the Windows font folders (where pwsh's Install-NerdFont puts it), or
set DEMO_FONT and DEMO_FONT_BOLD to another's files. It has to be Nerd Fonts 3.3 or newer
(3.1 for Fira Code), which is where the tier 2 progress bar's glyphs arrived. Characters
that font lacks are drawn from DejaVu Sans Mono, and emoji from Noto Color Emoji (found
via fc-list, or set DEMO_FONT_EMOJI); a character none of them has is an error.
"""

import argparse
import codecs
import os
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
COLS, ROWS, FONT_SIZE, PAD = 72, 30, 30, 30
DEJAVU = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono'
FAMILY = 'JetBrainsMono Nerd Font Mono'
EMOJI = 'Noto Color Emoji'
BG, FG = (30, 30, 36), (220, 220, 220)
SGR = {
    '31': (240, 98, 98),
    '32': (120, 210, 120),
    '33': (230, 200, 90),
    '35': (200, 140, 240),
    '36': (90, 200, 220),
}
PROMPT, COMMENT = (130, 170, 255), (120, 120, 135)
TOKEN = re.compile(
    r'\x1b\[(\d+)m|\x1b\]8;;([^\x1b]*)\x1b\\|\x1b\[(\d*)([AJK])|\r?\n|\r|[^\x1b\r\n]'
)
# An escape sequence cut off by the end of a read: held back until the rest arrives.
PARTIAL = re.compile(r'\x1b(?:\[\d*|\]8;;[^\x1b]*\x1b?)?$')
# Output arriving within this many seconds is one frame: a burst of print() calls is not
# worth a frame each, and a redraw is never split from its erase.
BURST = 0.05
# Measured gaps are shown rounded to this many ms: wall-clock jitter would otherwise
# change every file on every run.
STEP = 50
# One animation per file, written to <name>.webp. Each scene starts on a cleared screen:
# (typed command, comment shown after it) pairs. The log file keeps the redirect scene
# deterministic: `| cat` would race cat against stderr.
ANIMATIONS = {
    'demo-tier0': [
        [('NO_COLOR=1 uv run python demo.py', '# tier 0 on both streams')],
        [
            ('uv run python demo.py > sync.log', '# stderr is still a terminal'),
            ('cat sync.log', '# stdout was not: tier 0'),
        ],
    ],
    'demo-tier1': [[('uv run python demo.py', '# a terminal: tier 1')]],
    'demo-tier2': [
        [('NERD_FONT=1 uv run python demo.py', '# tier 2: Nerd Font glyphs')]
    ],
    'demo-geese': [
        [('NERD_FONT=1 uv run python demo_geese.py', '# any glyph can be the bar')]
    ],
}


def find_font(style: str, family: str = FAMILY) -> str:
    """`family`'s file in `style` from fontconfig, else WSL's Windows font folders, else ''."""
    try:
        out = subprocess.run(
            ['fc-list', '-f', '%{file}\n', f'{family}:style={style}'],
            capture_output=True,
            text=True,
        ).stdout
    except FileNotFoundError:
        out = ''
    windows = [
        *Path('/mnt/c/Users').glob('*/AppData/Local/Microsoft/Windows/Fonts'),
        Path('/mnt/c/Windows/Fonts'),
    ]
    name = f'{family.replace(" ", "")}-{style}'
    found = [f for d in windows for f in sorted(d.glob(f'{name}.[ot]tf'))]
    return next(iter([*out.splitlines(), *map(str, found)]), '')


def capture(cmd: str, cwd: str) -> list:
    """`cmd`'s stdout+stderr as a terminal sees them: (seconds from start, text) bursts.

    term_styling is imported from src/.
    """
    master, slave = pty.openpty()
    env = {**os.environ, 'COLUMNS': str(COLS), 'PYTHONPATH': str(HERE.parent / 'src')}
    for k in ('NO_COLOR', 'CI', 'NERD_FONT'):
        env.pop(k, None)
    env['TERM'] = 'xterm-256color'
    cmd = cmd.replace('python', sys.executable, 1)
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd, shell=True, cwd=cwd, env=env, stdout=slave, stderr=slave
    )
    os.close(slave)
    # A character or an escape sequence can straddle two reads, and so two bursts.
    decode = codecs.getincrementaldecoder('utf-8')().decode
    held = ''
    bursts: list = []  # [first byte's time, last byte's time, text]
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:  # EIO once the child closes the pty
            break
        if not chunk:
            break
        now = time.monotonic() - start
        text = held + decode(chunk)
        cut = PARTIAL.search(text)
        held = text[cut.start() :] if cut else ''
        text = text[: len(text) - len(held)]
        if bursts and now - bursts[-1][1] < BURST:
            bursts[-1][1] = now
            bursts[-1][2] += text
        else:
            bursts.append([now, now, text])
    decode(b'', final=True)  # raises on output cut off mid-character
    assert not held, f'{cmd}: output ended inside an escape sequence: {held!r}'
    proc.wait()
    os.close(master)
    return [(at, text) for at, _, text in bursts]


class Screen:
    """Lines of (char, color, dim, bold, underline) cells, and a cursor writing them."""

    def __init__(self) -> None:
        self.lines: list = [[]]
        self.row = self.col = 0
        self.style = {'color': FG, 'dim': False, 'bold': False, 'link': False}

    def feed(self, text: str) -> None:
        style = self.style
        for m in TOKEN.finditer(text):
            code, url, count, move, tok = m.group(1, 2, 3, 4, 0)
            line = self.lines[self.row]
            if code is not None:
                if code == '0':
                    style.update(color=FG, dim=False, bold=False)
                elif code == '1':
                    style['bold'] = True
                elif code == '2':
                    style['dim'] = True
                else:
                    style['color'] = SGR[code]
            elif url is not None:
                style['link'] = bool(url)
            elif move == 'A':
                self.row = max(0, self.row - int(count or 1))
            elif move:  # K erases to the end of the line, J the lines below as well
                del line[self.col :]
                if move == 'J':
                    del self.lines[self.row + 1 :]
            elif tok == '\r':
                self.col = 0
            elif tok.endswith('\n'):
                self.row, self.col = self.row + 1, 0
                if self.row == len(self.lines):
                    self.lines.append([])
            else:
                cell = (tok, style['color'], style['dim'], style['bold'], style['link'])
                # A wide character (an emoji) fills two columns: its cell and a None.
                wide = unicodedata.east_asian_width(tok) in ('W', 'F')
                line[self.col : self.col + 1 + wide] = [cell, None][: 1 + wide]
                self.col += 1 + wide

    def type(self, line: list) -> None:
        """A whole line at the cursor, as if entered at a prompt."""
        self.lines[self.row :] = [line, []]
        self.row, self.col = self.row + 1, 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Record the demo animations.')
    parser.add_argument(
        'names',
        nargs='*',
        metavar='name',
        help=f'animations to record (default: all): {", ".join(ANIMATIONS)}',
    )
    parser.add_argument(
        '--out', type=Path, default=HERE, help='directory to write to (default: docs/)'
    )
    args = parser.parse_args()
    unknown = sorted(set(args.names) - set(ANIMATIONS))
    if unknown:
        parser.error(f'no such animation: {", ".join(unknown)}')
    chosen = {n: s for n, s in ANIMATIONS.items() if not args.names or n in args.names}
    font = os.environ.get('DEMO_FONT') or find_font('Regular')
    bold = os.environ.get('DEMO_FONT_BOLD') or find_font('Bold') or font
    if not font:
        sys.exit(
            f'{FAMILY} not found: install it or set DEMO_FONT (and DEMO_FONT_BOLD)'
        )
    fonts = {
        False: ImageFont.truetype(font, FONT_SIZE),
        True: ImageFont.truetype(bold, FONT_SIZE),
    }
    fallback = {
        False: ImageFont.truetype(f'{DEJAVU}.ttf', FONT_SIZE),
        True: ImageFont.truetype(f'{DEJAVU}-Bold.ttf', FONT_SIZE),
    }
    covered = set(TTFont(font).getBestCmap())
    drawable = covered | set(TTFont(f'{DEJAVU}.ttf').getBestCmap())
    # Emoji come from a color font, found only if a demo prints one. Noto Color Emoji
    # holds bitmaps at one size, so each glyph is drawn there and scaled to two columns.
    emoji_file = os.environ.get('DEMO_FONT_EMOJI') or find_font('Regular', EMOJI)
    emoji_face = ImageFont.truetype(emoji_file, 109) if emoji_file else None
    emoji_has = set(TTFont(emoji_file).getBestCmap()) if emoji_file else set()
    tiles: dict = {}
    cw = fonts[False].getlength('M')
    ch = round(FONT_SIZE * 1.35)
    size = (round(COLS * cw) + 2 * PAD, ROWS * ch + 2 * PAD)

    def tile(c: str) -> Image.Image:
        """The emoji `c` as an image two columns wide."""
        if c not in tiles:
            box = emoji_face.getbbox(c)
            glyph = Image.new('RGBA', (box[2], box[3]), (0, 0, 0, 0))
            ImageDraw.Draw(glyph).text((0, 0), c, font=emoji_face, embedded_color=True)
            scale = min(2 * cw / glyph.width, ch / glyph.height)
            tiles[c] = glyph.resize(
                (round(glyph.width * scale), round(glyph.height * scale)), Image.LANCZOS
            )
        return tiles[c]

    def draw(lines: list) -> Image.Image:
        img = Image.new('RGB', size, BG)
        d = ImageDraw.Draw(img)
        for y, row in enumerate(lines):
            for x, cell in enumerate(row):
                if cell is None:  # the second column of a wide character
                    continue
                c, color, dim, bold, ul = cell
                if dim:
                    color = tuple((a + b) // 2 for a, b in zip(color, BG))
                px, py = PAD + round(x * cw), PAD + y * ch
                if ord(c) not in drawable and ord(c) in emoji_has:
                    img.paste(tile(c), (px, py + (ch - tile(c).height) // 2), tile(c))
                    continue
                if ord(c) not in drawable:
                    sys.exit(
                        f'{font}, DejaVu Sans Mono, and {emoji_file or "no emoji font"}'
                        f' all lack U+{ord(c):04X}'
                    )
                face = (fonts if ord(c) in covered else fallback)[bold]
                d.text((px, py), c, font=face, fill=color)
                if ul:
                    uy = py + FONT_SIZE + 3
                    d.line((px, uy, px + round(cw), uy), fill=color, width=2)
        return img

    with tempfile.TemporaryDirectory() as cwd:
        for script in HERE.glob('demo*.py'):
            shutil.copy(script, cwd)
        # Render every tier before saving any, so a missing glyph leaves no file half-updated.
        renders = {name: render(scenes, draw, cwd) for name, scenes in chosen.items()}
    args.out.mkdir(parents=True, exist_ok=True)
    for name, (frames, durations, rows) in renders.items():
        # Each animation is as tall as its own longest screen, not the tallest of all.
        box = (0, 0, size[0], rows * ch + 2 * PAD)
        save(args.out / f'{name}.webp', [f.crop(box) for f in frames], durations)
    return 0


def render(scenes: list, draw, cwd: str) -> tuple:
    """Frames, their durations in ms, and the rows used: each command typed, then run."""
    frames, durations, tallest = [], [], 1
    for scene in scenes:
        screen = Screen()
        for cmd, comment in scene:
            prompt = [
                ('$', PROMPT, False, True, False),
                (' ', FG, False, False, False),
            ]
            typed = [(c, FG, False, False, False) for c in cmd]
            typed += [(c, COMMENT, False, False, False) for c in '  ' + comment]
            for i in range(0, len(typed) + 1, 2):  # two characters a frame
                frames.append(draw([*screen.lines[: screen.row], prompt + typed[:i]]))
                durations.append(500 if i == 0 else 40)
            screen.type(prompt + typed)
            bursts = capture(cmd, cwd)
            for i, (at, text) in enumerate(bursts):
                screen.feed(text)
                rows = len(screen.lines) - (not screen.lines[-1])
                assert rows <= ROWS, f'{cmd}: {rows} lines > {ROWS} rows'
                tallest = max(tallest, rows)
                assert all(len(r) <= COLS for r in screen.lines), (
                    f'{cmd}: a line > {COLS} cols'
                )
                frames.append(draw(screen.lines))
                # Each burst stays up until the next one arrived; the last, to be read.
                if i + 1 < len(bursts):
                    gap = (bursts[i + 1][0] - at) * 1000
                    durations.append(max(STEP, round(gap / STEP) * STEP))
                else:
                    durations.append(4500 if cmd == scene[-1][0] else 1500)
    return frames, durations, tallest


def save(out_path: Path, frames: list, durations: list) -> None:
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        lossless=True,
    )
    print(
        f'wrote {out_path} ({out_path.stat().st_size // 1024} KiB, {len(frames)} frames)'
    )


if __name__ == '__main__':
    sys.exit(main())
