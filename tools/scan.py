"""Flatten a dimension's generated chunks into surface grids.

Usage: python tools/scan.py <save>/dimensions/frostline/zone_one/region [surface.pkl]
Then: python tools/lone_blocks.py surface.pkl
"""
import sys, os, glob, collections, pickle
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from anvil import World, region_chunks

REGION = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else 'surface.pkl'

w = World()
AIR = w.sid('minecraft:air')

def not_ground(name):
    n = name.split('[')[0]
    base = n.split(':')[1]
    if base in ('air', 'cave_air', 'snow', 'short_grass', 'grass', 'tall_grass', 'fern', 'large_fern',
                'dead_bush', 'sweet_berry_bush', 'vine', 'powder_snow'):
        return True
    for k in ('leaves', '_log', '_wood', 'sapling', 'flower', 'tulip', 'orchid', 'daisy', 'poppy',
              'dandelion', 'allium', 'bluet', 'cornflower', 'lily', 'bush', 'moss_carpet', 'carpet',
              'button', 'torch', 'pile', 'icicle', 'lichen', 'root', 'mushroom'):
        if k in base:
            return True
    return False

cols = {}
full = 0
for path in sorted(glob.glob(os.path.join(REGION, '*.mca'))):
    for c in region_chunks(path):
        if c.get('Status') != 'minecraft:full':
            continue
        full += 1
        blocks, biomes = w.chunk(c)
        lut = np.array([not_ground(s) for s in w.state_list], dtype=bool)
        ng = lut[blocks]                      # [y,z,x]
        g = ~ng
        flipped = g[::-1]
        top = blocks.shape[0] - 1 - np.argmax(flipped, axis=0)   # [z,x]
        has = g.any(axis=0)
        zz, xx = np.mgrid[0:16, 0:16]
        gid = blocks[top, zz, xx]
        above = blocks[np.minimum(top + 1, 383), zz, xx]
        below = blocks[np.maximum(top - 1, 0), zz, xx]
        airid = np.array([n in ('minecraft:air','minecraft:cave_air') for n in w.state_list], dtype=bool)[blocks]
        ttop = blocks.shape[0] - 1 - np.argmax(~airid[::-1], axis=0)
        tid = blocks[ttop, zz, xx]
        bio = biomes[np.minimum(top // 4, 95), zz // 4, xx // 4]
        cols[(c['xPos'], c['zPos'])] = (np.where(has, top - 64, -999), gid, above, below, bio, tid)

print('full chunks', full)
xs = [k[0] for k in cols]; zs = [k[1] for k in cols]
x0, z0 = min(xs), min(zs)
W = (max(xs) - x0 + 1) * 16; H = (max(zs) - z0 + 1) * 16
Y = np.full((H, W), -999, np.int32); G = np.full((H, W), -1, np.int32)
A = np.full((H, W), -1, np.int32); B = np.full((H, W), -1, np.int32); BI = np.full((H, W), -1, np.int32); T = np.full((H, W), -1, np.int32)
for (cx, cz), (y, gid, ab, be, bio, tid) in cols.items():
    sz = (cz - z0) * 16; sx = (cx - x0) * 16
    Y[sz:sz+16, sx:sx+16] = y; G[sz:sz+16, sx:sx+16] = gid
    A[sz:sz+16, sx:sx+16] = ab; B[sz:sz+16, sx:sx+16] = be; BI[sz:sz+16, sx:sx+16] = bio; T[sz:sz+16, sx:sx+16] = tid
pickle.dump(dict(T=T, Y=Y, G=G, A=A, B=B, BI=BI, x0=x0 * 16, z0=z0 * 16, states=w.state_list, biomes=w.biome_list), open(OUT, 'wb'))
print('saved', OUT, Y.shape)
