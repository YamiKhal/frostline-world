#!/usr/bin/env python3
"""Self-contained tests for litematic2nbt. Run: python tools/test_litematic2nbt.py

Builds synthetic .litematic files -- including the cases a hand-saved schematic
rarely covers, like negative region sizes and two overlapping regions -- and
checks the converted structure cell by cell. No game, no fixtures.
"""
import os, shutil, sys, tempfile
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nbtio
import litematic2nbt as L

FAILS = []


def check(cond, label):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILS.append(label)


# --- building a .litematic ----------------------------------------------

def pack(indices, bits):
    """Inverse of L.unpack_states: LSB-first, entries may straddle longs."""
    total = len(indices) * bits
    n_longs = (total + 63) // 64
    buf = [0] * n_longs
    for i, v in enumerate(indices):
        start = i * bits
        lo, off = start >> 6, start & 63
        buf[lo] |= (v << off) & 0xFFFFFFFFFFFFFFFF
        if off + bits > 64:
            buf[lo + 1] |= v >> (64 - off)
    # NBT longs are signed.
    return [b - (1 << 64) if b >= (1 << 63) else b for b in buf]


def state(name, props=None):
    d = OrderedDict([('Name', nbtio.string(name))])
    if props:
        d['Properties'] = nbtio.comp(OrderedDict(
            (k, nbtio.string(v)) for k, v in props.items()))
    return ('comp', d)


def xyz(vals, kind='i'):
    return nbtio.comp(OrderedDict((k, (kind, v)) for k, v in zip('xyz', vals)))


def comps(values):
    """A TAG_List of compounds, accepting either ('comp', d) values or bare dicts."""
    return ('list', (10, [v if isinstance(v, tuple) else ('comp', v) for v in values]))


def region(pos, size, palette, cells, tiles=(), entities=()):
    """cells: {(x,y,z) local index -> palette index}, local index space is
    always 0..abs(size)-1 on each axis, exactly as Litematica stores it."""
    dims = [abs(s) for s in size]
    w, h, l = dims
    flat = [0] * (w * h * l)
    for (x, y, z), pi in cells.items():
        flat[y * w * l + z * w + x] = pi
    d = OrderedDict([
        ('Position', xyz(pos)),
        ('Size', xyz(size)),
        ('BlockStatePalette', comps(palette)),
        ('BlockStates', ('la', pack(flat, L.bits_for(len(palette))))),
        ('TileEntities', comps(tiles)),
        ('Entities', comps(entities)),
        ('PendingBlockTicks', nbtio.comp_list([])),
        ('PendingFluidTicks', nbtio.comp_list([])),
    ])
    return ('comp', d)


def write_litematic(path, regions, data_version=3465):
    root = ('comp', OrderedDict([
        ('MinecraftDataVersion', nbtio.i32(data_version)),
        ('Version', nbtio.i32(6)),
        ('SubVersion', nbtio.i32(1)),
        ('Metadata', nbtio.comp(OrderedDict([
            ('Name', nbtio.string('test')),
            ('Author', nbtio.string('test')),
            ('Description', nbtio.string('')),
            ('RegionCount', nbtio.i32(len(regions))),
            ('TimeCreated', ('l', 0)),
            ('TimeModified', ('l', 0)),
            ('EnclosingSize', xyz([1, 1, 1])),
            ('TotalBlocks', nbtio.i32(0)),
            ('TotalVolume', nbtio.i32(0)),
        ]))),
        ('Regions', nbtio.comp(OrderedDict((n, r) for n, r in regions))),
    ]))
    nbtio.save(path, '', root)


def policy(**kw):
    return L.Policy(**kw)


def read_grid(root):
    c = root[1]
    pal = []
    for e in c['palette'][1][1]:
        d = e[1]
        props = tuple(sorted((k, v[1]) for k, v in
                             d.get('Properties', ('comp', {}))[1].items()))
        pal.append((d['Name'][1], props))
    out = {}
    for b in c['blocks'][1][1]:
        d = b[1]
        pos = tuple(v[1] for v in d['pos'][1][1])
        out[pos] = (pal[d['state'][1]], d.get('nbt'))
    return [v[1] for v in c['size'][1][1]], out


# --- tests ---------------------------------------------------------------

def test_bit_roundtrip():
    print('bit packing')
    for bits in range(2, 13):
        n = 200
        vals = [(i * 7 + bits) % (1 << bits) for i in range(n)]
        got = L.unpack_states(pack(vals, bits), bits, n)
        check(got == vals, 'round-trips at %d bits (straddling: %s)'
              % (bits, 64 % bits != 0))
    check(L.bits_for(1) == 2 and L.bits_for(4) == 2 and L.bits_for(5) == 3
          and L.bits_for(16) == 4 and L.bits_for(17) == 5, 'bits_for matches Litematica')


def test_block_nbt(tmp):
    print('block nbt')
    pal = [state('minecraft:air'), state('minecraft:structure_void'),
           state('minecraft:chest', {'facing': 'north', 'type': 'single',
                                     'waterlogged': 'false'}),
           state('minecraft:oak_sign', {'rotation': '3', 'waterlogged': 'false'}),
           state('minecraft:spawner')]
    cells = {(0, 0, 0): 2, (1, 0, 0): 3, (2, 0, 0): 4, (0, 1, 0): 1, (1, 1, 0): 0}
    chest = nbtio.comp(OrderedDict([
        ('id', nbtio.string('minecraft:chest')),
        ('x', nbtio.i32(0)), ('y', nbtio.i32(0)), ('z', nbtio.i32(0)),
        ('keepPacked', ('b', 0)),
        ('Lock', nbtio.string('frostline_key')),
        ('Items', nbtio.comp_list([OrderedDict([
            ('Slot', ('b', 3)),
            ('id', nbtio.string('minecraft:diamond')),
            ('Count', ('b', 17)),
        ])])),
    ]))
    sign = nbtio.comp(OrderedDict([
        ('id', nbtio.string('minecraft:sign')),
        ('x', nbtio.i32(1)), ('y', nbtio.i32(0)), ('z', nbtio.i32(0)),
        ('Text1', nbtio.string('{"text":"cold"}')),
        ('GlowingText', ('b', 1)),
    ]))
    spawner = nbtio.comp(OrderedDict([
        ('id', nbtio.string('minecraft:mob_spawner')),
        ('x', nbtio.i32(2)), ('y', nbtio.i32(0)), ('z', nbtio.i32(0)),
        ('RequiredPlayerRange', ('s', 24)),
        ('MaxNearbyEntities', ('s', 6)),
        ('SpawnData', nbtio.comp(OrderedDict([
            ('entity', nbtio.comp(OrderedDict([('id', nbtio.string('minecraft:stray'))]))),
        ]))),
    ]))
    p = os.path.join(tmp, 'be.litematic')
    write_litematic(p, [('main', region([0, 0, 0], [3, 2, 1], pal, cells,
                                        tiles=[chest, sign, spawner]))])
    root, rep = L.convert(p, policy())
    _, grid = read_grid(root)

    got_chest = grid[(0, 0, 0)][1]
    check(got_chest is not None, 'chest keeps its nbt')
    if got_chest:
        d = got_chest[1]
        check('x' not in d and 'y' not in d and 'z' not in d, 'chest position keys stripped')
        check('keepPacked' not in d, 'keepPacked stripped')
        check(d.get('id', ('str', ''))[1] == 'minecraft:chest', 'chest id preserved')
        check(d.get('Lock', ('str', ''))[1] == 'frostline_key', 'chest Lock preserved')
        item = d['Items'][1][1][0][1]
        check(item['Count'] == ('b', 17), 'item Count stays a byte with its value')
        check(item['Slot'] == ('b', 3), 'item Slot stays a byte')
        check(item['id'][1] == 'minecraft:diamond', 'item id preserved')

    got_sign = grid[(1, 0, 0)][1]
    check(got_sign and got_sign[1]['Text1'][1] == '{"text":"cold"}', 'sign text preserved')
    check(got_sign and got_sign[1]['GlowingText'] == ('b', 1), 'sign byte flag preserved')

    got_spawner = grid[(2, 0, 0)][1]
    check(got_spawner and got_spawner[1]['RequiredPlayerRange'] == ('s', 24),
          'spawner short stays a short')
    check(got_spawner and got_spawner[1]['SpawnData'][1]['entity'][1]['id'][1]
          == 'minecraft:stray', 'spawner nested SpawnData preserved')

    check(grid[(0, 0, 0)][0] == ('minecraft:chest',
                                 (('facing', 'north'), ('type', 'single'),
                                  ('waterlogged', 'false'))),
          'block state properties preserved')
    check(sum(rep['block_nbt'].values()) == 3, 'all three blocks with NBT reported')

    root, rep = L.convert(p, policy(block_nbt=False))
    _, grid = read_grid(root)
    check(all(v[1] is None for v in grid.values()), '--no-block-nbt drops the data')

    # A dropped air policy must not silently strip a block entity with it.
    root, rep = L.convert(p, policy(air='drop'))
    _, grid = read_grid(root)
    check(grid[(0, 0, 0)][1] is not None, 'block entity survives --air drop')


def test_modded_block_nbt(tmp):
    print('modded block nbt')
    # A block this tool has never heard of, with data shaped nothing like a
    # chest. Nothing may be filtered by id, or modded builds lose their guts.
    pal = [state('minecraft:air'),
           state('create:copycat_panel', {'facing': 'north', 'waterlogged': 'false'})]
    copycat = nbtio.comp(OrderedDict([
        ('id', nbtio.string('create:copycat_panel')),
        ('x', nbtio.i32(0)), ('y', nbtio.i32(0)), ('z', nbtio.i32(0)),
        ('Material', nbtio.comp(OrderedDict([
            ('Name', nbtio.string('minecraft:warped_planks')),
            ('Properties', nbtio.comp(OrderedDict([('waterlogged', nbtio.string('false'))]))),
        ]))),
        ('Redstone', ('b', 1)),
        ('Colour', ('i', 16733525)),
        ('Consumed', ('f', 0.25)),
        ('Sides', ('ba', [0, 1, 2, 3, 4, 5])),
        ('Wrapped', ('la', [-9007199254740993, 7])),
        ('Slots', ('ia', [4, 8, 15])),
        ('Ratio', ('d', 0.3333333333333333)),
        ('Frequency', ('s', -12000)),
    ]))
    p = os.path.join(tmp, 'modded.litematic')
    write_litematic(p, [('main', region([0, 0, 0], [1, 1, 1], pal, {(0, 0, 0): 1},
                                        tiles=[copycat]))])
    root, rep = L.convert(p, policy())
    _, grid = read_grid(root)
    got = grid[(0, 0, 0)]
    check(got[0] == ('create:copycat_panel',
                     (('facing', 'north'), ('waterlogged', 'false'))),
          'modded block state and properties preserved')
    check(got[1] is not None, 'modded block keeps its nbt')
    if got[1]:
        d = got[1][1]
        check(d['Material'][1]['Name'][1] == 'minecraft:warped_planks',
              'nested modded compound preserved')
        check(d['Redstone'] == ('b', 1) and d['Colour'] == ('i', 16733525)
              and d['Frequency'] == ('s', -12000),
              'byte, int and short stay their own types')
        check(d['Consumed'] == ('f', 0.25) and d['Ratio'] == ('d', 0.3333333333333333),
              'float and double stay distinct and exact')
        check(d['Sides'] == ('ba', [0, 1, 2, 3, 4, 5])
              and d['Slots'] == ('ia', [4, 8, 15])
              and d['Wrapped'] == ('la', [-9007199254740993, 7]),
              'byte, int and long arrays survive, 64-bit values intact')
        check(sorted(d) == sorted(['id', 'Material', 'Redstone', 'Colour', 'Consumed',
                                   'Sides', 'Wrapped', 'Slots', 'Ratio', 'Frequency']),
              'no modded key was dropped, and x/y/z are gone')
    check(rep['block_nbt'] == {'create:copycat_panel': 1},
          'modded block reported by its own id')


def test_negative_size(tmp):
    print('negative region sizes')
    pal = [state('minecraft:air'), state('minecraft:stone'),
           state('minecraft:structure_void')]
    # Local index (0,0,0) is the corner AT Position; the region grows -x, -z.
    cells = {(0, 0, 0): 1, (2, 0, 0): 2, (0, 0, 1): 2}
    p = os.path.join(tmp, 'neg.litematic')
    write_litematic(p, [('main', region([10, 5, 10], [-3, 1, -2], pal, cells))])
    root, _ = L.convert(p, policy())
    size, grid = read_grid(root)
    check(size == [3, 1, 2], 'negative size yields a positive box')
    # Position 10 with size -3 spans world x 8..10, so local x = 10 - 8 = 2.
    check(grid[(2, 0, 1)][0][0] == 'minecraft:stone', 'origin cell lands at the high corner')
    check(grid[(0, 0, 1)][0][0] == 'minecraft:structure_void', 'x offset mirrors correctly')
    check(grid[(2, 0, 0)][0][0] == 'minecraft:structure_void', 'z offset mirrors correctly')


def test_multi_region(tmp):
    print('multi-region merge')
    pal = [state('minecraft:air'), state('minecraft:stone')]
    a = region([0, 0, 0], [2, 1, 1], pal, {(0, 0, 0): 1, (1, 0, 0): 1})
    b = region([5, 0, 0], [2, 1, 1], pal, {(0, 0, 0): 1, (1, 0, 0): 1})
    p = os.path.join(tmp, 'multi.litematic')
    write_litematic(p, [('a', a), ('b', b)])
    root, rep = L.convert(p, policy())
    size, grid = read_grid(root)
    check(size == [7, 1, 1], 'bounding box spans both regions')
    check(rep['gaps'] == 3, 'the three cells between the regions are reported as gaps')
    check(grid[(3, 0, 0)][0][0] == 'minecraft:structure_void',
          'uncovered cells default to structure_void')
    check(grid[(6, 0, 0)][0][0] == 'minecraft:stone', 'far region placed at the right offset')
    root, _ = L.convert(p, policy(gap='drop'))
    _, grid = read_grid(root)
    check((3, 0, 0) not in grid, '--gap drop omits uncovered cells')


def test_air_policy(tmp):
    print('air policy')
    pal = [state('minecraft:air'), state('minecraft:stone')]
    p = os.path.join(tmp, 'air.litematic')
    write_litematic(p, [('main', region([0, 0, 0], [2, 1, 1], pal, {(1, 0, 0): 1}))])
    for mode, expect in (('keep', 'minecraft:air'), ('void', 'minecraft:structure_void')):
        root, _ = L.convert(p, policy(air=mode))
        _, grid = read_grid(root)
        check(grid[(0, 0, 0)][0][0] == expect, '--air %s' % mode)
    root, rep = L.convert(p, policy(air='drop'))
    _, grid = read_grid(root)
    check((0, 0, 0) not in grid and len(grid) == 1, '--air drop omits the cell entirely')
    root, rep = L.convert(p, policy(air='keep'))
    check(any('carve terrain' in w for w in rep['warnings']),
          'air with no voids is warned about')


def test_entities(tmp):
    print('entities')
    pal = [state('minecraft:air'), state('minecraft:stone')]

    def ent(eid, pos):
        return nbtio.comp(OrderedDict([
            ('id', nbtio.string(eid)),
            ('Pos', nbtio.dbl_list(pos)),
            ('Rotation', ('list', (5, [('f', 90.0), ('f', 0.0)]))),
            ('UUID', ('ia', [1, 2, 3, 4])),
            ('Motion', nbtio.dbl_list([0.1, 0.0, 0.0])),
            ('forge:spawn_type', nbtio.string('NATURAL')),
            ('Invulnerable', ('b', 1)),
        ]))

    p = os.path.join(tmp, 'ent.litematic')
    write_litematic(p, [('main', region(
        [4, 0, 4], [2, 1, 1], pal, {(0, 0, 0): 1},
        entities=[ent('minecraft:armor_stand', [0.5, 0.0, 0.5]),
                  ent('minecraft:item_frame', [1.5, 0.0, 0.5]),
                  ent('minecraft:stray', [1.5, 0.0, 0.5]),
                  ent('minecraft:marker', [0.5, 0.0, 0.5])]))])

    root, rep = L.convert(p, policy())
    kept = sorted(e[1]['nbt'][1]['id'][1] for e in root[1]['entities'][1][1])
    check(kept == ['minecraft:armor_stand', 'minecraft:item_frame',
                   'minecraft:marker', 'minecraft:stray'],
          'every entity is kept by default, armour stands and item frames included')
    check(not rep['entities_dropped'], 'nothing is dropped unasked')

    root, rep = L.convert(p, policy(drop_entities={'minecraft:stray',
                                                   'minecraft:marker'}))
    kept = sorted(e[1]['nbt'][1]['id'][1] for e in root[1]['entities'][1][1])
    check(kept == ['minecraft:armor_stand', 'minecraft:item_frame'],
          '--drop-entity removes only what was named')
    check(sum(rep['entities_dropped'].values()) == 2, 'dropped entities are reported')

    root, rep = L.convert(p, policy(keep_entities=set()))
    check(len(root[1]['entities'][1][1]) == 0, '--no-entities drops them all')

    root, rep = L.convert(p, policy(keep_entities={'minecraft:item_frame',
                                                   'minecraft:armor_stand'}))
    kept = [e[1]['nbt'][1]['id'][1] for e in root[1]['entities'][1][1]]
    check(sorted(kept) == ['minecraft:armor_stand', 'minecraft:item_frame'],
          '--keep-entity keeps only the listed types')
    e = [x for x in root[1]['entities'][1][1]
         if x[1]['nbt'][1]['id'][1] == 'minecraft:item_frame'][0][1]
    check([v[1] for v in e['pos'][1][1]] == [1.5, 0.0, 0.5],
          'entity position made local to the box')
    check([v[1] for v in e['blockPos'][1][1]] == [1, 0, 0], 'blockPos floors the position')
    check('UUID' not in e['nbt'][1] and 'Motion' not in e['nbt'][1],
          'per-instance junk stripped')
    check('forge:spawn_type' not in e['nbt'][1], 'forge spawn metadata stripped')
    check(e['nbt'][1]['Rotation'][1][1][0][1] == 90.0, 'Rotation kept')
    check(e['nbt'][1]['Invulnerable'] == ('b', 1), 'entity flags kept')


def test_cli(tmp):
    print('cli and target matching')
    pal = [state('minecraft:air'), state('minecraft:stone')]
    src = os.path.join(tmp, 'schematics', 'r1')
    os.makedirs(src, exist_ok=True)
    for n in ('fallen_tree_0', 'fallen_tree_1', 'fallen_tree_12', 'boulder_1'):
        write_litematic(os.path.join(src, n + '.litematic'),
                        [('main', region([0, 0, 0], [2, 1, 1], pal, {(0, 0, 0): 1}))])
    out = os.path.join(tmp, 'out')

    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        rc = L.main(['fallen_tree_*', '-d', out, '-q'])
        check(rc == 0, 'wildcard target exits clean')
        made = sorted(os.listdir(out)) if os.path.isdir(out) else []
        check(made == ['fallen_tree_0.nbt', 'fallen_tree_1.nbt', 'fallen_tree_12.nbt'],
              'wildcard matched every fallen_tree and nothing else')
        shutil.rmtree(out)
        check(L.main(['fallen_tree_1', '-d', out, '-q']) == 0,
              'bare name with no extension and no path resolves')
        check(os.path.isfile(os.path.join(out, 'fallen_tree_1.nbt')), 'bare name wrote output')
        check(L.main(['schematics', '-d', out, '-q']) == 0, 'directory target walks')
        check(len(os.listdir(out)) == 4, 'directory target converted all four')
        check(L.main(['no_such_thing_*', '-q']) == 2, 'an unmatched target is an error')
        check(L.main(['fallen_tree_*', '-o', 'x.nbt', '-q']) == 2,
              '-o with several matches is refused')
        check(L.main(['fallen_tree_1', '--keep-entity', 'stray',
                      '--drop-entity', 'marker', '-q']) == 2,
              'contradictory entity flags are refused')
        check(L.main(['--list', 'fallen_tree_1', '-q']) == 0, '--list runs')
        check(L.main(['--list', '--json', 'fallen_tree_1']) == 0, '--list --json runs')
    finally:
        os.chdir(cwd)


def test_inventory(tmp):
    print('inventory')
    pal = [state('minecraft:air'), state('minecraft:stone'),
           state('minecraft:snow', {'layers': '3'})]
    chest = nbtio.comp(OrderedDict([
        ('id', nbtio.string('minecraft:chest')),
        ('x', nbtio.i32(1)), ('y', nbtio.i32(0)), ('z', nbtio.i32(0)),
    ]))
    ents = [nbtio.comp(OrderedDict([('id', nbtio.string('minecraft:armor_stand')),
                                    ('Pos', nbtio.dbl_list([0.5, 0.0, 0.5]))]))]
    p = os.path.join(tmp, 'inv.litematic')
    write_litematic(p, [('main', region([0, 0, 0], [3, 1, 1], pal,
                                        {(1, 0, 0): 1, (2, 0, 0): 2},
                                        tiles=[chest], entities=ents))])
    inv = L.inventory(L.read_schematic(p))
    check(inv['size'] == [3, 1, 1] and inv['volume'] == 3, 'inventory reports the box')
    check(inv['blocks'] == {'minecraft:air': 1, 'minecraft:stone': 1,
                            'minecraft:snow[layers=3]': 1},
          'inventory counts every block state, properties included')
    check(inv['entities'] == {'minecraft:armor_stand': 1}, 'inventory lists entities')
    check(inv['block_nbt'] == {'minecraft:chest': 1}, 'inventory lists blocks with NBT')


def test_drop_block(tmp):
    print('block exclusion')
    pal = [state('minecraft:air'), state('minecraft:stone'),
           state('minecraft:grass_block', {'snowy': 'false'})]
    p = os.path.join(tmp, 'db.litematic')
    write_litematic(p, [('main', region([0, 0, 0], [3, 1, 1], pal,
                                        {(0, 0, 0): 1, (1, 0, 0): 2, (2, 0, 0): 2}))])
    root, rep = L.convert(p, policy(drop_blocks={'minecraft:grass_block'}))
    _, grid = read_grid(root)
    check(grid[(0, 0, 0)][0][0] == 'minecraft:stone', 'untouched block still placed')
    check(grid[(1, 0, 0)][0][0] == 'minecraft:structure_void'
          and grid[(2, 0, 0)][0][0] == 'minecraft:structure_void',
          'dropped block type becomes structure_void')
    check(rep['blocks_dropped'] == {'minecraft:grass_block': 2}, 'dropped blocks reported')


def test_gui(tmp):
    print('gui')
    try:
        import tkinter as tk
        import litematic_gui as G
        root = tk.Tk()
    except Exception as e:
        print('  skip  no tkinter or no display (%s)' % e)
        return
    try:
        root.withdraw()
        pal = [state('minecraft:air'), state('minecraft:stone')]
        ents = [nbtio.comp(OrderedDict([('id', nbtio.string('minecraft:stray')),
                                        ('Pos', nbtio.dbl_list([0.5, 0.0, 0.5]))]))]
        src = os.path.join(tmp, 'gui.litematic')
        write_litematic(src, [('main', region([0, 0, 0], [2, 1, 1], pal,
                                              {(0, 0, 0): 1}, entities=ents))])
        out = os.path.join(tmp, 'guiout')
        app = G.App(root, outdir=out)
        app.add_paths([src])
        check(len(app.good_scenes()) == 1, 'gui loads a schematic')
        check(set(app.entities.state) == {'minecraft:stray'}, 'gui lists the entity')
        check(all(app.entities.state.values()) and all(app.blocks.state.values()),
              'everything starts ticked')
        check(app.policy().drop_entities == set(), 'nothing is dropped while all ticked')
        app.entities.toggle(['minecraft:stray'])
        check(app.policy().drop_entities == {'minecraft:stray'},
              'unticking an entity drops it')
        app.convert()
        made = os.path.join(out, 'gui.nbt')
        check(os.path.isfile(made), 'gui writes the .nbt')
        if os.path.isfile(made):
            _, r = nbtio.load(made)
            check(len(r[1]['entities'][1][1]) == 0, 'the unticked entity is gone from output')
        app.blocks.set_all(False)
        check(app.policy().drop_blocks == {'minecraft:air', 'minecraft:stone'},
              'unticking every state of a block drops that block')
    finally:
        root.destroy()


def main():
    tmp = tempfile.mkdtemp(prefix='lite2nbt-')
    try:
        test_bit_roundtrip()
        test_block_nbt(tmp)
        test_modded_block_nbt(tmp)
        test_negative_size(tmp)
        test_multi_region(tmp)
        test_air_policy(tmp)
        test_entities(tmp)
        test_cli(tmp)
        test_inventory(tmp)
        test_drop_block(tmp)
        test_gui(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print('%d FAILED:' % len(FAILS))
        for f in FAILS:
            print('  - ' + f)
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
