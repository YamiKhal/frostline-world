#!/usr/bin/env python3
"""Self-contained tests for litematic_doc, the editing engine behind
tools/apps/litematic_edit.py. Run: python tools/tests/test_litematic_doc.py

The schematic builders come from test_litematic2nbt, so both suites exercise the
same synthetic files. What is checked here is the part converting never does:
writing a .litematic back out and still having Litematica's own invariants hold
-- bit-packed states, palette indices, tile entity positions, unknown tags.

No game, no fixtures.
"""
import os
import random
import shutil
import sys
import tempfile
from collections import OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_TOOLS, 'apps'))
sys.path.insert(0, os.path.join(_TOOLS, 'cli'))
import nbtio
import litematic2nbt as L
import litematic_doc as D
from test_litematic2nbt import state, xyz, comps, region, write_litematic

FAILS = []

STONE = 'minecraft:stone'
LOG = 'minecraft:oak_log'
CHEST = 'minecraft:chest'


def check(cond, label):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILS.append(label)


def grid_of(doc):
    """-> {(x,y,z): state key} for a single-region document."""
    r = doc.regions[0]
    w, h, l = r.dims
    out = {}
    for i, si in enumerate(r.indices):
        y, rem = divmod(i, w * l)
        z, x = divmod(rem, w)
        out[(x, y, z)] = r.palette[si]
    return out


def save_apply(doc, tmp, out='out.litematic', **plan):
    """apply + save + reload, which is the only proof the bytes survive."""
    root, report = doc.apply(**plan)
    dest, _ = doc.save(root, os.path.join(tmp, out), backup=False)
    return D.Doc.load(dest), report


def simple(path, cells=None, tiles=(), entities=(), palette=None, size=(3, 1, 3)):
    palette = palette or [state('minecraft:air'), state(STONE), state(LOG, {'axis': 'y'})]
    cells = {(0, 0, 0): 1, (1, 0, 0): 1, (2, 0, 0): 2} if cells is None else cells
    write_litematic(path, [('r', region([0, 0, 0], list(size), palette, cells,
                                        tiles=tiles, entities=entities))])
    return path


# --- bit packing ---------------------------------------------------------

def test_packing():
    print('bit packing')
    rng = random.Random(7)
    ok = True
    for bits in range(2, 13):
        for count in (1, 2, 63, 64, 65, 257):
            values = [rng.randrange(1 << bits) for _ in range(count)]
            longs = D.pack_states(values, bits)
            ok = ok and len(longs) == (count * bits + 63) // 64
            ok = ok and all(-(1 << 63) <= v < (1 << 63) for v in longs)
            ok = ok and L.unpack_states(longs, bits, count) == values
    check(ok, 'pack -> unpack is the identity at every width, length and straddle')


# --- reading -------------------------------------------------------------

def test_rows(tmp):
    print('what the lists show')
    tile = OrderedDict([('x', ('i', 0)), ('y', ('i', 0)), ('z', ('i', 0)),
                        ('id', nbtio.string('minecraft:chest'))])
    ent = OrderedDict([('id', nbtio.string('minecraft:stray')),
                       ('Pos', nbtio.dbl_list([0.5, 0.0, 0.5])),
                       ('UUID', ('ia', [1, 2, 3, 4])),
                       ('Health', ('f', 20.0))])
    ent2 = OrderedDict([('id', nbtio.string('minecraft:marker')),
                        ('Pos', nbtio.dbl_list([1.5, 0.0, 0.5])),
                        ('Rotation', ('list', (5, [('f', 0.0), ('f', 0.0)])))])
    path = simple(os.path.join(tmp, 'rows.litematic'),
                  palette=[state('minecraft:air'), state(CHEST), state(STONE)],
                  cells={(0, 0, 0): 1, (1, 0, 0): 2, (2, 0, 0): 2},
                  tiles=[tile], entities=[ent, ent2])
    doc = D.Doc.load(path)

    rows = {L.state_label(k): (n, nbt) for k, n, nbt in doc.block_rows()}
    check(rows[CHEST] == (1, 1), 'the chest is listed once, carrying NBT')
    check(rows[STONE] == (2, 0), 'stone is listed twice, carrying none')
    check(rows['minecraft:air'][0] == 6, 'air fills the rest of the box')

    ents = {eid: (n, rich) for eid, n, rich in doc.entity_rows()}
    check(ents['minecraft:stray'] == (1, 1), 'the stray is listed as carrying NBT')
    check(ents['minecraft:marker'] == (1, 0), 'placement-only tags do not count as NBT')


def test_untouched_round_trip(tmp):
    print('an empty plan changes nothing')
    path = simple(os.path.join(tmp, 'plain.litematic'))
    doc = D.Doc.load(path)
    before = grid_of(doc)
    again, _ = save_apply(doc, tmp, 'plain_out.litematic')
    check(grid_of(again) == before, 'every cell survives a load/save round trip')
    check(again.root[1]['Regions'][1]['r'][1]['Size'][1]['x'][1] == 3,
          'the region keeps its own size and position')
    check(again.root[1]['MinecraftDataVersion'][1] == 3465, 'DataVersion is carried over')


def test_unknown_tags_survive(tmp):
    print('tags this tool does not know')
    path = simple(os.path.join(tmp, 'odd.litematic'))
    name, root = nbtio.load(path)
    root[1]['SomeFutureTag'] = nbtio.string('keep me')
    root[1]['Regions'][1]['r'][1]['ModSpecific'] = ('i', 42)
    nbtio.save(path, name, root)

    doc = D.Doc.load(path)
    again, _ = save_apply(doc, tmp, 'odd_out.litematic')
    check(again.root[1].get('SomeFutureTag', (None, None))[1] == 'keep me',
          'an unknown root tag is still there after saving')
    check(again.root[1]['Regions'][1]['r'][1].get('ModSpecific', (None, None))[1] == 42,
          'an unknown region tag is still there after saving')


# --- editing blocks ------------------------------------------------------

def test_remove_block(tmp):
    print('removing a block')
    path = simple(os.path.join(tmp, 'rm.litematic'))
    doc = D.Doc.load(path)
    again, report = save_apply(doc, tmp, 'rm_out.litematic',
                               blocks={(STONE, ()): D.DELETE})
    g = grid_of(again)
    check(g[(0, 0, 0)] == ('minecraft:air', ()), 'a removed block becomes air')
    check(g[(2, 0, 0)] == (LOG, (('axis', 'y'),)), 'other blocks are untouched')
    check(report['blocks_deleted'][STONE] == 2, 'both cells are reported removed')
    check(all(k[0] != STONE for k in again.regions[0].palette),
          'the palette entry is pruned once nothing points at it')


def test_remove_fills_with_void(tmp):
    print('removing into structure_void')
    path = simple(os.path.join(tmp, 'void.litematic'))
    doc = D.Doc.load(path)
    again, _ = save_apply(doc, tmp, 'void_out.litematic',
                          blocks={(STONE, ()): D.DELETE}, fill=(D.VOID, ()))
    check(grid_of(again)[(0, 0, 0)] == (D.VOID, ()),
          'the fill block is what the caller asked for')


def test_replace_block(tmp):
    print('replacing a block')
    path = simple(os.path.join(tmp, 'sw.litematic'))
    doc = D.Doc.load(path)
    target = ('minecraft:spruce_log', (('axis', 'z'),))
    again, report = save_apply(doc, tmp, 'sw_out.litematic',
                               blocks={(LOG, (('axis', 'y'),)): target})
    check(grid_of(again)[(2, 0, 0)] == target, 'the cell holds the new state')
    check(report['blocks_replaced']['%s[axis=y]' % LOG] == 1, 'the swap is reported')
    check(not report['blocks_deleted'], 'a swap is not a removal')


def test_replace_merges_palette(tmp):
    print('replacing onto a state already in the palette')
    path = simple(os.path.join(tmp, 'merge.litematic'))
    doc = D.Doc.load(path)
    again, _ = save_apply(doc, tmp, 'merge_out.litematic',
                          blocks={(LOG, (('axis', 'y'),)): (STONE, ())})
    labels = [L.state_label(k) for k in again.regions[0].palette]
    check(labels.count(STONE) == 1, 'the palette does not end up with the state twice')
    check(sum(1 for v in grid_of(again).values() if v == (STONE, ())) == 3,
          'all three cells are stone now')


def test_air_stays_first(tmp):
    print('palette order')
    path = simple(os.path.join(tmp, 'air.litematic'))
    doc = D.Doc.load(path)
    again, _ = save_apply(doc, tmp, 'air_out.litematic',
                          blocks={(STONE, ()): D.DELETE})
    check(again.regions[0].palette[0] == ('minecraft:air', ()),
          'air keeps palette slot 0, the way Litematica writes it')


# --- editing blocks that carry NBT --------------------------------------

def tile_at(x, y, z, tid='minecraft:chest'):
    return OrderedDict([('x', ('i', x)), ('y', ('i', y)), ('z', ('i', z)),
                        ('id', nbtio.string(tid)),
                        ('Items', nbtio.comp_list([]))])


def test_block_nbt_dropped_on_retype(tmp):
    print('block NBT when the block changes')
    path = simple(os.path.join(tmp, 'nbt.litematic'),
                  palette=[state('minecraft:air'), state(CHEST, {'facing': 'north'})],
                  cells={(0, 0, 0): 1},
                  tiles=[tile_at(0, 0, 0)])
    doc = D.Doc.load(path)
    again, report = save_apply(doc, tmp, 'nbt_out.litematic',
                               blocks={(CHEST, (('facing', 'north'),)): (STONE, ())})
    check(len(again.regions[0].tile_entities) == 0,
          'the chest data goes with the chest')
    check(report['block_nbt_dropped'][CHEST] == 1, 'dropping it is reported, not silent')


def test_block_nbt_kept_on_property_change(tmp):
    print('block NBT when only the properties change')
    path = simple(os.path.join(tmp, 'nbt2.litematic'),
                  palette=[state('minecraft:air'), state(CHEST, {'facing': 'north'})],
                  cells={(0, 0, 0): 1},
                  tiles=[tile_at(0, 0, 0)])
    doc = D.Doc.load(path)
    again, report = save_apply(
        doc, tmp, 'nbt2_out.litematic',
        blocks={(CHEST, (('facing', 'north'),)): (CHEST, (('facing', 'south'),))})
    check(len(again.regions[0].tile_entities) == 1,
          'turning a chest round keeps what is in it')
    check(not report['block_nbt_dropped'], 'and nothing is reported as dropped')
    check(grid_of(again)[(0, 0, 0)] == (CHEST, (('facing', 'south'),)),
          'the new property is what was asked for')


def test_nbt_of_other_blocks_survives(tmp):
    print('block NBT elsewhere in the region')
    path = simple(os.path.join(tmp, 'nbt3.litematic'),
                  palette=[state('minecraft:air'), state(CHEST), state(STONE)],
                  cells={(0, 0, 0): 1, (1, 0, 0): 2},
                  tiles=[tile_at(0, 0, 0)])
    doc = D.Doc.load(path)
    again, report = save_apply(doc, tmp, 'nbt3_out.litematic',
                               blocks={(STONE, ()): D.DELETE})
    check(len(again.regions[0].tile_entities) == 1,
          'editing one block does not touch another block\'s data')
    check(not report['block_nbt_dropped'], 'nothing reported dropped')


# --- entities ------------------------------------------------------------

def stray_at(x=0.5, z=0.5):
    return OrderedDict([('id', nbtio.string('minecraft:stray')),
                        ('Pos', nbtio.dbl_list([x, 0.0, z])),
                        ('Rotation', ('list', (5, [('f', 90.0), ('f', 0.0)]))),
                        ('Health', ('f', 20.0)),
                        ('HandItems', nbtio.comp_list([]))])


def test_remove_entity(tmp):
    print('removing an entity')
    path = simple(os.path.join(tmp, 'ent.litematic'),
                  entities=[stray_at(), OrderedDict([
                      ('id', nbtio.string('minecraft:marker')),
                      ('Pos', nbtio.dbl_list([1.5, 0.0, 0.5]))])])
    doc = D.Doc.load(path)
    again, report = save_apply(doc, tmp, 'ent_out.litematic',
                               entities={'minecraft:stray': D.DELETE})
    left = [e[1]['id'][1] for e in again.regions[0].entities]
    check(left == ['minecraft:marker'], 'only the named entity type goes')
    check(report['entities_deleted']['minecraft:stray'] == 1, 'the removal is reported')


def test_retype_entity(tmp):
    print('replacing an entity id')
    path = simple(os.path.join(tmp, 'ent2.litematic'), entities=[stray_at(2.5, 1.5)])
    doc = D.Doc.load(path)
    again, report = save_apply(doc, tmp, 'ent2_out.litematic',
                               entities={'minecraft:stray': 'minecraft:marker'})
    ent = again.regions[0].entities[0][1]
    check(ent['id'][1] == 'minecraft:marker', 'the id is the new one')
    check([v[1] for v in ent['Pos'][1][1]] == [2.5, 0.0, 1.5], 'it stays where it was')
    check('Rotation' in ent, 'rotation is placement, so it survives')
    check('Health' not in ent and 'HandItems' not in ent,
          'the old species\' own data does not follow it')
    check(report['entity_nbt_stripped']['minecraft:stray'] == 2,
          'the dropped tags are counted')


# --- negative region sizes ----------------------------------------------

def test_negative_size(tmp):
    print('a region dragged the other way')
    tile = tile_at(1, 0, 0)
    path = os.path.join(tmp, 'neg.litematic')
    write_litematic(path, [('r', region(
        [5, 0, 5], [3, 1, -2],
        [state('minecraft:air'), state(CHEST), state(STONE)],
        {(1, 0, 0): 1, (2, 0, 1): 2}, tiles=[tile]))])
    doc = D.Doc.load(path)
    again, report = save_apply(doc, tmp, 'neg_out.litematic',
                               blocks={(CHEST, ()): (STONE, ())})
    check(grid_of(again)[(1, 0, 0)] == (STONE, ()),
          'the cell is addressed from the region minimum, not from Position')
    check(report['block_nbt_dropped'][CHEST] == 1,
          'the tile entity at that local position is matched and dropped')
    check(again.root[1]['Regions'][1]['r'][1]['Size'][1]['z'][1] == -2,
          'the negative size is written back unchanged')


# --- multiple regions ----------------------------------------------------

def test_two_regions(tmp):
    print('two regions')
    path = os.path.join(tmp, 'two.litematic')
    pal = [state('minecraft:air'), state(STONE)]
    write_litematic(path, [
        ('a', region([0, 0, 0], [2, 1, 1], pal, {(0, 0, 0): 1})),
        ('b', region([9, 0, 0], [2, 1, 1], pal, {(1, 0, 0): 1})),
    ])
    doc = D.Doc.load(path)
    again, report = save_apply(doc, tmp, 'two_out.litematic',
                               blocks={(STONE, ()): D.DELETE})
    check(len(again.regions) == 2, 'both regions are still there')
    check(report['blocks_deleted'][STONE] == 2, 'the edit reaches every region')
    check(all(all(r.palette[i][0] == 'minecraft:air' for i in r.indices)
              for r in again.regions), 'and every cell in both is air now')


# --- metadata ------------------------------------------------------------

def test_metadata(tmp):
    print('metadata')
    path = simple(os.path.join(tmp, 'meta.litematic'))
    doc = D.Doc.load(path)
    before = doc.meta['TimeModified'][1]
    again, _ = save_apply(doc, tmp, 'meta_out.litematic',
                          blocks={(STONE, ()): D.DELETE})
    check(again.meta['TotalBlocks'][1] == 1,
          'TotalBlocks is recounted (one log left, air does not count)')
    check(again.meta['TimeModified'][1] > before, 'TimeModified moves forward')
    check(again.meta['Name'][1] == 'test', 'the name is left alone unless asked')


def test_rename(tmp):
    print('renaming')
    path = simple(os.path.join(tmp, 'ren.litematic'))
    doc = D.Doc.load(path)
    check(doc.name == 'test', 'the name read back is the one in Metadata')
    again, report = save_apply(doc, tmp, 'ren_out.litematic', name='grave_1')
    check(again.name == 'grave_1', 'the new name is in Metadata')
    check(report['renamed'] == ('test', 'grave_1'), 'the rename is reported')
    check(list(again.root[1]['Regions'][1]) == ['grave_1'],
          'a single region is renamed to match, the way Litematica saves one')
    check(report['region_renamed'] == ('r', 'grave_1'), 'the region rename is reported')
    check(grid_of(again) == grid_of(doc), 'and nothing in the region moved')


def test_rename_same_name(tmp):
    print('renaming to what it is already called')
    path = simple(os.path.join(tmp, 'ren2.litematic'))
    doc = D.Doc.load(path)
    _again, report = save_apply(doc, tmp, 'ren2_out.litematic', name='test')
    check(report['renamed'] is None, 'asking for the name it already has does nothing')


def test_rename_many_regions(tmp):
    print('renaming a file with several regions')
    path = os.path.join(tmp, 'ren3.litematic')
    pal = [state('minecraft:air'), state(STONE)]
    write_litematic(path, [
        ('a', region([0, 0, 0], [2, 1, 1], pal, {(0, 0, 0): 1})),
        ('b', region([9, 0, 0], [2, 1, 1], pal, {(1, 0, 0): 1})),
    ])
    doc = D.Doc.load(path)
    again, report = save_apply(doc, tmp, 'ren3_out.litematic', name='two_bits')
    check(again.name == 'two_bits', 'the schematic is renamed')
    check(sorted(again.root[1]['Regions'][1]) == ['a', 'b'],
          'region names are left alone: no one of them is "the" region')
    check(report['region_renamed'] is None and report['warnings'],
          'and that is said out loud rather than silently skipped')


def test_file_stem():
    print('what "match file" means')
    check(D.file_stem('/x/y/grave_1.litematic') == 'grave_1', 'the extension goes')
    check(D.file_stem('grave_1.litematic.backup') == 'grave_1.litematic',
          'only the last extension goes')
    check(D.file_stem(os.path.join('C:', 'schematics', 'dead_tree_2.litematic'))
          == 'dead_tree_2', 'a full path is fine')


# --- saving and backups --------------------------------------------------

def test_backup(tmp):
    print('backups')
    folder = os.path.join(tmp, 'bk')
    os.makedirs(folder)
    path = simple(os.path.join(folder, 'b.litematic'))
    original = open(path, 'rb').read()

    doc = D.Doc.load(path)
    root, _ = doc.apply(blocks={(STONE, ()): D.DELETE})
    dest, made = doc.save(root, backup=True)
    check(dest == os.path.abspath(path), 'saving with no path overwrites the file opened')
    check(made == path + '.backup', 'the old file is kept next to it as .backup')
    check(open(made, 'rb').read() == original, 'and it is the bytes that were there before')
    check(open(dest, 'rb').read() != original, 'while the file itself did change')

    doc2 = D.Doc.load(dest)
    root2, _ = doc2.apply(blocks={(LOG, (('axis', 'y'),)): D.DELETE})
    _, made2 = doc2.save(root2, backup=True)
    check(made2 == path + '.backup.1', 'a second save numbers the backup instead of '
                                       'overwriting the first')
    check(open(path + '.backup', 'rb').read() == original, 'the first backup is intact')


def test_no_backup(tmp):
    print('backups turned off')
    folder = os.path.join(tmp, 'nobk')
    os.makedirs(folder)
    path = simple(os.path.join(folder, 'n.litematic'))
    doc = D.Doc.load(path)
    root, _ = doc.apply()
    _, made = doc.save(root, backup=False)
    check(made is None, 'nothing is reported as backed up')
    check(os.listdir(folder) == ['n.litematic'], 'and nothing else is written')


def test_save_as(tmp):
    print('save as')
    path = simple(os.path.join(tmp, 'src.litematic'))
    doc = D.Doc.load(path)
    root, _ = doc.apply(blocks={(STONE, ()): D.DELETE})
    other = os.path.join(tmp, 'sub', 'copy.litematic')
    dest, made = doc.save(root, other, backup=True)
    check(os.path.isfile(dest), 'a missing folder is created')
    check(made is None, 'there was no file to back up')
    check(D.Doc.load(path).block_rows()[0][1] > 0 and
          any(k[0] == STONE for k, _n, _x in D.Doc.load(path).block_rows()),
          'the file it was loaded from is untouched')


# --- input handling ------------------------------------------------------

def test_parse_state():
    print('parsing what the user typed')
    check(D.parse_state('stone') == ('minecraft:stone', ()), 'a bare name is qualified')
    check(D.parse_state(' minecraft:oak_log[axis=y] ') ==
          ('minecraft:oak_log', (('axis', 'y'),)), 'properties are parsed and sorted')
    check(D.parse_state('a:b[y=1,x=2]')[1] == (('x', '2'), ('y', '1')),
          'property order does not matter')
    for bad in ('', '   ', 'minecraft:stone[axis]', '[axis=y]'):
        try:
            D.parse_state(bad)
            check(False, 'rejects %r' % bad)
        except D.EditError:
            check(True, 'rejects %r' % bad)


def test_rejects_non_schematic(tmp):
    print('files that are not schematics')
    path = os.path.join(tmp, 'nope.litematic')
    nbtio.save(path, '', ('comp', OrderedDict([('Hello', nbtio.string('world'))])))
    try:
        D.Doc.load(path)
        check(False, 'a file with no Regions is refused')
    except D.EditError:
        check(True, 'a file with no Regions is refused')

    old = os.path.join(tmp, 'old.litematic')
    write_litematic(old, [('r', region([0, 0, 0], [1, 1, 1],
                                       [state('minecraft:air')], {}))])
    name, root = nbtio.load(old)
    root[1]['Version'] = ('i', 3)
    nbtio.save(old, name, root)
    try:
        D.Doc.load(old)
        check(False, 'a pre-v4 schematic is refused')
    except D.EditError:
        check(True, 'a pre-v4 schematic is refused')


# --- the GUI on top of it ------------------------------------------------

def test_gui(tmp):
    print('gui')
    try:
        import tkinter as tk
        import litematic_edit as E
        root = tk.Tk()
    except Exception as e:
        print('  skip  no tkinter or no display (%s)' % e)
        return
    try:
        root.withdraw()
        path = simple(os.path.join(tmp, 'gui.litematic'),
                      palette=[state('minecraft:air'), state(CHEST), state(STONE)],
                      cells={(0, 0, 0): 1, (1, 0, 0): 2},
                      tiles=[tile_at(0, 0, 0)],
                      entities=[stray_at()])
        app = E.App(root)
        check(app.load_path(path), 'the app opens a schematic')
        check(CHEST in app.blocks.rows and STONE in app.blocks.rows,
              'every block state reaches the list')
        check(app.blocks.rows[CHEST][2] == 1, 'the list flags the block carrying NBT')
        check('minecraft:stray' in app.entities.rows, 'entities reach their list')

        app.blocks.set_action([STONE], (E.REMOVE,))
        app.entities.set_action(['minecraft:stray'], (E.REPLACE, 'minecraft:marker'))
        blocks, entities = app.build_plan()
        check(blocks == {(STONE, ()): D.DELETE}, 'a removal becomes a delete in the plan')
        check(entities == {'minecraft:stray': 'minecraft:marker'},
              'a retype becomes a rename in the plan')

        app.backup.set(False)
        app.save()
        after = D.Doc.load(path)
        labels = [L.state_label(k) for k, _n, _x in after.block_rows()]
        check(STONE not in labels, 'the saved file really lost the block')
        check(after.regions[0].entities[0][1]['id'][1] == 'minecraft:marker',
              'and really has the new entity')
        check(not app.planned(), 'the plan is cleared once it has been written')
        check(app.doc.path == os.path.abspath(path),
              'the app reopens what it just wrote')

        check(app.name_var.get() == 'test', 'the name field shows the schematic name')
        app.name_from_file()
        check(app.name_var.get() == 'gui', 'Match file takes the name from the filename')
        check(app.planned(), 'a rename on its own counts as an unsaved edit')
        app.save()
        check(D.Doc.load(path).name == 'gui', 'and it reaches the file')
        check(list(D.Doc.load(path).root[1]['Regions'][1]) == ['gui'],
              'region included')
        check(not app.planned(), 'after which there is nothing pending')
    finally:
        root.destroy()


def test_gui_folder(tmp):
    print('gui: walking a folder')
    try:
        import tkinter as tk
        import litematic_edit as E
        window = tk.Tk()
    except Exception as e:
        print('  skip  no tkinter or no display (%s)' % e)
        return

    folder = os.path.join(tmp, 'walk')
    os.makedirs(os.path.join(folder, 'sub'))
    names = ['a.litematic', 'b.litematic', os.path.join('sub', 'c.litematic')]
    for n in names:
        simple(os.path.join(folder, n))
    open(os.path.join(folder, 'notes.txt'), 'w').close()

    answers = {'yes': True}
    try:
        window.withdraw()
        app = E.App(window)
        check(app.load_folder(folder), 'a folder of schematics opens')
        check(len(app.files) == 3, 'every .litematic under it is listed, subfolders too')
        check(all(not f.endswith('.txt') for f in app.files), 'and nothing else is')
        check(len(app.file_tree.get_children()) == 3, 'the list shows them all')
        check(app.index == 0 and app.doc.path == app.files[0],
              'the first one is opened straight away')
        check(app.position_label.cget('text') == '1 / 3', 'the position is shown')
        check('disabled' in app.prev_button.state(), 'Prev is dead at the top')

        app.step(1)
        check(app.index == 1 and app.doc.path == app.files[1], 'Next moves on')
        app.step(1)
        check(app.index == 2, 'and again')
        check('disabled' in app.next_button.state(), 'Next is dead at the end')
        app.step(1)
        check(app.index == 2, 'and stepping past the end stays put')
        app.step(-1)
        check(app.index == 1, 'Prev goes back')

        app._open(app.files[0])
        check(app.index == 0 and app.doc.path == app.files[0],
              'clicking a row opens that file')
        check(app.file_tree.selection() == (app.files[0],),
              'and the list follows what is open')

        # Switching away from unsaved edits asks first, and no means no.
        app.blocks.set_action([STONE], (E.REMOVE,))
        E.messagebox.askyesno = lambda *_a, **_kw: answers['yes']
        answers['yes'] = False
        app.step(1)
        check(app.index == 0 and app.planned(),
              'saying no to the unsaved-edits prompt keeps you on the file')
        answers['yes'] = True
        app.step(1)
        check(app.index == 1 and not app.planned(), 'saying yes moves on and drops them')

        app.backup.set(False)
        app.blocks.set_action([STONE], (E.REMOVE,))
        app.save()
        check(app.files[1] in app.saved_paths, 'a saved file is marked in the list')
        check(app.file_tree.set(app.files[1], 'mark') == '*', 'with a visible mark')
        check(app.index == 1, 'and saving leaves you where you were')

        # A file opened from outside the folder leaves the walk without a place
        # in it, which Next has to cope with.
        outside = simple(os.path.join(tmp, 'outsider.litematic'))
        app.load_path(outside)
        check(app.index == -1, 'a file from elsewhere is not in the walk')
        check(app.position_label.cget('text') == '3 files',
              'so the position shows the count instead')
        app.step(1)
        check(app.index == 0, 'and Next starts the folder again from the top')
    finally:
        window.destroy()


def main():
    tmp = tempfile.mkdtemp(prefix='litedit')
    try:
        test_packing()
        test_parse_state()
        test_rows(tmp)
        test_untouched_round_trip(tmp)
        test_unknown_tags_survive(tmp)
        test_remove_block(tmp)
        test_remove_fills_with_void(tmp)
        test_replace_block(tmp)
        test_replace_merges_palette(tmp)
        test_air_stays_first(tmp)
        test_block_nbt_dropped_on_retype(tmp)
        test_block_nbt_kept_on_property_change(tmp)
        test_nbt_of_other_blocks_survives(tmp)
        test_remove_entity(tmp)
        test_retype_entity(tmp)
        test_negative_size(tmp)
        test_two_regions(tmp)
        test_metadata(tmp)
        test_file_stem()
        test_rename(tmp)
        test_rename_same_name(tmp)
        test_rename_many_regions(tmp)
        test_backup(tmp)
        test_no_backup(tmp)
        test_save_as(tmp)
        test_rejects_non_schematic(tmp)
        test_gui(tmp)
        test_gui_folder(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('')
    if FAILS:
        print('%d FAILED:' % len(FAILS))
        for f in FAILS:
            print('  ' + f)
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
