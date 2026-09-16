"""Name the square artifacts: find connected patches of exposed same-material surface and
score how rectangular each one is.

    fill = area / bounding-box area      1.00 = a perfect filled rectangle
    A natural blob sits around 0.55-0.75. Anything >= 0.85 with area >= 9 is a stamp.

Usage: python tools/patch_shapes.py surface.pkl [min_area]
"""
import sys, pickle, collections
import numpy as np

d = pickle.load(open(sys.argv[1] if len(sys.argv) > 1 else 'surface.pkl', 'rb'))
MIN = int(sys.argv[2]) if len(sys.argv) > 2 else 6
Y, G, A, BI = d['Y'], d['G'], d['A'], d['BI']
S, BN = d['states'], d['biomes']
H, W = Y.shape
valid = Y > -999
short = lambda i: S[i].split(':')[1].split('[')[0]
kind = np.array([short(i) for i in range(len(S))])[np.maximum(G, 0)]

def components(mask):
    """4-connected components, iterative flood fill."""
    seen = np.zeros(mask.shape, bool)
    out = []
    for sz, sx in np.argwhere(mask):
        if seen[sz, sx]:
            continue
        stack = [(sz, sx)]; seen[sz, sx] = True; cells = []
        while stack:
            z, x = stack.pop(); cells.append((z, x))
            for dz, dx in ((1,0),(-1,0),(0,1),(0,-1)):
                nz, nx = z+dz, x+dx
                if 0 <= nz < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[nz,nx] and not seen[nz,nx]:
                    seen[nz,nx] = True; stack.append((nz,nx))
        out.append(cells)
    return out

rows = []
for k in np.unique(kind[valid]):
    m = valid & (kind == k)
    if m.sum() < MIN:
        continue
    for cells in components(m):
        if len(cells) < MIN:
            continue
        zs = [c[0] for c in cells]; xs = [c[1] for c in cells]
        h = max(zs)-min(zs)+1; w = max(xs)-min(xs)+1
        fill = len(cells)/(h*w)
        bio = BN[BI[cells[0]]].split(':')[1]
        rows.append((fill, len(cells), h, w, k, bio, min(xs)+d['x0'], min(zs)+d['z0']))

rows.sort(reverse=True)
stamps = [r for r in rows if r[0] >= 0.85 and r[1] >= 9]
print(f'patches >= {MIN} blocks: {len(rows)}   rectangular stamps (fill >= 0.85, area >= 9): {len(stamps)}')
print()
by = collections.Counter((r[4], r[5]) for r in stamps)
print('WORST OFFENDERS (material, biome, count of rectangular patches):')
for (k, bio), n in by.most_common(12):
    ex = next(r for r in stamps if r[4] == k and r[5] == bio)
    print(f'  {n:4d}  {k:18s} {bio:22s} e.g. {ex[2]}x{ex[3]} fill {ex[0]:.2f} at x={ex[6]} z={ex[7]}')
print()
print('median fill by material (all patches, lower = more natural):')
bym = collections.defaultdict(list)
for r in rows: bym[r[4]].append(r[0])
for k, v in sorted(bym.items(), key=lambda kv: -np.median(kv[1]))[:14]:
    print(f'  {k:18s} n={len(v):5d}  median fill {np.median(v):.2f}  max {max(v):.2f}')
