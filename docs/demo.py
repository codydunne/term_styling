"""The script recorded in demo-tier{0,1,2}.webp, run at every tier by make_demo.py.

One run of a made-up course-site sync that uses everything in the module: states and
things, changes, links, nested progress lines with output above them, a table, and the
stderr helpers.
"""

import time

import term_styling
from term_styling import change, divider, link, mark, paint, progress, table

# A state of the script's own: mark() and paint() pick it up at every tier.
term_styling.SYMBOLS['late'] = ('[late]', '◔', '')
term_styling.COLORS['late'] = '33'

SITE = 'https://x.org/ds4200'
# What syncing each document reports, by folder.
FOLDERS = {
    'lectures': {
        'syllabus.md': [mark('ok', 'syllabus.md in sync')],
        'schedule.md': [mark('new', 'schedule.md created')],
    },
    'homework': {
        'a1-scatterplots.md': [mark('late', 'a1-scatterplots.md published late')],
        'a2-color.md': [
            mark('warn', 'a2-color.md points differ'),
            change('points', 10, 12),
            change('due_at', '2026-09-17 23:59', '2026-09-18 23:59'),
        ],
        'a3-networks.md': [mark('fail', 'a3-networks.md rejected: 409 Conflict')],
        'grades.csv': [],
    },
}

# Things beside the title: icons with a Nerd Font, the text alone without one.
print(
    paint('Syncing course site', 'head'),
    mark('branch', 'main'),
    mark('cloud', 'x.org/ds4200'),
    mark('tag', 'fall-2026'),
    sep='  ',
)
print(divider())
# A line per loop on stderr while it is a terminal, erased when the loop ends. Whatever
# prints meanwhile, on either stream, lands above the lines.
for folder, documents in progress(FOLDERS.items(), 'sections'):
    for name, lines in progress(documents.items(), folder):
        time.sleep(0.4)  # stands in for the request
        if name == 'a3-networks.md':
            term_styling.warn('rate limit at 80%')
        if name == 'grades.csv':
            term_styling.skip(
                f'{name}: no API_TOKEN\n  tried $API_TOKEN, ~/.config/sync'
            )
        for line in lines:
            print(line)
print(mark('info', link(f'{SITE}/a2-color', 'a2-color.md')))
print(
    '   ',
    mark('edit', link(f'{SITE}/a2/edit', 'edit')),
    mark('file', link('file:///srv/a2-color.md', 'source')),
)
print(divider())

# (thing, name, owner, edited, words, state, what the state says)
DOCUMENTS = [
    ('folder', 'lectures/', 'cody', '09-18 09:12', None, 'published', 'published'),
    ('file', 'syllabus.md', 'cody', '09-02 14:30', 1840, 'published', 'published'),
    ('file', 'a2-color.md', 'connor', '09-18 08:05', 540, 'held', 'held for review'),
    ('lock', 'grades.csv', 'cody', '09-18 09:10', None, 'unpublished', 'unpublished'),
    ('bug', 'a3-networks.md', 'eduardo', '09-16 11:41', 71, 'fail', 'broken link'),
]
# An icon in every cell that names a thing, where there is a font for it. Styled cells
# still line up, and None is the `none` symbol; piped, the same call is plain records.
print(
    table(
        [
            (
                mark(kind, name),
                mark('user', who),
                mark('time', when),
                words,
                mark(s, says),
            )
            for kind, name, who, when, words, s, says in DOCUMENTS
        ],
        headers=('document', 'owner', 'edited', 'words', 'state'),
        align='lllr',
    )
)
print(paint('skipped:', 'head'), *term_styling.SKIPPED, sep='\n  ')
# One red line on stderr, then exit: the run's status is the caller's to act on.
term_styling.die('1 rejected, 1 skipped', code=1)
