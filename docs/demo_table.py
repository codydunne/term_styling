"""Everything a table cell can hold, recorded in demo-table.webp by make_demo.py."""

import term_styling
from term_styling import link, mark, paint, table

# A state of the script's own, as in demo.py.
term_styling.SYMBOLS['held'] = ('[~]', '◐', '')
term_styling.COLORS['held'] = '35'


def page(name: str) -> str:
    """The assignment's id, clickable on a terminal and followed by its URL in a pipe."""
    return link(f'https://x.org/{name.lower()}', name)


rows = [
    (page('A1'), 'Scatterplots', 10, mark('ok', 'in sync')),
    (page('A2'), 'Color scales', 10, mark('new', 'created')),
    (page('A3'), 'Networks', 12, mark('warn', 'points differ')),
    (page('A4'), 'Maps', 8, mark('held', 'in review')),
    (page('A5'), 'Uncertainty', 100, mark('fail', 'rejected')),
    # None is the `none` symbol at every tier, so no later column can shift left.
    (page('A6'), paint('withdrawn', 'dim'), None, mark('none', 'skipped')),
]

print(paint('Assignments', 'head'))
# One `l` or `r` per column. Cells are measured without their escapes, so the colored
# and linked ones line up; piped, the same call prints plain records.
print(table(rows, headers=('id', 'title', 'points', 'state'), align='llr'))
