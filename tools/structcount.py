"""Count structure STARTS recorded in generated chunks.

Exact: reads chunk.structures.starts, so it does not depend on a marker block.
Usage: python tools/structcount.py <region dir>
"""
import sys, os, glob, collections
sys.path.insert(0, os.path.dirname(__file__))
from anvil import region_chunks

region = sys.argv[1]
starts = collections.Counter()
refs = collections.Counter()
nch = 0
for rf in sorted(glob.glob(os.path.join(region, '*.mca'))):
    for c in region_chunks(rf):
        if c.get('Status') not in ('minecraft:full', 'full'):
            continue
        nch += 1
        st = c.get('structures') or c.get('Structures') or {}
        for k, v in (st.get('starts') or {}).items():
            if isinstance(v, dict) and v.get('id') not in (None, 'INVALID'):
                starts[k] += 1
        for k, v in (st.get('References') or st.get('references') or {}).items():
            if v:
                refs[k] += 1
print('full chunks: %d' % nch)
print('\nstructure STARTS (chunks where a structure begins):')
for k, n in starts.most_common():
    print('   %-42s %6d   1 per %.0f chunks' % (k, n, nch / n))
if not starts:
    print('   (none)')
