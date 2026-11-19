# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow", "fonttools"]
# ///
"""Regenerate docs/demo-tier{0,1,2}.webp: `uv run docs/make_demo.py` (Linux/macOS, needs a pty).

Runs demo.py under a real pseudo-terminal once per command and replays the captured output
with its timing, so the progress line animates. Only what term_styling emits is parsed: SGR
codes, OSC 8 links, and the carriage return, cursor-up, and erase codes of progress(). The NERD_FONT scene needs
a Nerd Font: JetBrainsMono Nerd Font Mono is found via fc-list or, under WSL, in the
Windows font folders (where pwsh's Install-NerdFont puts it), or set DEMO_FONT and
DEMO_FONT_BOLD to another's files. Characters that font lacks are drawn from DejaVu Sans
Mono; a character neither has is an error.
"""

import codecs
import os
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
COLS, ROWS, FONT_SIZE, PAD = 72, 26, 30, 30
DEJAVU = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono'
FAMILY = 'JetBrainsMono Nerd Font Mono'
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
# Output arriving within this many seconds is one frame: a burst of print() calls is not
# worth a frame each, and a redraw is never split from its erase.
BURST = 0.05
# One animation per tier, written to demo-tier<N>.webp. Each scene starts on a cleared
# screen: (typed command, comment shown after it) pairs. The log file keeps the redirect
# scene deterministic: `| cat` would race cat against stderr.
ANIMATIONS = {
    0: [
        [('NO_COLOR=1 uv run python demo.py', '# tier 0 on both streams')],
        [
            ('uv run python demo.py > sync.log', '# stderr is still a terminal'),
            ('cat sync.log', '# stdout was not: tier 0'),
        ],
    ],
    1: [[('uv run python demo.py', '# a terminal: tier 1')]],
    2: [[('NERD_FONT=1 uv run python demo.py', '# tier 2: Nerd Font glyphs')]],
}


def find_font(style: str) -> str:
    """FAMILY's file in `style` from fontconfig, else from WSL's Windows font folders, else ''."""
    try:
        out = subprocess.run(
            ['fc-list', '-f', '%{file}\n', f'{FAMILY}:style={style}'],
            capture_output=True,
            text=True,
        ).stdout
    except FileNotFoundError:
        out = ''
    windows = [
        *Path('/mnt/c/Users').glob('*/AppData/Local/Microsoft/Windows/Fonts'),
        Path('/mnt/c/Windows/Fonts'),
    ]
    name = f'{FAMILY.replace(" ", "")}-{style}'
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
    # A character can straddle two reads, and so two bursts.
    decode = codecs.getincrementaldecoder('utf-8')().decode
    bursts: list = []  # [first byte's time, last byte's time, text]
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:  # EIO once the child closes the pty
            break
        if not chunk:
            break
        now = time.monotonic() - start
        if bursts and now - bursts[-1][1] < BURST:
            bursts[-1][1] = now
            bursts[-1][2] += decode(chunk)
        else:
            bursts.append([now, now, decode(chunk)])
    decode(b'', final=True)  # raises on output cut off mid-character
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
                line[self.col : self.col + 1] = [cell]
                self.col += 1

    def type(self, line: list) -> None:
        """A whole line at the cursor, as if entered at a prompt."""
        self.lines[self.row :] = [line, []]
        self.row, self.col = self.row + 1, 0


def main() -> int:
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
    cw = fonts[False].getlength('M')
    ch = round(FONT_SIZE * 1.35)
    size = (round(COLS * cw) + 2 * PAD, ROWS * ch + 2 * PAD)

    def draw(lines: list) -> Image.Image:
        img = Image.new('RGB', size, BG)
        d = ImageDraw.Draw(img)
        for y, row in enumerate(lines):
            for x, (c, color, dim, bold, ul) in enumerate(row):
                if dim:
                    color = tuple((a + b) // 2 for a, b in zip(color, BG))
                px, py = PAD + round(x * cw), PAD + y * ch
                if ord(c) not in drawable:
                    sys.exit(f'{font} and DejaVu Sans Mono both lack U+{ord(c):04X}')
                face = (fonts if ord(c) in covered else fallback)[bold]
                d.text((px, py), c, font=face, fill=color)
                if ul:
                    uy = py + FONT_SIZE + 3
                    d.line((px, uy, px + round(cw), uy), fill=color, width=2)
        return img

    with tempfile.TemporaryDirectory() as cwd:
        shutil.copy(HERE / 'demo.py', cwd)
        # Render every tier before saving any, so a missing glyph leaves no file half-updated.
        renders = {n: render(scenes, draw, cwd) for n, scenes in ANIMATIONS.items()}
    for n, (frames, durations) in renders.items():
        save(HERE / f'demo-tier{n}.webp', frames, durations)
    return 0


def render(scenes: list, draw, cwd: str) -> tuple:
    """Frames and their durations in ms: each command typed out, then its output."""
    frames, durations = [], []
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
                assert all(len(r) <= COLS for r in screen.lines), (
                    f'{cmd}: a line > {COLS} cols'
                )
                frames.append(draw(screen.lines))
                # Each burst stays up until the next one arrived; the last, to be read.
                if i + 1 < len(bursts):
                    durations.append(max(20, round((bursts[i + 1][0] - at) * 1000)))
                else:
                    durations.append(4500 if cmd == scene[-1][0] else 1500)
    return frames, durations


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
