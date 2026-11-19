"""The script recorded in demo-tier{0,1,2}.webp, run at every tier by make_demo.py."""

import time

import term_styling
from term_styling import change, divider, link, mark, paint, progress, table

# A state of the script's own: mark() and paint() pick it up at every tier.
term_styling.SYMBOLS['held'] = ('[~]', '◐', '\uf04c')
term_styling.COLORS['held'] = '35'

results = {
    'A1': [mark('ok', 'A1 in sync')],
    'A2': [mark('new', 'A2 created')],
    'A3': [
        mark('warn', 'A3 points differ'),
        change('points', 10, 12),
        change('due_at', '2026-09-17 23:59', '2026-09-18 23:59'),
    ],
    'A4': [mark('held', 'A4 held for review')],
    'A5': [mark('fail', 'A5 rejected: 409 Conflict')],
    'A6': [],
}

print(paint('Syncing 6 assignments', 'head'))
print(divider())
# One line on stderr while stderr is a terminal, erased when the loop ends. Everything
# printed meanwhile, on either stream, lands above it.
for name, lines in progress(results.items(), 'syncing'):
    time.sleep(0.4)  # stands in for the request
    if name == 'A5':
        term_styling.warn('rate limit at 80%')
    if name == 'A6':
        term_styling.skip('A6: no API_TOKEN\n  tried $API_TOKEN, ~/.config/sync/token')
    for line in lines:
        print(line)
print(mark('info', link('https://example.org/a3', 'A3')))
print(
    '   ',
    mark('edit', link('https://example.org/a3/edit', 'edit')),
    mark('file', link('file:///srv/a3.md', 'a3.md')),
)
print(divider())
# Styled cells still line up; piped, the same call is plain records.
print(
    table(
        [
            ('A1', 10, mark('ok', 'in sync')),
            ('A2', 10, mark('new', 'created')),
            ('A3', 12, mark('warn', 'updated')),
            ('A4', 8, mark('held', 'in review')),
            ('A5', None, mark('fail', 'rejected')),
            ('A6', None, mark('none', 'skipped')),
        ],
        headers=('id', 'points', 'state'),
        align='lr',
    )
)
print(paint('skipped:', 'head'), *term_styling.SKIPPED, sep='\n  ')
# One red line on stderr, then exit: the run's status is the caller's to act on.
term_styling.die('1 rejected, 1 skipped', code=1)
