"""An editable .litematic, in .litematic form. Backs tools/apps/litematic_edit.py.

`litematic2nbt.Scene` rasterises every region into one box and throws the file's
own structure away -- right for converting, useless for editing, because saving
it back would need the regions reassembled. This keeps the loaded NBT tree and
rewrites only what an edit touches, so region layout, metadata, pending ticks
and every tag this tool has never heard of survive a load/save round trip byte
for byte.

An edit is a *plan*, not a mutation: `apply()` returns a fresh root and leaves
the document alone, so the same document can be re-planned as often as you like
and nothing is committed until `save()`.

Two rules the format imposes:

  * A block's data (chest Items, sign Text, a modded block's tile entity) hangs
    off a position, not off the palette entry. Change what block is at that
    position and the data is orphaned -- it would be read as the new block's
    data, which it is not. So a replacement that changes the block *name* drops
    the tile entity, and `apply()` reports every one it dropped.
  * Same for entities: an entity's NBT is its species. `minecraft:stray` NBT on
    a `minecraft:marker` is not a marker. Renaming an entity keeps only the
    placement tags (`Pos`, `Motion`, `Rotation`) and drops the rest.
"""
import os
import sys
import time
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nbtio
import litematic2nbt as L

AIR = L.AIR
VOID = L.VOID
DELETE = object()          # plan value meaning "take this out"

# What survives an entity keeping its identity but changing its id. Placement
# only: everything else described the old species.
ENTITY_PLACEMENT = ('Pos', 'Motion', 'Rotation', 'TileX', 'TileY', 'TileZ', 'Facing')


class EditError(Exception):
    pass


def pack_states(indices, bits):
    """Inverse of litematic2nbt.unpack_states -> signed longs, ready for NBT.

    Entries are LSB-first and straddle longs; the array is
    ceil(count * bits / 64) longs, the same length Litematica allocates.
    """
    n = len(indices)
    longs = [0] * ((n * bits + 63) // 64)
    mask = (1 << bits) - 1
    for idx, value in enumerate(indices):
        value &= mask
        start = idx * bits
        lo = start >> 6
        hi = ((idx + 1) * bits - 1) >> 6
        off = start & 63
        longs[lo] |= (value << off) & 0xFFFFFFFFFFFFFFFF
        if hi != lo:
            longs[hi] |= value >> (64 - off)
    return [v - (1 << 64) if v >= (1 << 63) else v for v in longs]


def parse_state(text):
    """`stone`, `minecraft:stone`, `minecraft:oak_log[axis=y]` -> a state key."""
    text = (text or '').strip()
    if not text:
        raise EditError('give a block id')
    props = ()
    if text.endswith(']'):
        head, _, tail = text.partition('[')
        if not head:
            raise EditError('%r has properties but no block id' % text)
        pairs = []
        for part in tail[:-1].split(','):
            part = part.strip()
            if not part:
                continue
            k, eq, v = part.partition('=')
            if not eq:
                raise EditError('property %r is not key=value' % part)
            pairs.append((k.strip(), v.strip()))
        props = tuple(sorted(pairs))
        text = head
    if ' ' in text.strip():
        raise EditError('%r is not a block id' % text)
    return (L.qualify(text.strip()), props)


def _cell_index(x, y, z, dims):
    w, _h, l = dims
    return y * w * l + z * w + x


class Region(object):
    """One region of the file, decoded far enough to edit."""

    def __init__(self, name, comp):
        self.name = name
        self.comp = comp
        d = comp[1]
        self.size = [d['Size'][1][k][1] for k in 'xyz']
        self.dims = [abs(s) for s in self.size]
        self.palette = [L.state_key(e) for e in d['BlockStatePalette'][1][1]]
        if not self.palette:
            raise EditError('region %r has an empty palette' % name)
        self.cells = self.dims[0] * self.dims[1] * self.dims[2]
        self.indices = L.unpack_states(d['BlockStates'][1],
                                       L.bits_for(len(self.palette)), self.cells)

    @property
    def tile_entities(self):
        return self.comp[1].get('TileEntities', ('list', (nbtio.TAG_COMP, [])))[1][1]

    @property
    def entities(self):
        return self.comp[1].get('Entities', ('list', (nbtio.TAG_COMP, [])))[1][1]

    def tile_entity_cells(self):
        """-> {cell index: tile entity compound}. Positions are region-local."""
        out = {}
        for te in self.tile_entities:
            x, y, z = (te[1].get(k, ('i', 0))[1] for k in 'xyz')
            out[_cell_index(x, y, z, self.dims)] = te
        return out


class Doc(object):
    """A loaded .litematic. Read it, plan edits against it, save the result."""

    def __init__(self, path, root_name, root):
        self.path = path
        self.root_name = root_name
        self.root = root
        c = root[1]
        if 'Regions' not in c:
            raise EditError('not a .litematic (no Regions tag)')
        self.version = c.get('Version', ('i', 0))[1]
        if self.version < 4:
            raise EditError('schematic version %d is older than this tool supports '
                            '(need >= 4)' % self.version)
        self.regions = [Region(n, r) for n, r in c['Regions'][1].items()]
        if not self.regions:
            raise EditError('schematic has no regions')

    @classmethod
    def load(cls, path):
        name, root = nbtio.load(path)
        return cls(path, name, root)

    # -- what is in here ---------------------------------------------------
    @property
    def meta(self):
        return self.root[1].get('Metadata', ('comp', OrderedDict()))[1]

    @property
    def name(self):
        return self.meta.get('Name', ('str', ''))[1]

    @property
    def size(self):
        e = self.meta.get('EnclosingSize')
        if e is None:
            return [max(r.dims[i] for r in self.regions) for i in range(3)]
        return [abs(e[1][k][1]) for k in 'xyz']

    def block_rows(self):
        """-> [(state key, cells, cells carrying block NBT)], commonest first."""
        cells = Counter()
        with_nbt = Counter()
        for region in self.regions:
            for si in region.indices:
                cells[region.palette[si]] += 1
            for cell in region.tile_entity_cells():
                if 0 <= cell < len(region.indices):
                    with_nbt[region.palette[region.indices[cell]]] += 1
        return [(key, n, with_nbt.get(key, 0))
                for key, n in sorted(cells.items(),
                                     key=lambda kv: (-kv[1], L.state_label(kv[0])))]

    def entity_rows(self):
        """-> [(entity id, count, how many carry NBT beyond placement)]."""
        count = Counter()
        rich = Counter()
        for region in self.regions:
            for ent in region.entities:
                eid = ent[1].get('id', ('str', '<unknown>'))[1]
                count[eid] += 1
                if entity_extra_keys(ent[1]):
                    rich[eid] += 1
        return [(eid, n, rich.get(eid, 0))
                for eid, n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))]

    def orphan_nbt_count(self):
        """Tile entities whose position is outside the region box, if any."""
        n = 0
        for region in self.regions:
            for cell in region.tile_entity_cells():
                if not 0 <= cell < len(region.indices):
                    n += 1
        return n

    # -- editing -----------------------------------------------------------
    def apply(self, blocks=None, entities=None, fill=(AIR, ()), name=None):
        """Plan -> (new root, report). This document is not modified.

        blocks:   {state key: state key | DELETE}. A key the plan does not name
                  is left alone. DELETE fills the cell with `fill`.
        entities: {entity id: entity id | DELETE}.
        fill:     the state a deleted block becomes -- air, or structure_void if
                  you want the cell to leave terrain alone once converted.
        name:     the schematic's name, the one Litematica's list shows. A
                  single-region schematic gets its region renamed to match,
                  which is what Litematica does when it saves one; a file with
                  several regions keeps its region names, because there is no
                  one of them the name belongs to.
        """
        blocks = dict(blocks or {})
        entities = dict(entities or {})
        report = {
            'blocks_replaced': Counter(), 'blocks_deleted': Counter(),
            'entities_replaced': Counter(), 'entities_deleted': Counter(),
            'block_nbt_dropped': Counter(), 'entity_nbt_stripped': Counter(),
            'ticks_dropped': 0, 'renamed': None, 'region_renamed': None,
            'warnings': [],
        }

        new_regions = OrderedDict()
        total_blocks = 0
        for region in self.regions:
            comp, placed = self._apply_region(region, blocks, entities, fill, report)
            new_regions[region.name] = comp
            total_blocks += placed

        if name is not None and name != self.name:
            report['renamed'] = (self.name, name)
            if len(self.regions) == 1:
                only = self.regions[0].name
                if only != name:
                    report['region_renamed'] = (only, name)
                    new_regions = OrderedDict([(name, new_regions[only])])
            elif len(self.regions) > 1:
                report['warnings'].append(
                    'the file has %d regions, so their names are left as they are'
                    % len(self.regions))

        root = ('comp', OrderedDict(self.root[1]))
        root[1]['Regions'] = ('comp', new_regions)
        meta = OrderedDict(self.meta)
        if report['renamed']:
            meta['Name'] = nbtio.string(name)
        if 'TotalBlocks' in meta:
            meta['TotalBlocks'] = ('i', total_blocks)
        meta['TimeModified'] = ('l', int(time.time() * 1000))
        root[1]['Metadata'] = ('comp', meta)

        mods = sorted({k[0].split(':')[0] for k in blocks.values()
                       if k is not DELETE and not k[0].startswith('minecraft:')})
        if mods:
            report['warnings'].append('replacement blocks come from %s: the schematic now '
                                      'needs those mods' % ', '.join(mods))
        return root, report

    def _apply_region(self, region, blocks, entities, fill, report):
        # Which palette entry becomes what. Unused entries are pruned; an entry
        # only survives if some cell still points at it.
        used = set(region.indices)
        target = {}
        for i, key in enumerate(region.palette):
            if i not in used:
                continue
            new = blocks.get(key, key)
            target[i] = fill if new is DELETE else new

        new_palette = []
        slot = {}

        def index_of(key):
            if key not in slot:
                slot[key] = len(new_palette)
                new_palette.append(key)
            return slot[key]

        # Litematica writes air at palette 0 and a few tools assume it; keep
        # that true when air is in the result at all.
        if (AIR, ()) in target.values() or (AIR, ()) in region.palette:
            index_of((AIR, ()))

        remap = {i: index_of(k) for i, k in target.items()}
        changed_names = {i for i, k in target.items()
                         if k[0] != region.palette[i][0]}
        changed_cells = set()
        indices = []
        placed = 0
        for cell, si in enumerate(region.indices):
            old = region.palette[si]
            new = target[si]
            if new != old:
                changed_cells.add(cell)
                if blocks.get(old) is DELETE:
                    report['blocks_deleted'][L.state_label(old)] += 1
                else:
                    report['blocks_replaced'][L.state_label(old)] += 1
            indices.append(remap[si])
            if new[0] != AIR:
                placed += 1

        comp = OrderedDict(region.comp[1])
        comp['BlockStatePalette'] = nbtio.comp_list(
            [L.state_to_nbt(k) for k in new_palette])
        comp['BlockStates'] = ('la', pack_states(indices, L.bits_for(len(new_palette))))

        # Block NBT belongs to a position. If the block at that position is no
        # longer the same *block*, the data is not its data any more.
        kept_te = []
        for te in region.tile_entities:
            x, y, z = (te[1].get(k, ('i', 0))[1] for k in 'xyz')
            cell = _cell_index(x, y, z, region.dims)
            if not 0 <= cell < len(region.indices):
                kept_te.append(te)          # already orphaned; not ours to judge
                continue
            if region.indices[cell] in changed_names:
                report['block_nbt_dropped'][
                    te[1].get('id', ('str', '<no id>'))[1]] += 1
                continue
            kept_te.append(te)
        if 'TileEntities' in comp or kept_te:
            comp['TileEntities'] = ('list', (nbtio.TAG_COMP, kept_te))

        kept_ents = []
        for ent in region.entities:
            eid = ent[1].get('id', ('str', '<unknown>'))[1]
            new = entities.get(eid, eid)
            if new is DELETE:
                report['entities_deleted'][eid] += 1
                continue
            if new != eid:
                kept_ents.append(retype_entity(ent, new, report))
                report['entities_replaced'][eid] += 1
            else:
                kept_ents.append(ent)
        if 'Entities' in comp or kept_ents:
            comp['Entities'] = ('list', (nbtio.TAG_COMP, kept_ents))

        for key in ('PendingBlockTicks', 'PendingFluidTicks'):
            ticks = comp.get(key)
            if not ticks or not ticks[1][1]:
                continue
            kept = []
            for t in ticks[1][1]:
                x, y, z = (t[1].get(k, ('i', 0))[1] for k in 'xyz')
                if _cell_index(x, y, z, region.dims) in changed_cells:
                    report['ticks_dropped'] += 1
                    continue
                kept.append(t)
            comp[key] = ('list', (nbtio.TAG_COMP, kept))

        return ('comp', comp), placed

    # -- writing -----------------------------------------------------------
    def save(self, root, path=None, backup=True):
        """Write `root` to `path` (default: where this was loaded from).

        With `backup`, an existing file is *moved* to `<name>.backup` first --
        moved, not copied, so a failed write can never leave a half-written file
        where the original was. `.backup.1`, `.backup.2`... if one is there
        already; a backup is never overwritten.
        """
        dest = os.path.abspath(path or self.path)
        made = None
        if backup and os.path.exists(dest):
            made = backup_path(dest)
            os.rename(dest, made)
        folder = os.path.dirname(dest)
        if folder:
            os.makedirs(folder, exist_ok=True)
        try:
            nbtio.save(dest, self.root_name, root)
        except Exception:
            if made and not os.path.exists(dest):
                os.rename(made, dest)       # put the original back
            raise
        return dest, made


def file_stem(path):
    """`.../grave_1.litematic` -> `grave_1`. What "match the file" means."""
    return os.path.splitext(os.path.basename(path))[0]


def backup_path(dest):
    """`x.litematic` -> `x.litematic.backup`, then `.backup.1`, `.backup.2`..."""
    first = dest + '.backup'
    if not os.path.exists(first):
        return first
    n = 1
    while os.path.exists('%s.%d' % (first, n)):
        n += 1
    return '%s.%d' % (first, n)


def entity_extra_keys(ed):
    """Keys that are neither placement nor the per-instance junk every entity has.

    What is left is the entity's own data: inventory, profession, armour, a
    marker's `data` tag. This is what a retype throws away.
    """
    skip = set(ENTITY_PLACEMENT) | set(L.ENTITY_JUNK) | {'id'}
    return [k for k in ed if k not in skip]


def retype_entity(ent, new_id, report=None):
    """Same spot, different entity. Keeps placement, drops the species' data."""
    old = ent[1]
    kept = OrderedDict()
    kept['id'] = nbtio.string(new_id)
    for k in ENTITY_PLACEMENT:
        if k in old:
            kept[k] = old[k]
    if report is not None:
        dropped = entity_extra_keys(old)
        if dropped:
            report['entity_nbt_stripped'][
                old.get('id', ('str', '<unknown>'))[1]] += len(dropped)
    return ('comp', kept)
