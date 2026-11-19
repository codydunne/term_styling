# Changelog

## 0.3.0 — 2026-11-19

- Added `table()`: aligned columns that are borderless records at tier 0, with a bold header and dim rule on a terminal. Styled cells, East Asian wide characters, and multi-codepoint emoji are measured by display width. Control characters in a cell become spaces.
- Added `progress()`: progress lines on stderr with an estimate of the time left, silent at tier 0. Text printed inside the loop appears above the line; loops may nest or run in threads.
- `sys.stderr` (and a terminal `sys.stdout`) is wrapped while a `progress()` loop runs at tier 1 or 2, and restored afterwards.
- Tier detection is unchanged from 0.2: the encoding probe did not grow.

## 0.2.1 — 2026-11-17

Initial public release.