# Frostline — Natural Generation Reference

How to make generated things look like they grew there instead of being stamped.
Distilled from reading `ref/` (Terralith + its vanilla overrides), from decompiling
vanilla features, and from the pond/lake work that went wrong before it went right.

Read this **before** adding any terrain feature, water, rock, patch or set-piece.
`WORLDGEN.md` covers the snow system, region geometry and feature ordering; this file
covers *technique*. Where they overlap, this one is newer.

> **Stale note:** `WORLDGEN.md` and `STRUCTURES.md` describe `gen3.py` / `verify.py`
> regenerating `data/`. Neither script exists any more (the mod repo's last commit is
> "datapack split"). `data/` is hand-maintained JSON now. Edit it directly, keep line
> endings, and verify by loading a new world.

---

## 0. The five rules

1. **Never cut terrain to a flat plane.** A plane intersecting a slope *is* a wall.
   Move terrain (sink, raise, carry the surface along), don't slice it.
2. **Let terrain decide the outline.** Water, snow, moss, rubble: anything that
   "collects" must end where the ground says, i.e. on a contour or next to a step.
   Geometry you invent (a radius, an ellipse) is only the *budget*, never the edge.
3. **No circles.** Every radial shape gets a domain warp (noise offsets the lookup
   position) plus low harmonics. A plain `distance < r` is visible from 100 blocks.
4. **Cluster, don't sprinkle; spread, don't clump.** Small things (plants, rocks)
   cluster via noise. Rare set-pieces are spread like structures via a region grid.
   Uniform `rarity_filter` does neither: it sprinkles *and* clumps.
5. **Plan, validate, then write.** A feature that can't fit leaves zero blocks.
   Half-placed features are the ugliest thing in worldgen.

---

## 1. What is in `ref/`

| path | what it is |
|---|---|
| `ref/terralith/worldgen/` | the Terralith datapack: ~1900 files, zero Java |
| `ref/minecraft/worldgen/` | vanilla files Terralith overrides: `noise_settings/overworld`, `density_function/overworld/*`, a handful of placed features |
| `ref/c/`, `ref/forge/`, `ref/biome_tag_villagers/` | cross-mod biome/block tags only |

Survey counts, which say what the pros actually reach for:

- **Configured features:** `tree` 186, `random_selector` 66, `simple_block` 41,
  `random_patch` 37, `vegetation_patch` 34, `disk` 23, `ore` 22,
  `waterlogged_vegetation_patch` 4, `lake` **0**.
- **Placement modifiers:** `count` 482, `biome` 397, `in_square` 377,
  `heightmap` 316, `block_predicate_filter` 205, `random_offset` 141,
  `noise_based_count` 125, `height_range` 110, `environment_scan` 104,
  `rarity_filter` only 68.
- **State providers:** `simple` 817, `weighted` 102, `noise_provider` 45,
  `dual_noise_provider` 23.
- **Block predicates:** `matching_blocks` 1324, `all_of` 289, `any_of` 116, `not` 107,
  `would_survive` 94, `replaceable` 69, `matching_fluids` 67.

**Takeaway:** Terralith's quality is placement predicates and noise, not exotic
feature types. It never uses `minecraft:lake`.

How to search it quickly:

```bash
cd ref && python - <<'EOF'
import json, os
for r, _, fs in os.walk("terralith/worldgen"):
    for f in fs:
        p = os.path.join(r, f); s = open(p, encoding="utf-8").read()
        if '"minecraft:noise_based_count"' in s: print(p)
EOF
```

---

## 2. Water: how everyone does it

### 2.1 Vanilla `minecraft:lake` (decompiled from the 1.20.1 srg jar)

- Origin moved down 4, box is **16 × 8 × 16**, 4–7 random ellipsoid blobs.
- Blob cells with `y < 4` get fluid, `y ≥ 4` get air. **Depth is always 4.**
- **Aborts** if any boundary cell at `y ≥ 4` is liquid, or any boundary cell at
  `y < 4` is non-solid and not the fluid. Most slopes abort.
- Barrier block replaces solid boundary blocks. The freeze pass tests y = 4, which is
  air, so in practice `freeze_top_layer` does the icing later.
- Surface water lakes were removed from vanilla biomes in 1.18. Only lava lakes remain.

**Verdict:** fixed size, fixed depth, slope-hostile. Don't use it for anything that
should look natural.

### 2.2 Where real vanilla lakes come from

Lakes and rivers are **terrain below the fluid level**: `sea_level` plus aquifers
(`fluid_level_floodedness`, `fluid_level_spread`, `barrier` in the noise router). The
water outline is automatically a terrain contour, which is why it looks right.
Frostline zones have `aquifers_enabled: false` and `sea_level: -64`, so this route is
closed without redesigning the router.

### 2.3 Terralith's water tricks

| feature | technique | why it looks natural |
|---|---|---|
| `highlands/lake`, `taiga/birch/pond` | `waterlogged_vegetation_patch`, radius 1–2, depth 1–2. Placement demands **air in a + or ring shape at the placement Y** (`block_predicate_filter`, offsets ±1..±3) | only fires on level ground; tiny; the patch's exposure check keeps the water sealed |
| `mountains/misty/tuff_lake` | waterlogged patch, radius 2–8, placed via `noise_based_count` | pools appear in noise-driven clusters |
| `yellowstone/lakes`, `canyon/*/lakes` | `count 20 × count 30` of **single `simple_block` water**, then `environment_scan` down, keeping a spot only if **no horizontal neighbour is air**. Canyon swaps in a `noise_provider` choosing water vs sand | water pools in the terrain's own hollows; the outline is the hollow |
| `caldera/lakes` | `vegetation_patch` radius 8 lays a floor; the inner feature is water with a neighbours-not-air predicate | fills a basin the density function already shaped |
| `mountains/misty/v_lake` | `vegetation_patch` with `ground_state: air` (digs), inner feature a `tree` whose **trunk is water** 2 tall | a hack for "dig then fill" without Java |

**The common idea:** the terrain makes the hollow, water only fills what's enclosed.
Nobody decides a water outline geometrically.

### 2.4 Frostline's ponds and lakes (Java, in `FrostLine/src/.../frostline/`)

Built because zone terrain has no hollows to fill and aquifers are off. What survived
two failed attempts:

**`Hollow` (shared).**
- `profile(t) = (1 - t²)²`: 1 at the centre, easing to 0 with zero slope at the edge,
  so there is no rim step.
- `sink(column, lower)` moves the top of a column down by `lower`, carrying 5 blocks of
  soil plus surface, snow layer and plant with it. Vacated blocks become air.
- `terrainLike()` fills a gap with the block under it instead of a visible "wall" block.

**`PondFeature`.**
1. **Region gate:** the chunk must be the seed-chosen chunk of its
   `spacing × spacing` region, and the region must pass `region_chance`
   (`WorldgenRandom.setLargeFeatureWithSalt`, same as structures).
2. **Outline:** stretched, rotated ellipse, two sine harmonics on the edge, then a
   simplex **domain warp** (offset lookup coords by `noise * 0.25 * radius`).
3. **Water level:** lowest ground on the ring just outside the basin.
4. **Sink:** every basin column sinks `depth * profile(t)`.
5. **Flood:** water only where the sunken ground is below the water level. On slopes
   the uphill side stays a dry dent, and the shore is a contour.
6. **Reject untouched** if: the rim height spread exceeds `max_bank_height`, less
   than 30 % of the basin floods, the basin touches the reach limit, or too many
   seal blocks are needed.
7. Everything goes into a plan map first, then gets written.

**`FrozenLakeFeature` plus `LakeFieldDensityFunction`.**
- **Where lakes go:** the lake field puts sparse sites on a 1536-block cell grid
  (biome radius 110–170), gated by erosion so lakes only form on flat ground, and seeded
  through a `NoiseHolder`.
- **Big lakes need scaled companions:** deeper sink (8–14), looser `max_bank_height`
  (14), lower `warp_scale` (0.12) for broad bays instead of wiggles. The basin must also
  be clamped inside the biome (`BIOME_LIMIT`), because a warp proportional to the radius
  otherwise pushes the hollow into chunks that never run the feature, leaving a step.
- **Biome fit:** the field feeds the router's otherwise-unused `continents` slot.
  The lake biome claims `continentalness ≥ 0.6`, so the biome sits exactly on each lake.
- **Water level:** lakes are bigger than the 3×3-chunk write window, so every chunk
  must agree without seeing the others. The level comes from
  `ChunkGenerator#getBaseHeight` on 48 rays out to the warped rim. Base height is
  noise-only, so every chunk computes the same value; it's cached per site.
- **Carving:** each chunk sinks and floods only its own columns, using the same
  `Hollow` + contour rule as ponds.

### 2.5 Failures, so they are not repeated

| attempt | result | root cause |
|---|---|---|
| v1: water plane = lowest rim, cut everything above it | "took out half a hill, flat wall behind it" | rule 1: slicing a slope |
| v1: water fills `t < 1` of an ellipse | "sat down like a perfect circle" | rules 2 and 3: geometric outline, no warp |
| v1: `rarity_filter` 9–16 plus 2 features per biome | "6–7 in one area" | rule 4: uniform rarity clumps |
| v2: `rarity_filter` 28–40 | "5 next to each other" | same; rarer uniform noise still clumps |
| v3: region grid 18–24 chunks | too rare | overshoot; settled on 10–12 chunks (§5) |
| lake biome with its own icy surface rule, then made 300 blocks wide | "turns entire hills into packed ice" | **a surface rule paints the whole biome**, and a climate-placed biome covers hills as well as the lake. Give such a biome its neighbours' palette; let the feature supply the ice. |
| hollow sinks every column in the basin | hills inside the lake sag | reshaping must be gated by **height relative to the water level** (`shore_height` fade), not just by distance from the centre |
| 48 rim rays on a 300-block lake | leaks plugged with packed-ice dams | rim sampling must scale with circumference (one sample per ~2 blocks) |
| dense rim sampling + "rim spread ≤ 14" + level = lowest rim point | every lake silently dry | over hundreds of blocks the rim min is a valley floor and the spread is always large. **Set the level from the interior** (35th percentile), lift low rim points with a berm, shrink the basin until it holds, and **log every dry verdict with its numbers**. A feature that silently does nothing costs a full test cycle to diagnose. |
| berm at full height up to the basin edge, sink scaled by each column's own height | "a clear edge… a difference in y level not blended with the terrain around it" | any shaping term must reach **zero value and zero slope** where the feature's footprint ends: give berms an outer skirt that eases back to natural ground. And never scale a deformation by per-column height: a 1-block step times a depth factor becomes a ledge. Read a **smoothed relief** (coarse base-height grid, bilinear) for decisions, and *pull toward* a target (`g + (target - g) * w`) instead of *adding* offsets, so bumps are compressed, not amplified. |

---

## 3. Terrain shaping in density functions (Terralith's approach)

Terralith's `final_density` is vanilla's with two hooks, which is effectively
**constructive solid geometry**:

```
max( sloped_cheese + beardifier, terralith:overworld/extra_terrain_sum )     ← union: add arches, spikes
min( …, 5 * min(caves/entrances, terralith:overworld/subtract_terrain_sum) ) ← intersection: carve cliffs, dunes
```

- `extra_terrain_sum` is `max` over add-shapes (`arch/*`, `spike/*`), each
  `-64`/very negative outside its region.
- `subtract_terrain_sum` is `min` over carve-shapes (`cliff/carve`, `dune/total`), each
  large positive outside its region.
- **Region masks use `range_choice`:** `dune/total = range_choice(size_spline in [-1,∞)
  → dune noise, else -64)`. A shape exists only where a control value is in range.
- **Cliffs** (`cliff/carve`) = spline + `max(-1.65 + max_cut noise, range_choice(…
  carve_depth …))`. `carve_depth = (-2.325 + slope noise) * cliff_depth`. Noise sets
  both *how deep* and *how steep*, so cliff lines wander.
- **Caching:** 2D control values are `flat_cache(cache_2d(noise, y_scale 0))`. Values
  referenced from several places are `cache_once`. Always cache a 2D term, or it's
  recomputed per 3D sample.
- `cliff/modified_offset` is vanilla `overworld/offset` copied with a different
  continentalness input. Change terrain height by **substituting an input**, not
  by rewriting the spline.

Frostline specifics (from `WORLDGEN.md` §4–5, still true):
- `offset` is flat-cached, so it can only raise or lower ground, never fold it.
- Overhangs and shelves come from terms added to `DEPTH` *outside* the cache, times a
  Y band built from two `y_clamped_gradient`s.

**When to reach for density instead of a feature:** anything bigger than about 3
chunks, anything that must look continuous across chunk borders, and anything that
should get surface rules applied to its new faces (features run after surface rules,
so their exposed faces show raw host rock).

---

## 4. Placement idioms worth copying

**Clustering and bands**
- `noise_based_count` (125 uses) turns "N per chunk" into "dense patches and bare gaps".
  If something looks sprinkled, this is the fix.
- **Band-pass with two counts:** `snowy/trees_center` uses `noise_based_count(offset 0,
  ratio +1)` then `(offset -1, ratio -1)`; `trees_edge` uses `(offset 0.35, +1)` then
  `(0, -1)`. Opposite-signed passes zero the count outside a noise band, so the forest
  interior gets big spruces and a separate edge band gets sparse small ones. Use this
  for any "core vs fringe" gradient.
- `noise_threshold_count(noise_level, below_noise, above_noise)` is a hard two-level
  density switch.

**Reading the terrain**
- `environment_scan` down (`max_steps`, `target_condition`) finds real floors under
  overhangs and in caves. Up plus `random_offset y -1` hangs things from ceilings.
- `surface_relative_threshold_filter(heightmap WORLD_SURFACE_WG, max -12)` means "at
  least 12 blocks underground": cave decoration that never leaks to the surface.
- `count_on_every_layer` decorates every exposed floor in a column (ledges, overhang
  tops), not just the heightmap top.
- `surface_water_depth_filter` limits trees or props by water depth (swamps).
- **Ring predicates at the placement Y** (§2.3) are the cheapest "is this flat?" test.

**Blocks and survival**
- `would_survive` (94 uses) beats hand-listing valid support blocks for plants.
- `noise_provider` / `dual_noise_provider` in `simple_block` inside `random_patch` pick
  the block *species* by noise, so flower meadows get patches of one colour, not
  confetti. Use the same for rubble, gravel vs stone, ice vs packed ice.
- Rocks: `forest_rock` (mossy cobble blob), `block_pile`, `disk`
  (`target: matching_blocks`, `half_height`).
- `ore` doubles as a block **swapper**: `alpha/clay_patch` turns sand into clay,
  `alpha/sand_beaches` turns soil into sand. Placing `air` digs craters (WORLDGEN.md §5).
- `random_patch(tries, xz_spread, y_spread)` with a predicate inner placement is the
  standard scatter.
- **Catch-up passes:** `snowy/fix_snow` is a `random_patch` whose inner placement scans
  down 16 for air over leaves/grass. It re-applies snow under canopies that
  `freeze_top_layer` missed. Any "global pass missed sheltered spots" problem has this
  shape.

**Order inside a biome** (Terralith `alpha` biome, typical):
- step 3 `monster_room`; 5 clay patch; 6 ores; 7 sand beach swap; 8 springs
- step 9 trees, flowers, mushrooms, sugar cane
- step 10 `freeze_top_layer`

Terrain-altering features in Frostline go at the **end of step 10** (after snow), or
their snow gets re-laid wrong.

---

## 5. Frequency: how to pick numbers

Per-type expected density, in chunks per placement:

| method | expected | clusters? |
|---|---|---|
| `rarity_filter chance N` | 1 per N chunks | yes, Poisson clumps |
| `count 1` + `noise_based_count` | patchy by design | intentionally |
| region grid `spacing S`, `region_chance p` | 1 per S²/p chunks, at most 1 per region | no |
| structure `random_spread spacing S separation D` | ~1 per S² chunks, min distance D | no |

- Multiple features of the same kind in one biome **add** densities. Two ponds at
  1/40 plus 1/32 is one pond every ~18 chunks, which reads as a cluster field.
- Validation failures lower the real rate further (steep, dry, obstructed). Tune
  by observation, not just by formula.
- **"Interesting to find"** is a region grid, not a rarer rarity. Frostline ponds
  after user tuning:

  | pond | spacing | region_chance | ≈ chunks/pond |
  |---|---|---|---|
  | deep_flat | 12 | 0.60 | 240 |
  | shallow_flat | 10 | 0.60 | 167 |
  | deep_mountain | 12 | 0.55 | 262 |
  | shallow_mountain | 10 | 0.55 | 182 |

  The user called this "the perfect ratio": roughly the geometric middle between
  1 per ~35 (too common) and 1 per ~900 (too rare).
- Different pond types need different `salt`s, or their grids pick the same chunk.
- Limit rare features to **empty** biomes (plains, gravel plains, highlands, stony
  highlands). In forests and hills they compete with trees and read as clutter.

Terralith structure sets for scale:
- `spire`: `random_spread spacing 36 separation 18`.
- fortified villages: `spacing 46 separation 18` plus
  `exclusion_zone {other_set: minecraft:villages, chunk_count: 8}`.

---

## 6. Java feature rules (learned the hard way)

**Justify it first.** Java is for what data cannot say (WORLDGEN.md §0). Ponds and
lakes qualified: no vanilla feature picks one level for a whole water body and shapes
ground around it. Say why in the class javadoc, in the same style as
`ProgressionDensityFunction`.

**Write window.**
- A feature may write **one chunk past its origin chunk** (the 3×3 around it). Writing
  further corrupts or logs "far chunk" errors.
- Snap the origin to the chunk middle (`chunk.getMiddleBlockX()`) to get ±22 blocks
  of reach for the shape plus 1 for the rim.
- Anything larger must be computed deterministically and written per chunk
  (FrozenLakeFeature).

**Cross-chunk determinism.**
- Shared decisions come from (a) seeded site rolls and (b) `getBaseHeight`. Never from
  blocks in other chunks, never from `ctx.random()`.
- Cache per site in a `ConcurrentHashMap`. Worldgen is multithreaded.
- Get the dimension's `RandomState` with `level.getLevel().getChunkSource().randomState()`.
- Find a seeded density function instance by walking
  `randomState.router().continents().mapAll(visitor)`.

**Density functions with state.**
- **Not records.** Router wiring hashes density functions, and a record's hash changes
  as its cache fills.
- Seed through `NoiseHolder` plus `visitor.visitNoise` in `mapAll`. Roll randomness as
  `frac(|noise(cell coords + salt)| * 7919)`.

**Neighbour biomes.** Decoration runs features for biomes found in the chunk and its
neighbours, and a feature without a `minecraft:biome` placement filter runs whenever
its biome is within about a chunk. Keep shapes within ~16 blocks of their biome.

**Validate before writing.** Build a `Map<BlockPos, BlockState>` plan, run every
rejection check, then `setBlock` in one pass.

**Replaceability.** Never move or replace `BlockTags.FEATURES_CANNOT_REPLACE`, logs, or
anything with a block entity. Finding the surface: skip air, leaves, and replaceable
non-fluid blocks (snow layers, grass). Stop at the ground tag. Anything else means an
obstruction, so abort.

**Water specifics.**
- Water placed in worldgen stays put only if every horizontal neighbour is sturdy, or
  is itself a water source.
- Seal gaps with `terrainLike`, and cap how many seals you'll accept. A pond needing
  many seals is on a cliff edge; skip it.
- Floors: a `FallingBlock` (gravel, sand) with air below falls on the first update and
  drains the pond. Swap it for a solid block.

**Snow and ice interplay.**
- `freeze_top_layer` turns top water into vanilla ice and drops snow layers on anything
  with a full top face. Place water features **after** it in step 10.
- `immersive_weathering:thin_ice` is a 4px slab occupying the water's top block. Its
  fluid state is water; it needs water directly below (`canSurvive`) and melts only
  from block light (config `THIN_ICE_MELTING`). Properties: `cracked` 0–3,
  `can_expand`. Use ice or packed ice wherever water is 1 deep.

**Inspecting vanilla.** No sources are bundled. Read bytecode:

```bash
J="$APPDATA/ATLauncher/libraries/net/minecraft/client/1.20.1-20230612.114412/client-1.20.1-20230612.114412-srg.jar"
javap -c -p -cp "$J" net.minecraft.world.level.levelgen.feature.LakeFeature
```

Class names are Mojang names; method names are SRG (`m_xxxx_`). Read the invoke
targets and constants. That was enough to reconstruct LakeFeature, VegetationPatch,
WaterloggedVegetationPatch and ThinIceBlock.

**Build and deploy.**

```bash
cd /d/Development/Games/Minecraft/LastDeparture/FrostLine && ./gradlew build
cp build/libs/frostline-1.0.0.jar "$APPDATA/ATLauncher/instances/LastDepartureTestingChamber/mods/"
```

New registry types need a full game restart. Worldgen changes only show in **new chunks**.

---

## 7. Checklist before shipping a "natural" feature

- [ ] Does anything cut a flat plane through terrain? (walls)
- [ ] Is any visible edge a geometric shape rather than a contour or noise? (circles)
- [ ] Is the outline domain-warped?
- [ ] Rarity: region grid for set-pieces, `noise_based_count` for props, never plain
      `rarity_filter` for anything the player should "discover".
- [ ] Only in biomes where it has room (empty biomes for set-pieces)?
- [ ] Rejects bad sites *without writing anything*?
- [ ] Respects the write window (±1 chunk), or is deterministic per chunk?
- [ ] Runs after `freeze_top_layer` if it touches water, ice or the surface?
- [ ] Exposed faces: will raw host rock show? If that's ugly, carry the surface
      (Hollow) or do it in density so surface rules apply.
- [ ] New ground block? Add it to `drift_ground`, `patch_replaceable` and
      `pond_ground`.
- [ ] Looked at it in a **new world**, from far away and up close, before calling it
      done.
