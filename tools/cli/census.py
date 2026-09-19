"""Count marker blocks per biome in a generated dimension.

Answers "did this feature actually generate?" without flying. Give it block
substrings; it reports counts and how many chunks of each biome were scanned.
Usage: python tools/cli/census.py <region dir> <substr> [substr ...]
"""
import sys, os, glob, collections
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from anvil import World, region_chunks

region = sys.argv[1]
wanted = [w.lower() for w in sys.argv[2:]] or ['spruce_wood']
w = World()
hits = collections.Counter()
biome_chunks = collections.Counter()
per_biome = collections.defaultdict(collections.Counter)
nch = 0

for rf in sorted(glob.glob(os.path.join(region, '*.mca'))):
    for c in region_chunks(rf):
        if c.get('Status') not in ('minecraft:full', 'full'):
            continue
        nch += 1
        blocks, biomes = w.chunk(c)
        # dominant surface biome of the chunk
        bcount = collections.Counter(biomes.ravel().tolist())
        dom = w.biome_list[bcount.most_common(1)[0][0]].split(':')[-1]
        biome_chunks[dom] += 1
        present = np.unique(blocks)
        for sid in present:
            name = w.state_list[sid]
            base = name.split('[')[0].split(':')[-1]
            for pat in wanted:
                if base == pat:
                    n = int((blocks == sid).sum())
                    hits[pat] += n
                    per_biome[pat][dom] += n

print('scanned %d full chunks in %d regions' % (nch, len(glob.glob(os.path.join(region, '*.mca')))))
print('\nchunks by dominant biome:')
for b, n in biome_chunks.most_common():
    print('   %-28s %d' % (b, n))
print('\nmarker blocks:')
for pat in wanted:
    n = hits[pat]
    flag = '' if n else '   <-- ZERO'
    print('   %-26s %8d%s' % (pat, n, flag))
    for b, k in per_biome[pat].most_common(6):
        print('        %-22s %d' % (b, k))
