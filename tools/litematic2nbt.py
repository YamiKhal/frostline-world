#!/usr/bin/env python3
"""Convert Litematica .litematic schematics to vanilla structure .nbt.

Why this exists: the usual .litematic -> .nbt routes flatten
`minecraft:structure_void` into `minecraft:air`. A structure placed from such a
file *deletes* terrain everywhere the void was, instead of leaving it alone.
This converter keeps voids verbatim, carries block-entity data across, merges
multi-region schematics, drops the junk entities that a world selection sweeps
up, and reports what it did.

Targets may be files, directories, or globs. A bare name needs no extension and
no path: the tool searches the working directory and the instance `schematics/`
folder (found by walking up from this script), so `fallen_tree_*` finds every
fallen_tree schematic wherever it lives.

Examples
    # look before you leap: report every schematic, write nothing
    python tools/litematic2nbt.py schematics -q --dry-run

    # one file, explicit output path
    python tools/litematic2nbt.py schematics/tree/r1/fallen_tree_1.litematic \
        -o data/frostline/structures/r1/fallen_tree_1.nbt

    # a family of files by wildcard, into the datapack
    python tools/litematic2nbt.py "fallen_tree_*" -d data/frostline/structures/r1

    # a whole tree at once
    python tools/litematic2nbt.py schematics/tree/r1 -d data/frostline/structures/r1

    # a build with no voids placed: make air non-destructive
    python tools/litematic2nbt.py "boulder_*" --air void -d data/frostline/structures/r1

    # a piece that ships item frames and armour stands, nothing else
    python tools/litematic2nbt.py r3_shrine --keep-entity item_frame,armor_stand \
        -d data/frostline/structures/r3

    # keep every entity the selection caught, except markers
    python tools/litematic2nbt.py r3_shrine --keep-entities -d data/frostline/structures/r3

Read the report line `solid N  air N  structure_void N`. If air is non-zero and
structure_void is zero, that structure will carve terrain when it generates.
"""
import argparse, glob, math, os, struct, sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nbtio

AIR = 'minecraft:air'
VOID = 'minecraft:structure_void'
SUFFIXES = ('.litematic', '.litematica')

# Per-instance state that a structure template must not carry: identity, motion,
# and the bookkeeping a live world wrote. Rotation is deliberately NOT here -- a
# placed entity needs its facing.
ENTITY_JUNK = (
    'UUID', 'Pos', 'Motion', 'FallDistance', 'Fire', 'Air', 'OnGround',
    'PortalCooldown', 'HurtTime', 'HurtByTimestamp', 'DeathTime',
    'AbsorptionAmount', 'Brain', 'CanUpdate', 'forge:spawn_type',
)

# Position is implied by the block the data hangs off, and keepPacked is a
# loading flag. Everything else -- Items, Text, SpawnData, Lock -- is kept.
BLOCK_ENTITY_JUNK = ('x', 'y', 'z', 'keepPacked')


class ConvertError(Exception):
    pass


def qualify(entity_id):
    """`armor_stand` -> `minecraft:armor_stand`; a namespaced id is left alone."""
    return entity_id if ':' in entity_id else 'minecraft:' + entity_id


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


def axis_span(pos, size):
    """A region size may be negative, meaning the region grows the other way.

    Returns (world_min, step). With step -1 the local index 0 sits at the high
    end, so local i maps to world_min + (extent - 1 - i).
    """
    if size == 0:
        raise ConvertError('region has a zero-length axis')
    if size > 0:
        return pos, 1
    return pos + size + 1, -1


# --- palette -------------------------------------------------------------

def state_key(entry):
    """Hashable identity for a block state compound."""
    d = entry[1]
    name = d['Name'][1]
    props = d.get('Properties')
    if props is None:
        return (name, ())
    return (name, tuple(sorted((k, v[1]) for k, v in props[1].items())))


def state_to_nbt(key):
    name, props = key
    d = OrderedDict()
    if props:
        d['Properties'] = nbtio.comp(OrderedDict((k, nbtio.string(v)) for k, v in props))
    d['Name'] = nbtio.string(name)
    return d


# --- conversion ----------------------------------------------------------

def keep_entity(eid, opts):
    if opts.keep_only:
        return eid in opts.keep_only
    if not opts.keep_entities:
        return False
    return eid not in opts.drop_types


def convert(path, opts):
    _, root = nbtio.load(path)
    c = root[1]
    if 'Regions' not in c:
        raise ConvertError('not a .litematic (no Regions tag)')

    schem_version = c.get('Version', ('i', 0))[1]
    data_version = opts.data_version or c.get('MinecraftDataVersion', ('i', 3465))[1]
    if schem_version < 4:
        raise ConvertError('schematic version %d is older than this tool supports (need >= 4)'
                           % schem_version)

    regions = c['Regions'][1]
    if not regions:
        raise ConvertError('schematic has no regions')

    report = {
        'path': path, 'schem_version': schem_version, 'data_version': data_version,
        'regions': len(regions), 'entities_dropped': Counter(),
        'entities_kept': 0, 'block_entities': Counter(), 'warnings': [], 'overlaps': 0,
    }

    # Pass 1: bounding box over every region, in world coordinates.
    lo = [None, None, None]
    hi = [None, None, None]
    parsed = []
    for rname, reg in regions.items():
        d = reg[1]
        pos = [d['Position'][1][k][1] for k in 'xyz']
        size = [d['Size'][1][k][1] for k in 'xyz']
        spans = [axis_span(pos[i], size[i]) for i in range(3)]
        dims = [abs(s) for s in size]
        for i in range(3):
            mn = spans[i][0]
            mx = mn + dims[i] - 1
            lo[i] = mn if lo[i] is None else min(lo[i], mn)
            hi[i] = mx if hi[i] is None else max(hi[i], mx)
        parsed.append((rname, d, pos, spans, dims))

    dim = [hi[i] - lo[i] + 1 for i in range(3)]
    report['size'] = dim

    # Pass 2: rasterise every region into one grid keyed by local coordinate.
    grid = {}
    block_nbt = {}
    entities = []

    for rname, d, pos, spans, dims in parsed:
        pal = [state_key(e) for e in d['BlockStatePalette'][1][1]]
        w, h, l = dims
        volume = w * h * l
        indices = unpack_states(d['BlockStates'][1], bits_for(len(pal)), volume)

        for i, si in enumerate(indices):
            if si >= len(pal):
                raise ConvertError('region %r: state index %d outside palette of %d'
                                   % (rname, si, len(pal)))
            y, rem = divmod(i, w * l)
            z, x = divmod(rem, w)
            wx = spans[0][0] + (x if spans[0][1] > 0 else w - 1 - x)
            wy = spans[1][0] + (y if spans[1][1] > 0 else h - 1 - y)
            wz = spans[2][0] + (z if spans[2][1] > 0 else l - 1 - z)
            cell = (wx - lo[0], wy - lo[1], wz - lo[2])
            if cell in grid:
                report['overlaps'] += 1
            grid[cell] = pal[si]

        if not opts.no_block_entities:
            for te in d.get('TileEntities', ('list', (10, [])))[1][1]:
                td = OrderedDict(te[1])
                tx, ty, tz = (td.get(k, ('i', 0))[1] for k in 'xyz')
                for k in BLOCK_ENTITY_JUNK:
                    td.pop(k, None)
                cell = (pos[0] + tx - lo[0], pos[1] + ty - lo[1], pos[2] + tz - lo[2])
                block_nbt[cell] = td
                report['block_entities'][td.get('id', ('str', '<no id>'))[1]] += 1

        for ent in d.get('Entities', ('list', (10, [])))[1][1]:
            ed = OrderedDict(ent[1])
            eid = ed.get('id', ('str', '<unknown>'))[1]
            raw_pos = ed.get('Pos')
            if not keep_entity(eid, opts) or raw_pos is None:
                report['entities_dropped'][eid] += 1
                continue
            p = [v[1] for v in raw_pos[1][1]]
            local = [pos[i] + p[i] - lo[i] for i in range(3)]
            for k in ENTITY_JUNK:
                ed.pop(k, None)
            ed['Pos'] = nbtio.dbl_list(local)
            entities.append(OrderedDict([
                ('pos', nbtio.dbl_list(local)),
                ('blockPos', nbtio.int_list([math.floor(v) for v in local])),
                ('nbt', nbtio.comp(ed)),
            ]))
            report['entities_kept'] += 1

    report['gaps'] = dim[0] * dim[1] * dim[2] - len(grid)

    # Pass 3: apply the air/gap policy and build the output palette.
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
                key = grid.get(cell)
                dropped = False
                if key is None:
                    if opts.gap == 'drop':
                        dropped = True
                    key = (VOID, ())
                elif key[0] == AIR:
                    if opts.air == 'drop':
                        dropped = True
                    elif opts.air == 'void':
                        key = (VOID, ())
                if dropped:
                    if cell in block_nbt:
                        report['warnings'].append(
                            'block entity at %s sat on a cell the air/gap policy dropped'
                            % (cell,))
                    continue
                counts[key[0]] += 1
                entry = OrderedDict([
                    ('state', nbtio.i32(index_of(key))),
                    ('pos', nbtio.int_list(cell)),
                ])
                if cell in block_nbt:
                    entry['nbt'] = nbtio.comp(block_nbt[cell])
                    attached += 1
                blocks.append(entry)

    orphans = sum(report['block_entities'].values()) - attached
    if orphans > 0:
        report['warnings'].append('%d block entities did not land on a written block; '
                                  'their data was dropped' % orphans)

    report['counts'] = counts
    report['blocks'] = len(blocks)
    report['palette'] = list(pal_index.keys())

    mods = sorted({k[0].split(':')[0] for k in pal_index if not k[0].startswith('minecraft:')})
    if mods:
        report['warnings'].append('non-vanilla block namespaces: %s (the pack hard-depends '
                                  'on those mods)' % ', '.join(mods))
    if max(dim) > 48:
        report['warnings'].append('size %s exceeds 48 on an axis: a structure block cannot '
                                  'load it, worldgen templates are fine' % dim)
    if opts.air == 'keep' and counts.get(AIR) and not counts.get(VOID):
        report['warnings'].append('%d air blocks and no structure_void: this template will '
                                  'carve terrain. Place voids in Litematica, or pass '
                                  '--air void.' % counts[AIR])

    out = OrderedDict([
        ('size', nbtio.int_list(dim)),
        ('entities', nbtio.comp_list(entities)),
        ('blocks', nbtio.comp_list(blocks)),
        ('palette', nbtio.comp_list([state_to_nbt(k) for k in pal_index])),
        ('DataVersion', nbtio.i32(data_version)),
    ])
    return ('comp', out), report


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
    hits = [h for h in glob.glob(pattern, recursive=True)
            if h.lower().endswith(SUFFIXES)]
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


def collect(patterns, roots):
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


# --- cli -----------------------------------------------------------------

def describe(r, verbose):
    print(os.path.basename(r['path']))
    print('   size %dx%dx%d  blocks %d  palette %d  regions %d  DataVersion %d'
          % (r['size'][0], r['size'][1], r['size'][2], r['blocks'],
             len(r['palette']), r['regions'], r['data_version']))
    c = r['counts']
    solid = sum(v for k, v in c.items() if k not in (AIR, VOID))
    print('   solid %d   air %d   structure_void %d   uncovered cells %d'
          % (solid, c.get(AIR, 0), c.get(VOID, 0), r['gaps']))
    if r['block_entities']:
        print('   block entities kept: %s'
              % ', '.join('%s x%d' % (k.split(':')[-1], v)
                          for k, v in sorted(r['block_entities'].items())))
    if r['entities_kept']:
        print('   entities kept: %d' % r['entities_kept'])
    if r['entities_dropped']:
        print('   entities dropped: %s'
              % ', '.join('%s x%d' % (k.split(':')[-1], v)
                          for k, v in sorted(r['entities_dropped'].items())))
    if r['overlaps']:
        print('   note: %d cells written by more than one region (last region wins)'
              % r['overlaps'])
    if verbose:
        for k in r['palette']:
            props = ','.join('%s=%s' % kv for kv in k[1])
            print('      %s' % (k[0] + ('[%s]' % props if props else '')))
    for w in r['warnings']:
        print('   WARNING: %s' % w)


def main(argv=None):
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Convert Litematica .litematic files to vanilla structure .nbt, '
                    'preserving structure_void.',
        epilog='examples:\n'
               '  %(prog)s schematics -q --dry-run\n'
               '  %(prog)s "fallen_tree_*" -d data/frostline/structures/r1\n'
               '  %(prog)s "boulder_*" --air void -d data/frostline/structures/r1\n'
               '  %(prog)s r3_shrine --keep-entity item_frame,armor_stand -d out/\n')
    ap.add_argument('inputs', nargs='+',
                    help='.litematic files, wildcards (fallen_tree_*), or directories. '
                         'The extension is optional, and a name with no path is searched '
                         'for under the working directory and the instance schematics/ folder.')
    ap.add_argument('-o', '--out', help='output file (single input only)')
    ap.add_argument('-d', '--outdir', help='output directory (default: alongside the input)')
    ap.add_argument('--air', choices=('keep', 'void', 'drop'), default='keep',
                    help='what plain air becomes: keep it (carves terrain), turn it into '
                         'structure_void, or omit it entirely (default: keep)')
    ap.add_argument('--gap', choices=('void', 'drop'), default='void',
                    help='cells no region covers, for multi-region schematics (default: void)')
    ap.add_argument('--keep-entity', dest='keep_only', metavar='TYPES', default='',
                    help='keep ONLY these entity types and drop the rest, e.g. '
                         '--keep-entity item_frame,armor_stand. The minecraft: namespace '
                         'is assumed when none is given.')
    ap.add_argument('--keep-entities', action='store_true',
                    help='keep every entity except --drop-types; by default all are dropped')
    ap.add_argument('--drop-types', default='minecraft:marker',
                    help='entity types to drop even with --keep-entities '
                         '(default: minecraft:marker)')
    ap.add_argument('--no-block-entities', action='store_true',
                    help='also discard chest/sign/spawner contents')
    ap.add_argument('--root', action='append', default=[], metavar='DIR',
                    help='extra directory to search for bare names (repeatable)')
    ap.add_argument('--data-version', type=int, help='override DataVersion')
    ap.add_argument('--dry-run', action='store_true', help='report only, write nothing')
    ap.add_argument('-q', '--quiet', action='store_true')
    ap.add_argument('-v', '--verbose', action='store_true', help='list the palette')
    opts = ap.parse_args(argv)

    opts.drop_types = {qualify(s.strip()) for s in opts.drop_types.split(',') if s.strip()}
    opts.keep_only = {qualify(s.strip()) for s in opts.keep_only.split(',') if s.strip()}
    if opts.keep_only and opts.keep_entities:
        print('--keep-entity and --keep-entities contradict each other; pick one',
              file=sys.stderr)
        return 2

    try:
        files = collect(opts.inputs, opts.root + default_roots())
    except ConvertError as e:
        print(e, file=sys.stderr)
        return 2
    if opts.out and len(files) > 1:
        print('-o takes a single input, but %d matched; use -d for batches' % len(files),
              file=sys.stderr)
        return 2

    failures = 0
    for f in files:
        try:
            root, report = convert(f, opts)
        except (ConvertError, ValueError, KeyError, IndexError, struct.error) as e:
            print('%s: FAILED: %s' % (os.path.basename(f), e), file=sys.stderr)
            failures += 1
            continue
        dest = opts.out or os.path.join(
            opts.outdir or os.path.dirname(f) or '.',
            os.path.splitext(os.path.basename(f))[0] + '.nbt')
        if not opts.quiet:
            describe(report, opts.verbose)
        if opts.dry_run:
            if not opts.quiet:
                print('   dry run, would write %s' % dest)
            continue
        parent = os.path.dirname(os.path.abspath(dest))
        os.makedirs(parent, exist_ok=True)
        nbtio.save(dest, '', root)
        if not opts.quiet:
            print('   -> %s (%d bytes)' % (dest, os.path.getsize(dest)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
