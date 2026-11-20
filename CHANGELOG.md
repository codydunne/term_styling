# Changelog

## 0.3.1 — 2026-11-19

- Windows: a console that is not yet reading escapes (a legacy `conhost.exe` window) is switched to virtual-terminal processing when a stream is given tier 1 or 2, and gets tier 0 if it refuses. Before, the escapes printed literally there. A character device that is no console (`NUL`, a serial port) gets tier 0 as well.
- IDLE's shell gets tier 0: it claims to be a terminal and prints escapes literally.
- `progress()`: a label too wide for the terminal is cut short with `…` instead of wrapping, which left a copy of the line behind on every redraw. (A terminal too narrow for the count itself still wraps.)
- `progress()`: an empty write to the wrapped streams (click probes a stream with one) no longer redraws the lines.
- `link()`: on a terminal, control characters in the URL are percent-encoded, in the escape and in the URL shown when there is no label, so they cannot end the escape early; at tier 0 a label equal to the URL prints the URL once.
- `table()`: the soft hyphen and the Arabic number signs count as one column, as terminals draw them, not zero; the rule under the header no longer ends in spaces when the last column is empty.
- A replaced stream whose `isatty()` raises `NotImplementedError` or `OSError` is treated as not a terminal, and one whose `fileno()` raises `NotImplementedError` is measured as 80 columns, instead of the exception reaching the caller.
- Fixed the repository URL in the package metadata and the README's install-from-git lines (`term_style` for `term_styling`).

## 0.3.0 — 2026-11-19

- Added `table()`: aligned columns that are borderless records at tier 0, with a bold header and dim rule on a terminal. Styled cells, East Asian wide characters, and multi-codepoint emoji are measured by display width. Control characters in a cell become spaces.
- Added `progress()`: progress lines on stderr with an estimate of the time left, silent at tier 0. Text printed inside the loop appears above the line; loops may nest or run in threads.
- The bar is drawn from `BARS`: a heavy rule at tier 1 and Fira Code's framed progress glyphs at tier 2, which need Nerd Fonts 3.3 or newer (3.1 for the Fira Code Nerd Font). `progress(bar=...)` replaces them for one loop with two glyphs or six, each one or two columns wide.
- `sys.stderr` (and a terminal `sys.stdout`) is wrapped while a `progress()` loop runs at tier 1 or 2, and restored afterwards.
- Added document states `published`, `unpublished`, and `held`, and thing states `folder`, `branch`, `time`, `date`, `user`, `lock`, `cloud`, `tag`, `bug`, `download`, and `upload`. The thing states print an icon at tier 2 and the text alone below it (`download` and `upload` have arrows at tier 1). A script that defined its own `held` keeps its definition, since `SYMBOLS.update()` overrides the base.
- Tier detection is unchanged from 0.2: the encoding probe did not grow.

## 0.2.1 — 2026-11-17

Initial public release.