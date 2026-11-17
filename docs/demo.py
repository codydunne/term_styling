"""The script recorded in demo.webp, run at every tier by make_demo.py."""

import term_styling
from term_styling import change, divider, link, mark, paint

# A state of the script's own: mark() and paint() pick it up at every tier.
term_styling.SYMBOLS['held'] = ('[~]', '◐', '')
term_styling.COLORS['held'] = '35'

print(paint('Syncing 6 assignments', 'head'))
print(divider())
print(mark('ok', 'A1 in sync'))
print(mark('new', 'A2 created'))
print(mark('warn', 'A3 points differ'))
print(change('points', 10, 12))
print(change('due_at', '2026-09-17 23:59', '2026-09-18 23:59'))
print(mark('held', 'A4 held for review'))
print(mark('fail', 'A5 rejected: 409 Conflict'))
print(mark('info', link('https://example.org/a3', 'A3')))
print(
    '   ',
    mark('edit', link('https://example.org/a3/edit', 'edit')),
    mark('file', link('file:///srv/a3.md', 'a3.md')),
)
term_styling.warn('rate limit at 80%')
term_styling.skip('A6: no API_TOKEN\n  tried $API_TOKEN, ~/.config/sync/token')
print(divider())
print(paint('skipped:', 'head'), *term_styling.SKIPPED, sep='\n  ')
