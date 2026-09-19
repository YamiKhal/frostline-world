"""Generate boulder .nbt files for data/frostline/structures/r1/.

Rocks are jittered ellipsoids. Only solid blocks are recorded - no air entries -
so the template never carves a box out of the terrain around it (Terralith's own
small_decoration/boulder.nbt records 137 air blocks and does exactly that).

Layer y=0 lands LEVEL WITH the surface block (REF.md 9.3), so the widest part of
the rock is put at y=0 and the piece reads as bedded rather than perched.
"""
import os, sys, math, random
sys.path.insert(0, os.path.dirname(__file__))
from nbtwrite import write_structure

OUT = 'data/frostline/structures/r1'

# (name, rx, ry, rz, [(block, weight), ...], seed)
SPEC = [
    ('boulder_1', 1.6, 1.2, 1.5, [('minecraft:stone', 3), ('minecraft:andesite', 2)], 11),
    ('boulder_2', 1.5, 1.4, 1.7, [('minecraft:cobblestone', 3), ('minecraft:mossy_cobblestone', 2)], 22),
    ('boulder_3', 2.0, 2.0, 1.9, [('minecraft:stone', 4), ('minecraft:andesite', 2),
                                  ('minecraft:cobblestone', 1)], 33),
    ('boulder_4', 2.2, 2.4, 2.0, [('minecraft:mossy_cobblestone', 3), ('minecraft:cobblestone', 3)], 44),
    ('boulder_5', 2.4, 2.8, 2.3, [('minecraft:stone', 4), ('minecraft:cobblestone', 2),
                                  ('minecraft:andesite', 2)], 55),
    ('boulder_6', 2.7, 3.4, 2.6, [('minecraft:mossy_cobblestone', 3), ('minecraft:cobblestone', 2),
                                  ('minecraft:andesite', 1)], 66),
]

def build(name, rx, ry, rz, mats, seed):
    rnd = random.Random(seed)
    pal = [m for m, _ in mats]
    weights = [w for _, w in mats]
    nx, ny, nz = int(math.ceil(rx)) * 2 + 1, int(math.ceil(ry)) * 2 + 1, int(math.ceil(rz)) * 2 + 1
    cx, cz = nx // 2, nz // 2
    blocks = []
    for y in range(ny):
        # y=0 is the widest slice and sits level with the ground; taper upward only
        fy = (y) / float(ry + 0.5)
        if fy > 1.0:
            continue
        shrink = math.sqrt(max(0.0, 1.0 - (fy * 0.78) ** 2))
        for x in range(nx):
            for z in range(nz):
                dx, dz = (x - cx) / (rx * shrink + 1e-6), (z - cz) / (rz * shrink + 1e-6)
                d = dx * dx + dz * dz
                d += rnd.uniform(-0.18, 0.18)          # ragged edge
                if d <= 1.0:
                    blocks.append(((x, y, z), rnd.choices(range(len(pal)), weights)[0]))
    ys = max(p[1] for p, _ in blocks) + 1
    write_structure(os.path.join(OUT, name + '.nbt'), (nx, ys, nz), pal, blocks)
    return nx, ys, nz, len(blocks)

if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for spec in SPEC:
        nx, ny, nz, n = build(*spec)
        print('%-10s  %dx%dx%d   %3d blocks' % (spec[0], nx, ny, nz, n))
