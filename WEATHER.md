# Frostline weather

Snowstorms, snowfall, fog, wind and snow particles, built into the FrostLine mod. It
replaces the biomes' old ambient particles (`snowflake`, `white_ash`), which faked snow.

**Status:** implemented and building. **Not yet tested in-game.** Nothing committed.
§6 is the test plan; §7 is the to-do list.

**What it does not do:** no worldgen, no new blocks or items, no snow placement of its own,
no temperature, no sky or cloud changes. Nothing changes outside weather.

---

## 0. What was done

| area | change | where |
|---|---|---|
| Datapack: biomes | `particle` removed from the 41 `snowflake` / `white_ash` biomes (the 12 `warped_spore` biomes of The Quiet keep theirs) | `worldgen/biome/*.json` |
| Datapack: tag | `#frostline:windswept`: the 9 `blizzard_*` and 4 `ice_wall_*` biomes, never fully calm | `tags/worldgen/biome/windswept.json` |
| Mod: storm schedule | windowed storms on top of vanilla rain, saved per world; `/frostline storm` | `weather/StormScheduler`, `StormState`, `StormCommands` |
| Mod: config | `frostline-weather.toml` (schedule), `frostline-weather-client.toml` (looks and sound) | `weather/WeatherConfig` |
| Mod: rendering | snowfall replacing vanilla's, two layers, slanting with the wind | `weather/client/SnowRenderer`, `ColumnCache`, `FrostlineOverworldEffects` |
| Mod: fog, wind, particles | storm fog, looping wind, snow sifting and drifting | `weather/client/WeatherClient`, `WindSound`, `AmbientSnow`, `SnowDriftParticle` |
| Mod: assets | wind loop (generated, see §5), subtitle, particle, `#frostline:sifting_snow` | `assets/frostline/…`, `data/frostline/tags/blocks/sifting_snow.json` |
| Docs | particle mentions in BIOMES.md now point here | BIOMES.md |
| Deploy | new `frostline-1.0.0.jar` in **both** instances; LastDeparture's Paxi zip rebuilt (backup `frostline.zip.pre-weather`) | `…/mods`, `LastDeparture/config/paxi/datapacks/frostline.zip` |

No vanilla class is patched. Rendering uses Forge's `DimensionSpecialEffects` hook, and
everything else uses Forge events. No new mixins, so there is nothing for other mods to
collide with and no refmap is needed.

---

## 1. Storms: when

A storm **is vanilla rain** on the overworld. Every Frostline dimension shares the
overworld's weather. Snow piling up, a darker sky, mobs and the client's rain level all
stay vanilla. Frostline only replaces vanilla's random rain cycle with a schedule.

**The schedule.** Each in-game day has one *storm window* (`windowStart` → `windowEnd`).

1. When the window opens, roll `skipChance`. A hit means no storm this window.
2. Otherwise the storm starts a random `0 … maxStartDelay` ticks into the window and is
   due to end when the window closes.
3. At its end, roll `continueChance`. A hit keeps the storm going for **one more full day**:
   through the daytime and the whole next window. It rolls again every day until the storm
   has lasted `maxStormDays`.
4. After a storm, `cooldownDays` windows pass without one.
5. No storm before `firstStormDay`.

**Example: the defaults.** The window is dusk to dawn (13000 → 23000).

- Half of all nights have no storm.
- A storm that does come has a 20 % chance to run through the next day and night.
- Storms last at most 3 days.
- After a storm, one night is always clear.

**Other things that change the weather:**

| what | result |
|---|---|
| `doWeatherCycle` off | schedule paused; nothing changes by itself (vanilla behaviour) |
| `/weather rain` / `thunder` [duration] | becomes a storm for that duration; it never continues and leaves no cooldown |
| `/weather clear` | ends the storm |
| sleeping | vanilla sleep clears rain. `sleepEndsStorm = false` (default): the storm carries on; the night skip may still reach its end and roll `continueChance` |
| `/time set` backwards | a running storm ends and the skipped windows are rolled again |
| `enabled = false` | handed back to vanilla with a normal clear spell |
| mod removed | vanilla takes over within 5 minutes (timers are re-armed with a 5-minute buffer only) |

**Day time.** Windows use the day time. If `doDaylightCycle` is off, time stands still:
closed windows never open and a running storm never ends.

**Commands** (op level 2): `/frostline storm status`, `/frostline storm start [days]`
(until the end of the current window, or one window length from now, plus days − 1),
`/frostline storm stop`.

**State** is saved in the world as `data/frostline_weather.dat`.

---

## 2. Storms: how they look and sound

All of this is client-side and per player, in `frostline-weather-client.toml`.

**Intensity.**

- The client eases the rain level the server sends over `stormFadeSeconds` (30), so storms
  roll in and die down slowly.
- `#frostline:windswept` biomes never drop below `windsweptIntensity` (0.35). Zone 4 and
  the ice wall always have a light blizzard, which the `white_ash` particle used to fake.
- Snow only falls where vanilla would snow (biome precipitation and temperature). The
  green zone-zero biomes still rain; The Quiet has nothing.

**Snowfall** (`SnowRenderer`) replaces vanilla's snow. It uses the same textures and shader,
so shader packs still treat it as weather.

- **Main layer**, always drawn where it snows.
  - Calm: 8 blocks out, opacity `calmSnowfall` (0.35), slow. This is the snow outside storms.
  - Storm: widens to `stormRadius` (12), turns opaque and falls about 6× faster.
- **Near layer**, storms only: within 6 blocks, twice as dense, faster, opacity
  `stormSnowfall`.
- **Wind slant.** In a storm, columns shear with the wind by `windSlant` (0.5). Snow above
  your eyes comes from upwind and lands downwind.
- **Rain** in rain biomes is drawn like vanilla.

**Wind.**

- Direction and gusts come from game time, so everyone on a server sees the same wind
  without packets. The direction wanders over about half a day; gusts come every few seconds.
- The same wind drives the slant, the particles and the sound's volume and pitch.

**Fog**, while outdoors in a storm:

- Visibility closes in to `stormFogDistance` (40 blocks).
- Colour goes to `colorDay` / `colorNight`, blended by daylight.
- Scaled by how exposed you are (sky light at your eyes), so it lifts indoors and in caves.
  Water and lava fog are untouched.

**Wind sound.**

- One quiet looping sound in the **Weather** category, so the Weather slider controls it.
- Volume is `stormWindVolume` (0.45) times storm intensity, dropping to `shelteredVolume`
  (25 %) indoors. It swells with gusts and is muffled underwater.
- `calmWindVolume` (0) adds a breeze outside storms.
- It fades in and out, and stops itself after 2 silent seconds.

**Biome ambience in storms** (`StormAmbience`).

- The biome's own `ambient_sound` loop fades to `stormAmbienceVolume` (0, silent) as a
  storm builds, and comes back as it passes. Its `additions_sound` one-shots are skipped
  in proportion.
- This follows real storms only: the windswept floor doesn't count, so zone 4 keeps its
  ambience between storms. Leaving a snowy biome brings it back quickly.
- Cave mood sounds are untouched, and so are the biome files.
- How: Forge's `PlaySoundEvent` swaps the vanilla loop for a wrapper that scales its
  volume each tick. Vanilla still fades and stops the original.

**Particles** (`AmbientSnow`), at any weather:

- **Sifting:** grains fall from the underside of `#frostline:sifting_snow` blocks (snow
  block, powder snow) and of leaves carrying snow, when there is air below. More in storms.
  This is the Wintery Atmosphere effect.
- **Drifting:** grains blow off exposed snow in the wind, scaled by storm strength. Mostly
  in storms, lightly in windswept biomes.
- Both follow vanilla's Particles setting (Minimal: off; Decreased: half).

---

## 3. Performance

**Snowfall: vanilla, per frame.** For every column in range it:

- looks up the biome;
- reads the heightmap and the light;
- allocates a `RandomSource`.

**Snowfall: Frostline.**

- `ColumnCache` reads biome, height and light for a 33×33 area every 5 ticks, or when you
  change column.
- Frames only do arithmetic: no world access, no allocation.
- A full storm is about 700 quads in a single draw call per texture.

**Particles.**

- Wintery Atmosphere scans a whole cube of blocks every 10 ticks.
- Frostline reads `samplesPerTick` (300) random blocks per tick, like vanilla's
  `animateTick` (which reads 1 334), and only reads more for the few that are snow.

**Server.** One check per second on the overworld.

**Knobs if a machine struggles:** `stormRadius`, `stormDensity`, `stormSnowfall` 0,
`samplesPerTick`, the Particles setting. Lower density also draws fewer quads.

**Density vs opacity.** `calmSnowfall` / `stormSnowfall` set how solid the flakes look;
`calmDensity` / `stormDensity` set how many there are. Each column has a fixed rank, and
columns ranked above the density are skipped, fading at the cut so nothing pops as a
storm builds.

---

## 4. Config reference

### `config/frostline-weather.toml` (server / single-player world)

| key | default | meaning |
|---|---|---|
| `storms.enabled` | true | Frostline schedules the weather; false = vanilla cycle |
| `storms.firstStormDay` | 1 | no storm before this day |
| `storms.windowStart` | 13000 | window opens (day time) |
| `storms.windowEnd` | 23000 | window closes; equal to start = whole day |
| `storms.skipChance` | 0.5 | chance a window has no storm |
| `storms.maxStartDelay` | 2400 | random start delay into the window, ticks |
| `storms.continueChance` | 0.2 | chance, at its end, to continue one more day |
| `storms.maxStormDays` | 3 | hard cap on storm length |
| `storms.cooldownDays` | 1 | stormless windows after a storm |
| `storms.sleepEndsStorm` | false | sleeping ends a storm |

**Your example.** Storms always at night; some nights none; sometimes that night plus the
next day; never more than 2 days:

```toml
windowStart = 13000
windowEnd = 23000
skipChance = 0.4
continueChance = 0.3
maxStormDays = 2
cooldownDays = 0
```

### `config/frostline-weather-client.toml` (per player)

| key | default | meaning |
|---|---|---|
| `snowfall.enabled` | true | false = vanilla weather visuals, no fog, wind or particles |
| `snowfall.excludedDimensions` | [] | dimensions that keep vanilla visuals |
| `snowfall.calmSnowfall` | 0.35 | light snowfall opacity outside storms (0 = none) |
| `snowfall.stormSnowfall` | 1.0 | storm near-layer opacity |
| `snowfall.calmDensity` | 0.6 | how many flakes outside storms: share of snowfall columns drawn (1 = all, 0 = none) |
| `snowfall.stormDensity` | 0.7 | how many flakes in a full storm (both layers); eases from `calmDensity` as the storm builds |
| `snowfall.stormRadius` | 12 | snowfall radius in a storm (4–16) |
| `snowfall.windSlant` | 0.5 | how far storm snow slants |
| `snowfall.stormFadeSeconds` | 30 | build-up / die-down time |
| `snowfall.windsweptIntensity` | 0.35 | intensity floor in `#frostline:windswept` |
| `fog.enabled` | true | storm fog |
| `fog.stormFogDistance` | 40 | visibility in a full storm, blocks |
| `fog.colorDay` / `colorNight` | `#C3CBD3` / `#1C2229` | storm fog colour |
| `sound.stormWindVolume` | 0.45 | wind in a full storm |
| `sound.calmWindVolume` | 0.0 | wind outside storms |
| `sound.shelteredVolume` | 0.25 | share of wind heard indoors |
| `sound.stormAmbienceVolume` | 0.0 | biome ambience (background loop + additions) in a full storm; fades with the storm, 1 = unchanged |
| `particles.sifting` | 1.0 | sifting amount (0–4) |
| `particles.drifting` | 1.0 | drifting amount (0–4) |
| `particles.samplesPerTick` | 300 | blocks sampled per tick |

### Data (datapack-overridable)

- `#frostline:windswept` (biome tag): never fully calm.
- `#frostline:sifting_snow` (block tag, default in the mod jar): blocks that sift snow.

Both files are created with defaults on first launch. The LastDeparture instance needs no
config changes.

---

## 5. Internals

| class | side | role |
|---|---|---|
| `weather/FrostlineWeather` | both | registers sound `frostline:weather.wind`, particle `frostline:snow_drift`, tags, configs, listeners |
| `weather/WeatherConfig` | both | the two config specs |
| `weather/StormScheduler` | server | per-second schedule; re-arms vanilla timers; adopts `/weather` and sleep |
| `weather/StormState` | server | saved schedule state |
| `weather/StormCommands` | server | `/frostline storm …` |
| `weather/Wind` | both | wind heading and gusts from game time |
| `weather/client/WeatherClient` | client | per-tick intensity, exposure, wind; fog events; sound control |
| `weather/client/FrostlineOverworldEffects` | client | vanilla overworld effects + `renderSnowAndRain` → `SnowRenderer` |
| `weather/client/SnowRenderer` | client | the precipitation draw |
| `weather/client/ColumnCache` | client | cached column biome / height / light |
| `weather/client/AmbientSnow` | client | sifting and drifting spawns |
| `weather/client/SnowDriftParticle` | client | wind-carried snow grain (vanilla snowflake sprites) |
| `weather/client/WindSound` | client | looping tickable wind |

**Wind sound.** `assets/frostline/sounds/weather/wind.ogg` is 24 s, mono, streamed.

- It was generated for Frostline, so it has no licence strings attached: spectrally
  shaped noise with random phases (exactly periodic, so the loop has no seam) and gust and
  whistle envelopes on whole cycles.
- It sits around −18 dBFS RMS, deliberately quiet.
- The generator is `wind.py`, kept outside the repo. To swap in another sound, replace the
  ogg; keep it mono and loopable.

**Rendering hook.**

- `RegisterDimensionSpecialEffectsEvent` replaces the effects registered for
  `minecraft:overworld`, which is the `effects` of every Frostline dimension type.
- If another mod replaced the overworld effects too, the one loaded last wins. Nothing in
  the current instances does.
- Returning false from the hook hands rendering back to vanilla.

---

## 6. Test plan

1. **Load:** the log shows no Frostline errors, and both config files appear in `config/`.
2. **Calm:** in zone 1–3 a light snowfall falls, with no old white specks. Zone 4 and the
   ice wall have a light blizzard with a little fog. Zone zero's green biomes rain normally
   when it rains. The Quiet shows no precipitation.
3. **Storm:** run `/frostline storm start`.
   - Over ~30 s: snow thickens and slants, fog closes to ~40 blocks, the wind rises.
   - Indoors: fog lifts and the wind drops.
   - Underwater: the wind is muffled.
4. **Stop:** run `/frostline storm stop`. The storm fades out over ~30 s.
5. **Schedule:** set `skipChance = 0`, `maxStartDelay = 0`, then `/time set 12900`.
   - A storm starts at 13000 and `status` reports its end.
   - `/time set 22990`: at 23000 it ends, or continues with `continueChance = 1`.
   - With `maxStormDays = 2`, it ends after the second day.
6. **Commands:** `/weather rain 600` starts a storm that ends in 30 s; `/weather clear`
   ends a storm; `/gamerule doWeatherCycle false` freezes the weather.
7. **Sleep:** during a night storm, sleep. With the default config the storm carries on
   into the morning only if it rolled to continue, and otherwise ends at dawn.
8. **Particles:** under a snow-block overhang, and under snow-covered leaves, grains drift
   down. In a storm, grains blow off snowy ground downwind.
9. **Shaders (Oculus):** with a pack on, snowfall still draws in the weather pass.
10. **Performance:** F3 frame time in a full storm versus clear. `spark` if it looks off.
11. **Multiplayer (e4mc/LAN):** a second player sees the same storm and wind direction.

---

## 7. To do / open

- [ ] In-game test (§6).
- [ ] Tune the defaults to taste: storm fade, fog distance, snowfall opacity, wind volume.
- [ ] Rebuild the Paxi zip after every datapack change (see RAILWAYS.md §7).
- [ ] Optional: a config screen. Forge 1.20.1 has none built in; the `Configured` mod would
      show these files in-game without code.
- [ ] Optional: a second, calmer wind loop for `calmWindVolume` if a breeze is wanted.
