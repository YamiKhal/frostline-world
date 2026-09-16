"""Reorganise worldgen ids into <zone>/<purpose>/<name>.

A file's path IS its resource id, so every move has to rewrite every reference.
Run with --apply to move files and rewrite; without it, prints the plan.
"""
import json, glob, os, re, sys, io, collections

ZONE = {'zone_zero':'q','zone_one':'r1','zone_two':'r2',
        'zone_three':'r3','zone_four':'r4','zone_five':'r5'}

def ids(s): return set(re.findall(r'"([a-z_]+:[a-z_0-9/]+)"', s))

files = {}
for dp,_,fn in os.walk('data'):
    for f in fn:
        if f.endswith('.json'):
            p = os.path.join(dp,f).replace(os.sep,'/')
            files[p] = io.open(p,encoding='utf-8').read()

FEAT = re.compile(r'data/([a-z_]+)/worldgen/(configured_feature|placed_feature)/(.+)\.json$')
cf, pf = {}, {}
for p in files:
    m = FEAT.match(p)
    if m: (cf if m.group(2)=='configured_feature' else pf)['%s:%s'%(m.group(1),m.group(3))] = p

z2b = {}
for p in (x.replace(os.sep,'/') for x in glob.glob('data/frostline/dimension/*.json')):
    z2b[os.path.basename(p)[:-5]] = set(re.findall(r'"(frostline:[a-z_0-9]+)"', files[p]))
b2z = collections.defaultdict(set)
for z,bs in z2b.items():
    for b in bs: b2z[b].add(z)

def closure(start):
    seen, q = set(), [start]
    while q:
        i = q.pop()
        if i in seen: continue
        seen.add(i)
        p = cf.get(i) or pf.get(i)
        if p:
            for j in ids(files[p]):
                if j in cf or j in pf: q.append(j)
    return seen

feat2zone = collections.defaultdict(set)
feat2biome = collections.defaultdict(set)
for bp in (x.replace(os.sep,'/') for x in glob.glob('data/frostline/worldgen/biome/*.json')):
    bid = 'frostline:'+os.path.basename(bp)[:-5]
    for i in ids(files[bp]):
        if i in cf or i in pf:
            for f in closure(i):
                feat2zone[f] |= b2z.get(bid,set()); feat2biome[f].add(bid)

# ---- purpose rules -------------------------------------------------------
def split(fid):
    body = fid.split(':',1)[1]
    parts = body.split('/')
    # tree/r1/x -> tree, x   |   snow/normal/x -> snow, normal/x
    if parts[0] in ('tree','snow','sculk','pond','cave','boulder'):
        rest = parts[1:]
        if rest and rest[0] in ('r1','r2','r3','r4','r5'): rest = rest[1:]
        return parts[0], '/'.join(rest)
    if parts[0] == 'frozen_lake': return 'lake', 'frozen'
    if parts[0] == 'quiet':       return 'terrain', '/'.join(parts[1:])
    leaf = parts[-1]
    for suf, pur in (('_boulder','boulder'),):
        if leaf.endswith(suf): return pur, leaf[:-len(suf)]
    if leaf.startswith('rubble_'):   return 'rubble', leaf[len('rubble_'):]
    if leaf.startswith('ice_') or leaf in ('icicle','rare_ice_spike'):
        return 'ice', leaf.replace('ice_','').replace('rare_spike','rare_spike') if leaf!='icicle' else 'icicle'
    if leaf.endswith('_needle'):     return 'spire', leaf[:-len('_needle')]
    if leaf in ('grass_meadow','meadow_flowers','sparse_grass'): return 'plant', leaf
    return 'misc', leaf

moves = {}
for fid in sorted(set(cf) | set(pf)):
    zones = feat2zone.get(fid, set())
    if len(zones) == 1:   key = ZONE[next(iter(zones))]
    elif len(zones) > 1:  key = 'shared'
    else:                 # unreferenced by any live dimension; place by biome name
        bs = feat2biome.get(fid, set())
        key = 'r1' if any(b.split(':')[1].startswith('snowy_') for b in bs) else 'shared'
    pur, leaf = split(fid)
    moves[fid] = 'frostline:%s/%s/%s' % (key, pur, leaf)

if '--plan' in sys.argv:
    for old in sorted(moves, key=lambda k: (moves[k], k)):
        if moves[old] != old:
            print('%-42s -> %s' % (old, moves[old]))
    print('\n%d features, %d move' % (len(moves), sum(1 for k,v in moves.items() if k!=v)))
    sys.exit()
json.dump(moves, open('tools/_moves.json','w'), indent=1)
print('wrote tools/_moves.json (%d entries)' % len(moves))

# ---- structures, structure sets, tags: <zone>_<name> -> <zone>/<name> ----
ZKEY = ('q','r1','r2','r3','r4','r5','rc')
def zsplit(stem):
    for z in ZKEY:
        if stem.startswith(z+'_'): return '%s/%s' % (z, stem[len(z)+1:])
    return None

extra = {}
for reg, pre in (('structure','frostline:'), ('structure_set','frostline:')):
    for p in glob.glob('data/frostline/worldgen/%s/*.json' % reg):
        stem = os.path.basename(p)[:-5]
        n = zsplit(stem)
        if n: extra[pre+stem] = (pre+n, 'data/frostline/worldgen/%s/%s.json' % (reg, stem),
                                 'data/frostline/worldgen/%s/%s.json' % (reg, n))
for p in glob.glob('data/frostline/tags/worldgen/biome/has_structure/*.json'):
    stem = os.path.basename(p)[:-5]
    n = zsplit(stem)
    if n:
        extra['frostline:has_structure/'+stem] = (
            'frostline:has_structure/'+n,
            'data/frostline/tags/worldgen/biome/has_structure/%s.json' % stem,
            'data/frostline/tags/worldgen/biome/has_structure/%s.json' % n)
json.dump({k:v[0] for k,v in extra.items()}, open('tools/_moves_extra.json','w'), indent=1)
json.dump({k:[v[1],v[2]] for k,v in extra.items()}, open('tools/_moves_extra_paths.json','w'), indent=1)
print('extra: %d structures/sets/tags' % len(extra))
