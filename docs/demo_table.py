"""A document listing, recorded in demo-table-tier{0,1,2}.webp by make_demo.py.

The same call three ways: a terminal, a terminal with a Nerd Font (the icons for
folders, files, people, and times have no Unicode sign to fall back on), and a pipe.
"""

from term_styling import mark, paint, table


def doc(
    kind: str, name: str, owner: str, edited: str, words: object, state: str
) -> tuple:
    """One row: every cell but the count carries an icon where the terminal has one."""
    states = {
        'published': mark('published', 'published'),
        'unpublished': mark('unpublished', 'unpublished'),
        'held': mark('held', 'held for review'),
        'broken': mark('fail', 'broken link'),
    }
    return (
        mark(kind, name),
        mark('user', owner),
        mark('time', edited),
        words,
        states[state],
    )


rows = [
    doc('folder', 'lectures/', 'cody', '09-18 09:12', None, 'published'),
    doc('file', 'syllabus.md', 'cody', '09-02 14:30', 1840, 'published'),
    doc('file', 'a1-scatterplots.md', 'kim', '09-17 23:59', 620, 'published'),
    doc('file', 'a2-color.md', 'kim', '09-18 08:05', 540, 'held'),
    doc('lock', 'grades.csv', 'cody', '09-18 09:10', None, 'unpublished'),
    doc('bug', 'a3-networks.md', 'sam', '09-16 11:41', 71, 'broken'),
]

print(
    paint('Course site', 'head'),
    mark('branch', 'main'),
    mark('cloud', 'x.org/ds4200'),
    mark('tag', 'fall-2026'),
    sep='  ',
)
# One `l` or `r` per column. Cells are measured without their escapes and with their
# icons, so every tier lines up; None is the `none` symbol, so no column shifts left.
print(
    table(rows, headers=('document', 'owner', 'edited', 'words', 'state'), align='lllr')
)
