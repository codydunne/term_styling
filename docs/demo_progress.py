"""Several progress lines at once, recorded in demo-progress-tier{0,1,2}.webp by make_demo.py."""

import time
from collections.abc import Iterator

from term_styling import mark, paint, progress, warn

COURSES = {'DS 4200': 5, 'CS 7250': 8, 'DS 5500': 3}


def audit_log() -> Iterator[int]:
    """A stream with no length: its line counts and shows the time elapsed."""
    yield from range(40)


print(paint('Syncing 3 courses', 'head'))
# Nested loops get a line each, outermost first, with an estimate of the time left.
for course, count in progress(COURSES.items(), 'courses'):
    for number in progress(range(1, count + 1), course):
        time.sleep(0.35)  # stands in for the request
        # Whatever prints meanwhile, on either stream, lands above the lines.
        if (course, number) == ('CS 7250', 3):
            print(mark('warn', 'CS 7250 A3 points differ'))
        if (course, number) == ('CS 7250', 6):
            warn('rate limit at 80%')
    print(mark('ok', f'{course}: {count} assignments'))
for _ in progress(audit_log(), 'audit log'):
    time.sleep(0.03)
# Every line is gone once its loop ends: the outcome is the script's to print.
print(mark('ok', '16 assignments in sync, 40 audit records read'))
