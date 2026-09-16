"""Print the datapack layout and flag anything sitting outside the convention.

Convention (see REF.md 11):
    worldgen/<registry>/<zone>/<purpose>/<name>
    zone = q r1 r2 r3 r4 r5 shared
"""
import os, glob, collections, re, json, sys

ZONES = ('q','r1','r2','r3','r4','r5','rc','shared')
bad = []
counts = collections.Counter()
for reg in ('configured_feature','placed_feature','structure','structure_set','template_pool'):
    base = 'data/frostline/worldgen/%s' % reg
    if not os.path.isdir(base): continue
    for root,_,fn in os.walk(base):
        for f in fn:
            if not f.endswith('.json'): continue
            rel = os.path.join(root,f).replace(os.sep,'/')[len(base)+1:]
            parts = rel.split('/')
            counts['%s/%s' % (reg, parts[0] if parts[0] in ZONES else '??')] += 1
            if parts[0] not in ZONES or len(parts) < 2:
                bad.append('%s/%s' % (reg, rel))
base='data/frostline/tags/worldgen/biome/has_structure'
for root,_,fn in os.walk(base):
    for f in fn:
        rel = os.path.join(root,f).replace(os.sep,'/')[len(base)+1:]
        if rel.split('/')[0] not in ZONES or '/' not in rel:
            bad.append('tags/has_structure/'+rel)

for k in sorted(counts):
    print('  %-34s %d' % (k, counts[k]))
print()
if bad:
    print('OUTSIDE CONVENTION (%d):' % len(bad))
    for b in bad: print('   ', b)
    sys.exit(1)
print('OK: every worldgen file is <zone>/<purpose>/<name>')
