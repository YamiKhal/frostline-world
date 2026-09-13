"""Report lone surface blocks from a scan.py grid: the "single block with nothing around it" check.

Exposed = air directly above the ground block (no snow layer, no plant).
Lone    = exposed and no exposed block of the same kind among its 8 neighbours.
Canopy  = something (leaves, snow on leaves) higher up in the same column.
"""
import sys, pickle, collections
import numpy as np

d = pickle.load(open(sys.argv[1] if len(sys.argv) > 1 else 'surface.pkl', 'rb'))
Y, G, A, BI, T = d['Y'], d['G'], d['A'], d['BI'], d['T']
S, BN = d['states'], d['biomes']
H, W = Y.shape
valid = Y > -999
air = [i for i, s in enumerate(S) if s == 'minecraft:air']
short = lambda i: S[i].split(':')[1].split('[')[0]
kind = np.array([short(i) for i in range(len(S))])[np.maximum(G, 0)]
exposed = valid & np.isin(A, air)

lone = np.zeros_like(exposed)
for k in np.unique(kind[exposed]):
    m = exposed & (kind == k)
    P = np.pad(m, 1)
    n = sum(P[1 + a:1 + a + H, 1 + b:1 + b + W].astype(int) for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b)
    lone |= m & (n == 0)

canopy = lone & ~np.isin(T, air) & (T != G)
print(f'columns {valid.sum()}  exposed {exposed.sum()}  lone {lone.sum()}  lone under canopy {canopy.sum()}')
by = collections.Counter((BN[BI[z, x]].split(':')[1], kind[z, x], bool(canopy[z, x])) for z, x in np.argwhere(lone))
for (biome, block, under), n in by.most_common(20):
    print(f'  {n:6d}  {biome:24s} {block:16s} {"canopy" if under else "open sky"}')
pts = np.argwhere(lone)
print('samples x,y,z:', [(int(x + d['x0']), int(Y[z, x]), int(z + d['z0'])) for z, x in pts[::max(1, len(pts) // 8)]][:8])
