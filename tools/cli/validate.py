"""Load-time checks for the datapack, without launching the game.

    python tools/cli/validate.py

Catches, in about a second, the four things that otherwise cost a full launch:

  1. every frostline reference resolves on disk - biome feature steps, carvers, placed ->
     configured, selector entries (which hold PLACED features, a classic silent break),
     patch inner features, structures -> pools -> .nbt, sets -> structures, biome tags
  2. feature-order cycles: the real topological sort vanilla does over each biome's
     per-step lists. A cycle is a hard crash at world load.
  3. every JSON parses
  4. new surface blocks that are missing from #frostline:drift_ground / #patch_replaceable,
     which is a silent "snow and patches ignore this block"

Exit code is non-zero if anything failed, so it can gate a deploy.
"""
import json, glob, os, sys, collections

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)

problems = []
def bad(msg):
    problems.append(msg)

def load(path):
    try:
        return json.loads(open(path, 'rb').read().decode('utf-8'))
    except Exception as e:
        bad('%s does not parse: %s' % (path, str(e)[:80]))
        return None

def need(path, what):
    if not os.path.exists(path):
        bad('missing %s (referenced as %s)' % (path, what))

def as_path(fid, kind, ext='json'):
    ns, p = fid.split(':', 1)
    return 'data/%s/%s/%s.%s' % (ns, kind, p, ext), ns

# ---- 1. every JSON parses ------------------------------------------------
docs = {}
for f in glob.glob('data/**/*.json', recursive=True):
    d = load(f)
    if d is not None:
        docs[f.replace('\\', '/')] = d

# ---- 2. references -------------------------------------------------------
def check_feature(fid, kind):
    path, ns = as_path(fid, 'worldgen/' + kind)
    if ns == 'frostline':
        need(path, fid)

for f, d in docs.items():
    if '/worldgen/configured_feature/' in f:
        cfg = d.get('config', {}) or {}
        t = d.get('type')
        if t == 'minecraft:random_selector':
            for e in cfg.get('features', []):
                if isinstance(e.get('feature'), str):
                    check_feature(e['feature'], 'placed_feature')
            if isinstance(cfg.get('default'), str):
                check_feature(cfg['default'], 'placed_feature')
        if t == 'minecraft:simple_random_selector':
            for e in cfg.get('features', []):
                if isinstance(e, str):
                    check_feature(e, 'placed_feature')
        for key in ('feature', 'vegetation_feature'):
            inner = cfg.get(key)
            if isinstance(inner, dict) and isinstance(inner.get('feature'), str):
                check_feature(inner['feature'], 'configured_feature')
    if '/worldgen/placed_feature/' in f and isinstance(d.get('feature'), str):
        check_feature(d['feature'], 'configured_feature')
    if '/worldgen/structure/' in f:
        if isinstance(d.get('start_pool'), str):
            check_feature(d['start_pool'], 'template_pool')
        b = d.get('biomes')
        if isinstance(b, str) and b.startswith('#'):
            p, ns = as_path(b[1:], 'tags/worldgen/biome')
            need(p, b)
    if '/worldgen/template_pool/' in f:
        for e in d.get('elements', []):
            loc = e.get('element', {}).get('location')
            if isinstance(loc, str):
                p, ns = as_path(loc, 'structures', 'nbt')
                need(p, loc)
    if '/worldgen/structure_set/' in f:
        for s in d.get('structures', []):
            if isinstance(s.get('structure'), str):
                check_feature(s['structure'], 'structure')

# ---- 3. biome steps, carvers, and the feature-order sort -----------------
edges = collections.defaultdict(set)
nodes = set()
for f, d in docs.items():
    if '/worldgen/biome/' not in f:
        continue
    for step in d.get('features', []):
        for i, x in enumerate(step):
            nodes.add(x)
            edges[x].update(step[i + 1:])
            check_feature(x, 'placed_feature')
    for lst in (d.get('carvers') or {}).values():
        for c in lst:
            check_feature(c, 'configured_carver')

colour = {}
def visit(n, stack):
    colour[n] = 1
    for m in edges.get(n, ()):
        if colour.get(m) == 1:
            bad('feature-order cycle: %s' % ' -> '.join(stack + [n, m]))
            return True
        if colour.get(m) is None and visit(m, stack + [n]):
            return True
    colour[n] = 2
    return False
for n in list(nodes):
    if colour.get(n) is None:
        visit(n, [])

# ---- 4. surface blocks must be known to snow and to patches --------------
def tag_values(name):
    d = docs.get('data/frostline/tags/blocks/%s.json' % name, {})
    return set(d.get('values', []))

drift, patch = tag_values('drift_ground'), tag_values('patch_replaceable')
surface_blocks = set()
for f, d in docs.items():
    if '/worldgen/noise_settings/' not in f:
        continue
    s = json.dumps(d.get('surface_rule', {}))
    import re
    surface_blocks |= set(re.findall(r'"Name": "([a-z_]+:[a-z_]+)"', s))
for b in sorted(surface_blocks):
    if b in ('minecraft:air', 'minecraft:water', 'minecraft:lava', 'minecraft:bedrock'):
        continue
    if b not in drift:
        bad('surface block %s is not in #frostline:drift_ground (snow will ignore it)' % b)
    if b not in patch:
        bad('surface block %s is not in #frostline:patch_replaceable' % b)

# ---- report --------------------------------------------------------------
print('checked %d JSON files, %d placed features referenced by biomes' % (len(docs), len(nodes)))
if problems:
    print('\nFAILED (%d):' % len(problems))
    for p in problems[:40]:
        print('  -', p)
    if len(problems) > 40:
        print('  ... and %d more' % (len(problems) - 40))
    sys.exit(1)
print('OK: references resolve, no feature-order cycle, surface blocks tagged')
