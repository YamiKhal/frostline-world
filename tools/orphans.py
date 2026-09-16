"""Reachability scan over worldgen features.

validate.py only walks features reachable FROM biomes, so a feature nobody
references is invisible to it. This finds those.
"""
import os, json, re, io, sys

ROOT = 'data'
files = {}
for dp, _, fn in os.walk(ROOT):
    for f in fn:
        if f.endswith('.json'):
            p = os.path.join(dp, f).replace(os.sep, '/')
            files[p] = io.open(p, encoding='utf-8').read()

FEAT = re.compile(r'data/([a-z_]+)/worldgen/(configured_feature|placed_feature)/(.+)\.json$')

def fid(p):
    m = FEAT.match(p)
    return (m.group(2), '%s:%s' % (m.group(1), m.group(3))) if m else None

cf, pf = {}, {}
for p in files:
    r = fid(p)
    if r:
        (cf if r[0] == 'configured_feature' else pf)[r[1]] = p

def ids(s):
    return set(re.findall(r'"([a-z_]+:[a-z_0-9/]+)"', s))

# roots: ids named by any file that is not itself a feature (biomes, structures, ...)
roots = set()
for p, s in files.items():
    if not fid(p):
        roots |= ids(s)

seen, queue = set(), [i for i in roots if i in cf or i in pf]
while queue:
    i = queue.pop()
    if i in seen:
        continue
    seen.add(i)
    p = cf.get(i) or pf.get(i)
    if p:
        for j in ids(files[p]):
            if (j in cf or j in pf) and j not in seen:
                queue.append(j)

orph = sorted([(k, cf[k]) for k in cf if k not in seen] +
              [(k, pf[k]) for k in pf if k not in seen])
print('configured %d  placed %d  reachable %d  ORPHANS %d'
      % (len(cf), len(pf), len(seen), len(orph)))
for k, p in orph:
    print('  ' + p)
if '--delete' in sys.argv:
    for k, p in orph:
        os.remove(p)
    print('deleted %d' % len(orph))
