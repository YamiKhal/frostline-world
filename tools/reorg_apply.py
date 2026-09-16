"""Apply the reorg plan: move files, then rewrite every reference.

A file's path is its resource id, so the two must happen together.
Replacement is done with ONE regex alternation, longest id first, so a short id
can never eat a longer one that contains it (frostline:.../spruce vs .../spruce_grand).
"""
import json, os, re, io, glob, shutil, sys

feat  = json.load(open('tools/_moves.json'))
extra = json.load(open('tools/_moves_extra.json'))
paths = json.load(open('tools/_moves_extra_paths.json'))

FEATREG = ('configured_feature','placed_feature')
moves = []          # (old_path, new_path)
idmap = {}

for old, new in feat.items():
    if old == new: continue
    idmap[old] = new
    for reg in FEATREG:
        op = 'data/frostline/worldgen/%s/%s.json' % (reg, old.split(':',1)[1])
        np = 'data/frostline/worldgen/%s/%s.json' % (reg, new.split(':',1)[1])
        if os.path.exists(op): moves.append((op, np))
for old, new in extra.items():
    idmap[old] = new
    op, np = paths[old]
    if os.path.exists(op): moves.append((op, np))

# longest first so prefixes cannot be clobbered
keys = sorted(idmap, key=len, reverse=True)
rx = re.compile('|'.join(re.escape(k) for k in keys))
def rewrite(s): return rx.sub(lambda m: idmap[m.group(0)], s)

targets = []
for root, _, fn in os.walk('data'):
    for f in fn:
        if f.endswith('.json'): targets.append(os.path.join(root,f).replace(os.sep,'/'))
targets += [x.replace(os.sep,'/') for x in glob.glob('docs/*.md')]

changed = 0
for p in targets:
    s = io.open(p, encoding='utf-8').read()
    n = rewrite(s)
    if n != s:
        io.open(p,'w',encoding='utf-8').write(n); changed += 1

for op, np in moves:
    os.makedirs(os.path.dirname(np), exist_ok=True)
    shutil.move(op, np)

# prune empty dirs
for _ in range(6):
    for root, dirs, fn in os.walk('data', topdown=False):
        if not dirs and not fn:
            os.rmdir(root)

print('rewrote %d files, moved %d' % (changed, len(moves)))
