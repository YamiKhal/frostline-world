#!/usr/bin/env python3
"""Convert Litematica .litematic schematics to vanilla structure .nbt.

Why this exists: the usual .litematic -> .nbt routes flatten
`minecraft:structure_void` into `minecraft:air`. A structure placed from such a
file *deletes* terrain everywhere the void was, instead of leaving it alone.
This converter keeps voids verbatim and carries block NBT across.

Nothing is thrown away unless you say so. Entities, block NBT and every block
state come through untouched by default, whatever mod they came from. Use
`--list` to see what is in a schematic, then drop what you do not want.

Targets may be files, directories, or globs. A bare name needs no extension and
no path: the tool searches the working directory and the instance `schematics/`
folder (found by walking up from this script), so `fallen_tree_*` finds every
fallen_tree schematic wherever it lives.

Examples
    # what is actually in these files?
    python tools/cli/litematic2nbt.py --list "fallen_tree_*"

    # point and click instead
    python tools/cli/litematic2nbt.py --gui

    # convert, keeping everything
    python tools/cli/litematic2nbt.py "fallen_tree_*" -d data/frostline/structures/r1

    # a build with no voids placed: make air non-destructive
    python tools/cli/litematic2nbt.py "boulder_*" --air void -d data/frostline/structures/r1

    # drop the mob the selection box caught, keep everything else
    python tools/cli/litematic2nbt.py fallen_tree_2 --drop-entity stray -d out/

    # keep only these two entity types
    python tools/cli/litematic2nbt.py r3_shrine --keep-entity item_frame,armor_stand -d out/

Read the report line `solid N  air N  structure_void N`. If air is non-zero and
structure_void is zero, that structure will carve terrain when it generates.
"""
import argparse, glob, json, math, os, struct, sys
from collections import Counter, OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__))
_APPS = os.path.join(os.path.dirname(_HERE), 'apps')
sys.path.insert(0, _HERE)
sys.path.insert(0, _APPS)                 # litematic_convert lives in tools/apps/
import nbtio

AIR = 'minecraft:air'
VOID = 'minecraft:structure_void'
SUFFIXES = ('.litematic', '.litematica')

# Per-instance state that a structure template must not carry: identity, motion,
# and the bookkeeping a live world wrote. Rotation is deliberately NOT here -- a
# placed entity needs its facing. This is cleanup of a single entity's record,
# not a decision about which entities survive; that is yours to make.
ENTITY_JUNK = (
    'UUID', 'Pos', 'Motion', 'FallDistance', 'Fire', 'Air', 'OnGround',
    'PortalCooldown', 'HurtTime', 'HurtByTimestamp', 'DeathTime',
    'AbsorptionAmount', 'Brain', 'CanUpdate', 'forge:spawn_type',
)

# Block NBT is whatever data a block carries: chest Items, sign Text, spawner
# SpawnData, and every modded equivalent -- Create copycat material, furnace
# colour, machine inventories. None of it is filtered by id, so a block type this
# tool has never heard of keeps its data. Only these keys go: position is implied
# by the block the data hangs off, and keepPacked is a loading flag.
BLOCK_NBT_JUNK = ('x', 'y', 'z', 'keepPacked')


class ConvertError(Exception):
    pass


def qualify(entity_id):
    """`armor_stand` -> `minecraft:armor_stand`; a namespaced id is left alone."""
    return entity_id if ':' in entity_id else 'minecraft:' + entity_id


def parse_types(text):
    return {qualify(s.strip()) for s in (text or '').split(',') if s.strip()}


# --- litematic bit unpacking ---------------------------------------------

def bits_for(palette_size):
    """Litematica: max(2, ceil(log2(size)))."""
    return max(2, (palette_size - 1).bit_length())


def unpack_states(longs, bits, count):
    """LitematicaBitArray: entries are packed LSB-first and may straddle longs.

    Same layout as 1.13-1.15 chunk block storage, which is why entries are not
    long-aligned -- do not "simplify" this to 64//bits entries per long.
    """
    mask = (1 << bits) - 1
    n_longs = len(longs)
    out = []
    for idx in range(count):
        start = idx * bits
        lo = start >> 6
        hi = ((idx + 1) * bits - 1) >> 6
        off = start & 63
        if hi >= n_longs:
            raise ConvertError('BlockStates too short: need long %d of %d' % (hi, n_longs))
        a = longs[lo] & 0xFFFFFFFFFFFFFFFF
        if lo == hi:
            out.append((a >> off) & mask)
        else:
            b = longs[hi] & 0xFFFFFFFFFFFFFFFF
            out.append(((a >> off) | (b << (64 - off))) & mask)
    return out


def axis_min(pos, size):
    """World coordinate of a region's low corner on one axis.

    A region size may be negative, meaning the region grows the other way from
    Position. Litematica still stores the block array and the tile entity
    positions indexed from the region's minimum corner, whichever way it was
    dragged (LitematicaSchematic.takeBlocksFromWorld loops x + minCorner.x), so
    the size's sign only moves the corner, never the direction of the index.
    """
    if size == 0:
        raise ConvertError('region has a zero-length axis')
    return pos if size > 0 else pos + size + 1


# --- palette -------------------------------------------------------------

def state_key(entry):
    """Hashable identity for a block state compound."""
    d = entry[1]
    name = d['Name'][1]
    props = d.get('Properties')
    if props is None:
        return (name, ())
    return (name, tuple(sorted((k, v[1]) for k, v in props[1].items())))


def state_label(key):
    props = ','.join('%s=%s' % kv for kv in key[1])
    return key[0] + ('[%s]' % props if props else '')


def state_to_nbt(key):
    name, props = key
    d = OrderedDict()
    if props:
        d['Properties'] = nbtio.comp(OrderedDict((k, nbtio.string(v)) for k, v in props))
    d['Name'] = nbtio.string(name)
    return d


# --- reading -------------------------------------------------------------

class Scene(object):
    """One schematic, rasterised into a single box. No policy applied yet."""

    def __init__(self, path):
        self.path = path
        self.size = [0, 0, 0]
        self.grid = {}          # (x,y,z) -> state key; missing = no region covered it
        self.block_nbt = {}     # (x,y,z) -> block entity compound, position keys stripped
        self.entities = []      # (id, local pos, cleaned nbt dict)
        self.data_version = 3465
        self.schem_version = 0
        self.regions = 0
        self.overlaps = 0

    @property
    def volume(self):
        return self.size[0] * self.size[1] * self.size[2]

    @property
    def gaps(self):
        return self.volume - len(self.grid)

    def block_counts(self):
        """Counter of state key -> cells, gaps included as structure_void."""
        c = Counter(self.grid.values())
        if self.gaps:
            c[(VOID, ())] += self.gaps
        return c

    def entity_counts(self):
        return Counter(e[0] for e in self.entities)

    def block_nbt_counts(self):
        return Counter(d.get('id', ('str', '<no id>'))[1] for d in self.block_nbt.values())


def read_schematic(path):
    _, root = nbtio.load(path)
    c = root[1]
    if 'Regions' not in c:
        raise ConvertError('not a .litematic (no Regions tag)')

    scene = Scene(path)
    scene.schem_version = c.get('Version', ('i', 0))[1]
    scene.data_version = c.get('MinecraftDataVersion', ('i', 3465))[1]
    if scene.schem_version < 4:
        raise ConvertError('schematic version %d is older than this tool supports (need >= 4)'
                           % scene.schem_version)

    regions = c['Regions'][1]
    if not regions:
        raise ConvertError('schematic has no regions')
    scene.regions = len(regions)

    # Pass 1: bounding box over every region, in world coordinates.
    lo = [None, None, None]
    hi = [None, None, None]
    parsed = []
    for rname, reg in regions.items():
        d = reg[1]
        pos = [d['Position'][1][k][1] for k in 'xyz']
        size = [d['Size'][1][k][1] for k in 'xyz']
        mins = [axis_min(pos[i], size[i]) for i in range(3)]
        dims = [abs(s) for s in size]
        for i in range(3):
            mn = mins[i]
            mx = mn + dims[i] - 1
            lo[i] = mn if lo[i] is None else min(lo[i], mn)
            hi[i] = mx if hi[i] is None else max(hi[i], mx)
        parsed.append((rname, d, pos, mins, dims))

    scene.size = [hi[i] - lo[i] + 1 for i in range(3)]

    # Pass 2: rasterise every region into the one box.
    for rname, d, pos, mins, dims in parsed:
        pal = [state_key(e) for e in d['BlockStatePalette'][1][1]]
        w, h, l = dims
        indices = unpack_states(d['BlockStates'][1], bits_for(len(pal)), w * h * l)

        for i, si in enumerate(indices):
            if si >= len(pal):
                raise ConvertError('region %r: state index %d outside palette of %d'
                                   % (rname, si, len(pal)))
            y, rem = divmod(i, w * l)
            z, x = divmod(rem, w)
            cell = (mins[0] + x - lo[0], mins[1] + y - lo[1], mins[2] + z - lo[2])
            if cell in scene.grid:
                scene.overlaps += 1
            scene.grid[cell] = pal[si]

        for te in d.get('TileEntities', ('list', (10, [])))[1][1]:
            td = OrderedDict(te[1])
            tx, ty, tz = (td.get(k, ('i', 0))[1] for k in 'xyz')
            for k in BLOCK_NBT_JUNK:
                td.pop(k, None)
            scene.block_nbt[(mins[0] + tx - lo[0], mins[1] + ty - lo[1],
                             mins[2] + tz - lo[2])] = td

        for ent in d.get('Entities', ('list', (10, [])))[1][1]:
            ed = OrderedDict(ent[1])
            eid = ed.get('id', ('str', '<unknown>'))[1]
            raw_pos = ed.get('Pos')
            if raw_pos is None:
                continue
            p = [v[1] for v in raw_pos[1][1]]
            local = [pos[i] + p[i] - lo[i] for i in range(3)]
            for k in ENTITY_JUNK:
                ed.pop(k, None)
            scene.entities.append((eid, local, ed))

    return scene


# --- policy --------------------------------------------------------------

class Policy(object):
    """What to throw away. Every default here is "keep it"."""

    def __init__(self, air='keep', gap='void', drop_entities=(), keep_entities=None,
                 drop_blocks=(), block_nbt=True, data_version=None):
        self.air = air
        self.gap = gap
        self.drop_entities = set(drop_entities)
        self.keep_entities = None if keep_entities is None else set(keep_entities)
        self.drop_blocks = set(drop_blocks)
        self.block_nbt = block_nbt
        self.data_version = data_version

    def keeps_entity(self, eid):
        if self.keep_entities is not None:
            return eid in self.keep_entities
        return eid not in self.drop_entities

    @classmethod
    def from_args(cls, a):
        keep = parse_types(a.keep_entity) if a.keep_entity else None
        if a.no_entities:
            keep = set()
        return cls(air=a.air, gap=a.gap,
                   drop_entities=parse_types(a.drop_entity),
                   keep_entities=keep,
                   drop_blocks=parse_types(a.drop_block),
                   block_nbt=not a.no_block_nbt,
                   data_version=a.data_version)


def build(scene, policy):
    """Scene + policy -> (structure nbt root, report)."""
    report = {
        'path': scene.path, 'size': scene.size, 'regions': scene.regions,
        'data_version': policy.data_version or scene.data_version,
        'gaps': scene.gaps, 'overlaps': scene.overlaps,
        'entities_kept': Counter(), 'entities_dropped': Counter(),
        'block_nbt': Counter(), 'blocks_dropped': Counter(), 'warnings': [],
    }
    dim = scene.size

    entities = []
    for eid, local, ed in scene.entities:
        if not policy.keeps_entity(eid):
            report['entities_dropped'][eid] += 1
            continue
        nbt = OrderedDict(ed)
        nbt['Pos'] = nbtio.dbl_list(local)
        entities.append(OrderedDict([
            ('pos', nbtio.dbl_list(local)),
            ('blockPos', nbtio.int_list([math.floor(v) for v in local])),
            ('nbt', nbtio.comp(nbt)),
        ]))
        report['entities_kept'][eid] += 1

    pal_index = OrderedDict()
    blocks = []
    counts = Counter()
    attached = 0

    def index_of(key):
        if key not in pal_index:
            pal_index[key] = len(pal_index)
        return pal_index[key]

    for y in range(dim[1]):
        for z in range(dim[2]):
            for x in range(dim[0]):
                cell = (x, y, z)
                key = scene.grid.get(cell)
                omit = False
                if key is None:
                    omit = policy.gap == 'drop'
                    key = (VOID, ())
                elif key[0] in policy.drop_blocks:
                    report['blocks_dropped'][key[0]] += 1
                    key = (VOID, ())
                elif key[0] == AIR:
                    if policy.air == 'drop':
                        omit = True
                    elif policy.air == 'void':
                        key = (VOID, ())
                if omit:
                    continue
                counts[key[0]] += 1
                entry = OrderedDict([
                    ('state', nbtio.i32(index_of(key))),
                    ('pos', nbtio.int_list(cell)),
                ])
                if policy.block_nbt and cell in scene.block_nbt:
                    entry['nbt'] = nbtio.comp(scene.block_nbt[cell])
                    attached += 1
                    report['block_nbt'][
                        scene.block_nbt[cell].get('id', ('str', '<no id>'))[1]] += 1
                blocks.append(entry)

    if policy.block_nbt:
        orphans = len(scene.block_nbt) - attached
        if orphans > 0:
            report['warnings'].append('%d blocks with NBT did not land on a written block; '
                                      'their data was dropped' % orphans)

    report['counts'] = counts
    report['blocks'] = len(blocks)
    report['palette'] = list(pal_index.keys())

    mods = sorted({k[0].split(':')[0] for k in pal_index if not k[0].startswith('minecraft:')})
    if mods:
        report['warnings'].append('non-vanilla block namespaces: %s (the pack hard-depends '
                                  'on those mods)' % ', '.join(mods))
    ent_mods = sorted({e.split(':')[0] for e in report['entities_kept']
                       if not e.startswith('minecraft:')})
    if ent_mods:
        report['warnings'].append('non-vanilla entity namespaces kept: %s'
                                  % ', '.join(ent_mods))
    if max(dim) > 48:
        report['warnings'].append('size %s exceeds 48 on an axis: a structure block cannot '
                                  'load it, worldgen templates are fine' % dim)
    if policy.air == 'keep' and counts.get(AIR) and not counts.get(VOID):
        report['warnings'].append('%d air blocks and no structure_void: this template will '
                                  'carve terrain. Place voids in Litematica, or pass '
                                  '--air void.' % counts[AIR])

    root = ('comp', OrderedDict([
        ('size', nbtio.int_list(dim)),
        ('entities', nbtio.comp_list(entities)),
        ('blocks', nbtio.comp_list(blocks)),
        ('palette', nbtio.comp_list([state_to_nbt(k) for k in pal_index])),
        ('DataVersion', nbtio.i32(report['data_version'])),
    ]))
    return root, report


def convert(path, policy):
    return build(read_schematic(path), policy)


# --- finding inputs ------------------------------------------------------

def default_roots():
    """Working directory, plus the instance `schematics/` folder if there is one.

    Lets `fallen_tree_*` work from anywhere in the pack without a path.
    """
    roots = [os.getcwd()]
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        cand = os.path.join(d, 'schematics')
        if os.path.isdir(cand):
            roots.append(cand)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    seen, out = set(), []
    for r in roots:
        key = os.path.normcase(os.path.abspath(r))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def expand(pattern):
    """Glob a pattern, retrying with each schematic suffix appended."""
    hits = [h for h in glob.glob(pattern, recursive=True) if h.lower().endswith(SUFFIXES)]
    if not hits:
        for suffix in SUFFIXES:
            hits += glob.glob(pattern + suffix, recursive=True)
    return sorted(set(hits))


def walk_dir(path):
    out = []
    for dirpath, _, names in os.walk(path):
        out += [os.path.join(dirpath, n) for n in sorted(names)
                if n.lower().endswith(SUFFIXES)]
    return out


def collect(patterns, roots=None):
    roots = roots or default_roots()
    files = []
    for pat in patterns:
        if os.path.isdir(pat):
            hits = walk_dir(pat)
        else:
            hits = expand(pat)
            # A bare name or wildcard with no directory part: search the roots.
            # Nearest root wins, so a match in the working directory is never
            # mixed with same-named schematics found further up.
            if not hits and not os.path.dirname(pat):
                for root in roots:
                    hits = expand(os.path.join(root, '**', pat))
                    if hits:
                        break
        if not hits:
            raise ConvertError('no schematic matched: %s' % pat)
        files += hits
    seen, out = set(), []
    for f in files:
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


# --- listing -------------------------------------------------------------

def inventory(scene):
    """Everything in the schematic, before any policy is applied."""
    return OrderedDict([
        ('file', scene.path),
        ('size', list(scene.size)),
        ('volume', scene.volume),
        ('regions', scene.regions),
        ('data_version', scene.data_version),
        ('schematic_version', scene.schem_version),
        ('uncovered_cells', scene.gaps),
        ('overlapping_cells', scene.overlaps),
        ('blocks', OrderedDict((state_label(k), n) for k, n in
                               sorted(scene.block_counts().items(),
                                      key=lambda kv: (-kv[1], kv[0])))),
        ('entities', OrderedDict(sorted(scene.entity_counts().items(),
                                        key=lambda kv: (-kv[1], kv[0])))),
        ('block_nbt', OrderedDict(sorted(scene.block_nbt_counts().items(),
                                         key=lambda kv: (-kv[1], kv[0])))),
    ])


def print_inventory(inv):
    print(os.path.basename(inv['file']))
    print('   size %dx%dx%d  volume %d  regions %d  DataVersion %d  schematic v%d'
          % (inv['size'][0], inv['size'][1], inv['size'][2], inv['volume'],
             inv['regions'], inv['data_version'], inv['schematic_version']))
    if inv['uncovered_cells']:
        print('   %d cells covered by no region (they become structure_void)'
              % inv['uncovered_cells'])
    if inv['overlapping_cells']:
        print('   %d cells written by more than one region (last region wins)'
              % inv['overlapping_cells'])

    def table(title, rows, empty):
        print('   %s' % title)
        if not rows:
            print('      %s' % empty)
            return
        width = max(len(k) for k in rows)
        for k, n in rows.items():
            print('      %-*s  %6d' % (width, k, n))

    table('BLOCKS (%d states)' % len(inv['blocks']), inv['blocks'], 'none')
    table('ENTITIES (%d types)' % len(inv['entities']), inv['entities'], 'none')
    table('BLOCK NBT (%d types)' % len(inv['block_nbt']),
          inv['block_nbt'], 'none')


# --- reporting -----------------------------------------------------------

def describe(r, verbose):
    print(os.path.basename(r['path']))
    print('   size %dx%dx%d  blocks %d  palette %d  regions %d  DataVersion %d'
          % (r['size'][0], r['size'][1], r['size'][2], r['blocks'],
             len(r['palette']), r['regions'], r['data_version']))
    c = r['counts']
    solid = sum(v for k, v in c.items() if k not in (AIR, VOID))
    print('   solid %d   air %d   structure_void %d   uncovered cells %d'
          % (solid, c.get(AIR, 0), c.get(VOID, 0), r['gaps']))

    def line(label, counter):
        if counter:
            print('   %s: %s' % (label, ', '.join(
                '%s x%d' % (k.split(':')[-1], v) for k, v in sorted(counter.items()))))

    line('block NBT kept', r['block_nbt'])
    line('entities kept', r['entities_kept'])
    line('entities dropped', r['entities_dropped'])
    line('blocks dropped', r['blocks_dropped'])
    if r['overlaps']:
        print('   note: %d cells written by more than one region (last region wins)'
              % r['overlaps'])
    if verbose:
        for k in r['palette']:
            print('      %s' % state_label(k))
    for w in r['warnings']:
        print('   WARNING: %s' % w)


# --- cli -----------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Convert Litematica .litematic files to vanilla structure .nbt, '
                    'preserving structure_void. Nothing is dropped unless you ask.',
        epilog='examples:\n'
               '  %(prog)s --gui\n'
               '  %(prog)s --list "fallen_tree_*"\n'
               '  %(prog)s "fallen_tree_*" -d data/frostline/structures/r1\n'
               '  %(prog)s "boulder_*" --air void -d data/frostline/structures/r1\n'
               '  %(prog)s fallen_tree_2 --drop-entity stray -d out/\n')
    ap.add_argument('inputs', nargs='*',
                    help='.litematic files, wildcards (fallen_tree_*), or directories. '
                         'The extension is optional, and a name with no path is searched '
                         'for under the working directory and the instance schematics/ folder.')
    ap.add_argument('--gui', action='store_true',
                    help='open the point-and-click window instead (it does everything below)')
    ap.add_argument('--list', dest='do_list', action='store_true',
                    help='print every block state, entity and block entity, and convert '
                         'nothing')
    ap.add_argument('--json', action='store_true', help='with --list, emit JSON')
    ap.add_argument('-o', '--out', help='output file (single input only)')
    ap.add_argument('-d', '--outdir', help='output directory (default: alongside the input)')
    ap.add_argument('--air', choices=('keep', 'void', 'drop'), default='keep',
                    help='what plain air becomes: keep it (carves terrain), turn it into '
                         'structure_void, or omit it entirely (default: keep)')
    ap.add_argument('--gap', choices=('void', 'drop'), default='void',
                    help='cells no region covers, for multi-region schematics (default: void)')
    ap.add_argument('--drop-entity', metavar='TYPES', default='',
                    help='entity types to drop, e.g. --drop-entity stray,marker. '
                         'Everything else is kept.')
    ap.add_argument('--keep-entity', metavar='TYPES', default='',
                    help='keep ONLY these entity types and drop the rest')
    ap.add_argument('--no-entities', action='store_true', help='drop every entity')
    ap.add_argument('--drop-block', metavar='BLOCKS', default='',
                    help='block types to replace with structure_void, e.g. '
                         '--drop-block grass_block,dirt')
    ap.add_argument('--no-block-nbt', '--no-block-entities', dest='no_block_nbt',
                    action='store_true',
                    help='discard the data blocks carry -- chest contents, sign text, '
                         'spawner settings, and any modded block data')
    ap.add_argument('--root', action='append', default=[], metavar='DIR',
                    help='extra directory to search for bare names (repeatable)')
    ap.add_argument('--data-version', type=int, help='override DataVersion')
    ap.add_argument('--dry-run', action='store_true', help='report only, write nothing')
    ap.add_argument('-q', '--quiet', action='store_true')
    ap.add_argument('-v', '--verbose', action='store_true', help='list the output palette')
    a = ap.parse_args(argv)

    if a.gui:
        import litematic_convert
        return litematic_convert.run(a.inputs, outdir=a.outdir)
    # Reported rather than raised through argparse, so main() stays callable as
    # a function (the tests drive it directly).
    if not a.inputs:
        print('give at least one target, or --gui (see --help)', file=sys.stderr)
        return 2
    if a.keep_entity and a.drop_entity:
        print('--keep-entity and --drop-entity contradict each other; pick one',
              file=sys.stderr)
        return 2

    try:
        files = collect(a.inputs, a.root + default_roots())
    except ConvertError as e:
        print(e, file=sys.stderr)
        return 2
    if a.out and len(files) > 1 and not a.do_list:
        print('-o takes a single input, but %d matched; use -d for batches' % len(files),
              file=sys.stderr)
        return 2

    policy = Policy.from_args(a)
    failures = 0
    invs = []

    for f in files:
        try:
            scene = read_schematic(f)
            if a.do_list:
                invs.append(inventory(scene))
                continue
            root, report = build(scene, policy)
        except (ConvertError, ValueError, KeyError, IndexError, struct.error) as e:
            print('%s: FAILED: %s' % (os.path.basename(f), e), file=sys.stderr)
            failures += 1
            continue
        dest = a.out or os.path.join(
            a.outdir or os.path.dirname(f) or '.',
            os.path.splitext(os.path.basename(f))[0] + '.nbt')
        if not a.quiet:
            describe(report, a.verbose)
        if a.dry_run:
            if not a.quiet:
                print('   dry run, would write %s' % dest)
            continue
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        nbtio.save(dest, '', root)
        if not a.quiet:
            print('   -> %s (%d bytes)' % (dest, os.path.getsize(dest)))

    if a.do_list:
        if a.json:
            print(json.dumps(invs if len(invs) != 1 else invs[0], indent=2))
        else:
            for inv in invs:
                print_inventory(inv)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
