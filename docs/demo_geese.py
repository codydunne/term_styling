"""A progress bar drawn with the script's own glyphs, recorded in demo-geese.webp."""

import time

from term_styling import mark, progress

# (done, to do): a glyph may be two columns wide, and the bar keeps its length.
for _ in progress(range(15), 'migrating', bar=('🪿', '·')):
    time.sleep(0.25)  # stands in for the flight
print(mark('ok', '15 geese landed'))
