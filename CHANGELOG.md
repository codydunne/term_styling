# Changelog

## 0.3.0 — 2026-11-19

- Added `table()`: aligned columns that are borderless records at tier 0, with a bold header and dim rule on a terminal. Styled cells, East Asian wide characters, and multi-codepoint emoji are measured by display width. Control characters in a cell become spaces.
- Added `progress()`: progress lines on stderr with an estimate of the time left, silent at tier 0. Text printed inside the loop appears above the line; loops may nest or run in threads.
- `sys.stderr` (and a terminal `sys.stdout`) is wrapped while a `progress()` loop runs at tier 1 or 2, and restored afterwards.
- Added document states `published`, `unpublished`, and `held`, and thing states `folder`, `branch`, `time`, `date`, `user`, `lock`, `cloud`, `tag`, `bug`, `download`, and `upload`. The thing states print an icon at tier 2 and the text alone below it (`download` and `upload` have arrows at tier 1). A script that defined its own `held` keeps its definition, since `SYMBOLS.update()` overrides the base.
- Tier detection is unchanged from 0.2: the encoding probe did not grow.

## 0.2.1 — 2026-11-17

Initial public release.