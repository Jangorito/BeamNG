# Vehicle hero viewer pipeline

Turns one specific BeamNG vehicle trim (the default part config used by a
launcher config, e.g. "SBR4 Track" or "ETK K-Series Trackday A") into a
lightweight GLB suitable for a web product-hero viewer -- not the full
in-game model, which bundles every body kit, race part, wheel, and historical
variant the car family ever shipped with.

Proven end-to-end on `etkc` (ETK K-Series Trackday A). Everything below is
the exact process used; `CAR = "etkc"` throughout is the only thing that
makes the current scripts etkc-specific -- see "Doing this for a new car".

## Why this isn't a straight DAE-to-GLB export

BeamNG's per-vehicle `.dae` is the *combined* model: every optional part
(sport/race/custom kits, interior trims, alternate lights/glass, rollcages,
historical variants) as named sub-objects in one file -- 257 objects for
etkc, most of them not used by any one trim. There's also no wheel/tire
geometry in it at all; those are separate shared assets under
`vehicles/common/wheels/` and `vehicles/common/tires/`, attached per the
car's part config. So the pipeline is: figure out exactly which meshes a
specific trim actually uses (body + wheels + tires), then build a clean
scene from just those.

## Pipeline overview

1. **Extract source files** from the BeamNG install (not from git -- these
   are BeamNG's own licensed assets; see "What to keep in version control").
2. **`resolve_parts.py`**: turn a trim's `.pc` config into the exact list of
   mesh names that trim uses, by cross-referencing each part's `.jbeam`
   definition.
3. **`build_hero_glb.py`** (run inside Blender, headless): import the
   vehicle `.dae`, strip it to the resolved mesh list, build the 4
   wheel/tire corner assemblies at their real hub positions, build PBR
   materials from BeamNG's own `materials.json` files, and export a GLB.
   Also renders a quick Blender-side preview (studio lights + ground) so you
   can sanity-check the result without leaving Blender.
4. **`viewer.html`**: a minimal three.js page that loads the GLB with
   `RoomEnvironment` lighting and orbit/zoom controls, to confirm it holds
   up outside Blender before it goes anywhere near the Flask launcher UI.

## Step-by-step (what was actually run for etkc)

### 0. Prerequisites

- BeamNG.tech install, found via `C:\Users\<you>\AppData\Local\BeamNG\BeamNG.tech.ini`
  → `installPath` (for this machine: `C:\Users\Jango\BeamNG.tech.v0.38.3.0\`)
- Blender (4.4 used here), not necessarily on PATH:
  `C:\Program Files\Blender Foundation\Blender 4.4\blender.exe`
- A scratch working directory outside git, e.g. `scratch_vehicle_extract/`
  at the repo root (gitignored -- see below)

### 1. Extract the vehicle's own files

```bash
mkdir -p scratch_vehicle_extract/etkc_raw
cd scratch_vehicle_extract/etkc_raw
unzip -o "<installPath>/content/vehicles/etkc.zip" -d .
```

Find the `.pc` file matching the launcher's car label -- e.g. the launcher's
"ETK K-Series Trackday A" is `vehicles/etkc/kc6x_trackday_A.pc`. Match by
reading the `.pc`'s `"name"` field in its parent info, or just by filename
pattern guessing + checking `"information"` blocks in the related `.jbeam`.

### 2. Resolve the trim's mesh list

```bash
python scripts/vehicle_viewer/resolve_parts.py \
    scratch_vehicle_extract/etkc_raw/vehicles/etkc \
    scratch_vehicle_extract/etkc_raw/vehicles/etkc/kc6x_trackday_A.pc \
    scratch_vehicle_extract/resolved_etkc.json
```

This reads the `.pc`'s part list, finds each part's definition across every
`.jbeam` in the vehicle folder, and pulls the real mesh names out of each
part's `flexbodies` array (part names don't always equal mesh names --
e.g. `etkc_taillight_sport_L` is a jbeam part whose flexbodies point at the
base `etkc_taillightframe_L` mesh plus `_ttsport` variant glass/lens
meshes). Prints how many parts had no mesh (normal -- ECU/diff/physics-only
parts have none).

Cross-check which resolved names are actually in the main `.dae` vs. need a
second source (wheels, spoilers, brake parts live elsewhere):

```python
# anything in resolved_etkc.json's mesh_names but not in the vehicle .dae's
# own object list needs tracking down separately (see step 3)
```

### 3. Find the wheel/tire assets

The `.pc` names the wheel/tire parts directly (e.g.
`"wheel_F_5": "etk_wheel_04a_19x9_F"`, `"tire_F_19x9": "tire_F_245_35_19_race"`).
Those are jbeam part names, not mesh or file names, so:

1. Search `common.zip`'s file listing (don't extract the whole 3.9GB archive)
   for a `.jbeam` defining that part name, e.g.:
   ```bash
   unzip -l common.zip | grep -iE "wheel_04a|tires_19"
   ```
2. Extract just the matching jbeam + the `.dae`/`materials.json` files it
   implies, and read the part's `flexbodies` entry for the real mesh name
   and any `scale`/`pos`/`rot` tweak (rim width, tire width, L/R mirroring).
3. Find which shared `.dae` under `common/wheels/` or `common/tires/`
   actually contains that mesh id (`grep -l 'id="<mesh_name>"' *.dae`) --
   these shared files bundle many brands'/sizes' wheels together, same as
   the vehicle `.dae` bundles every trim.
4. Get each wheel's real **hub position** from `<car>_brakes.jbeam`: every
   brake part variant (rotor, caliper, pads) references the same hub node
   group with an identical literal `pos` -- e.g. front-left is consistently
   `{"x":0.76, "y":-1.295, "z":0.351}` across every etkc brake part. That
   recurring value is a reliable anchor; don't try to resolve BeamNG's
   actual node/flexbody local-coordinate system, it's not worth it for a
   static hero shot.

Extract only the specific files identified this way, not the full zips --
`common.zip` is ~3.9GB.

### 4. Edit the VEHICLE CONFIG block in `build_hero_glb.py`

Everything in that block is per-car and documented inline: file paths,
`WHEEL_MESH_NAMES`/`TIRE_MESH_NAMES`, the `CORNERS` table (hub positions +
scale tweaks per corner from step 3), `FLAT_PAINT_MATS` (see "Why flat
paint" below), and `GLASS_MATS`.

### 5. Run it

```bash
"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe" \
    --background --factory-startup \
    --python scripts/vehicle_viewer/build_hero_glb.py
```

Outputs (into the scratch workdir): `<car>_hero.glb`, `<car>_hero_preview.png`
(Blender-side sanity check render).

### 6. Check it in three.js

```bash
cd scratch_vehicle_extract
python -m http.server 8008
```

Open `http://localhost:8008/viewer.html?model=<car>_hero.glb`.

## Gotchas hit while building this (so they don't get re-debugged)

- **`bpy.ops.object.origin_set` is flaky in a tight headless loop.** It
  silently no-op'd on one of eight wheel/tire objects when called
  repeatedly without a UI context, leaving that object's origin at the
  wrong place and (because the ground height is derived from the lowest
  wheel point) pushing the entire ground plane ~0.35m too low -- which
  looked like the whole car floating, with the correctly-placed far-side
  wheel peeking through the gap underneath like the car was sitting on a
  pedestal. Fixed by computing the recenter/rotate/place transform as a
  single pure matrix multiply instead of using that operator.
- **Mesh-emission light panels don't light an EEVEE scene** without baked
  irradiance volumes/light probes -- only real `Light` datablocks reliably
  illuminate (both direct and, with `scene.eevee.use_raytracing = True`,
  as sharp reflections). Don't use emissive planes for "fake area lights"
  in EEVEE Next; use `AREA` lights.
- **`obj.visible_camera = False` doesn't hide an EEVEE Next area light's
  visible shape** the way it hides a mesh object from camera rays. Keep
  lights positioned where the camera can't see them directly (not just
  flagged invisible), especially low panels near a gap under the car.
- **Why flat paint colors instead of real textures**: the base paint
  material's `colorMap` (e.g. `etkc_d.dds`) often references a file that
  doesn't exist on disk at all -- actual paint comes from BeamNG's
  skin/palette system (`"paint_design": "etkc_skin_signaturestripes"` in
  the `.pc`), a separate recoloring mechanism not worth chasing for a
  product-hero shot. A flat `Base Color` + clearcoat reads as glossy paint
  just fine -- but gloss only reads as gloss if there's something
  high-contrast nearby to reflect; a flat/uniform environment (lighting or
  `scene.environment`) makes even a near-zero-roughness clearcoat look
  matte. Worth a bespoke multi-panel env over a generic procedural one.
- **Headlight/signal/tail/brake/reverse/fog-light materials have no
  texture definition anywhere** -- not in the vehicle's own
  `main.materials.json`/`skin.materials.json`, not in any shared
  `common/etk/*.materials.json`. BeamNG drives their actual on/off
  emissive state at runtime from each part's jbeam light props, not a
  static material. Lumping them all into one flat cream-white fallback
  (the original heuristic) makes every light element -- lens, chrome
  reflector housing, amber turn signal, white DRL -- the same flat color,
  which is exactly what reads as "looks like a sticker." Fixed by giving
  each element name its own heuristic color (amber for `signal`, red for
  `chmsl`/`brakelight`/`taillight`, white for `reverselight`, etc.) in
  `build_material()`'s fallback branch -- check the MOST SPECIFIC
  substrings first, since `"headlight"`/`"runninglight"`/`"foglight"` all
  contain `"light"`. Also don't make the chrome reflector housing too
  metallic (we tried `metalness: 0.85`) without reliable reflection
  content nearby -- a flat, non-faceted reflector mesh at high metalness
  with nothing bright to catch just renders near-black from most angles,
  since metals have no diffuse component.
- **Glass looks wrong (streaky/dirty) if you use the real `colorMap` as the
  literal Base Color.** BeamNG's glass diffuse texture is meant to blend
  with a live in-game environment reflection (`dynamicCubemap`), not stand
  alone. Build glass as mostly Transmission + low roughness, mixing in the
  real texture at ~10-15% strength just for subtle tint/dirt.
- **Texture size dominates GLB size, not geometry.** The original 33MB
  `.dae` has zero embedded textures (just external file references) --
  geometry for a full 118-mesh trim is only ~4MB once exported to GLB's
  binary format. An unconstrained export embedding every texture at native
  resolution came to 80MB, of which ~63MB was two interior texture sets
  (4096px normal/color/roughness maps) that are barely visible from outside
  through tinted glass. Capping every texture on load (`MAX_TEX_SIZE` in
  `build_hero_glb.py`) to 1024px cut it to 15.5MB with no visible quality
  loss from a distance, but looked soft up close on the tires/grille/badges
  in the live three.js viewer (zoomed in, orbiting) -- bumped the cap to
  2048px (36.5MB) to fix that. Try this knob before reaching for
  Draco/meshopt mesh compression -- geometry was never the problem, and
  the right cap is a judgment call based on how close the viewer lets
  people zoom in, not a fixed number.
- **DDS loads natively in Blender 4.4** (confirmed by directly checking
  decoded pixel values) -- no format conversion step needed before using
  BeamNG's own texture files as Blender image textures.

## What to keep in version control

Nothing under the scratch workdir should be committed -- it's BeamNG's own
licensed game assets (extracted zips) plus build output (GLB/renders), not
source. The workdir is gitignored at the repo root. What IS committed:

- This README and the two Python scripts (`resolve_parts.py`,
  `build_hero_glb.py`) -- reusable pipeline code
- `viewer.html` -- the three.js check page
- Nothing from `<car>_raw/`, `common_raw/`, or any `.glb`/`.png` output --
  regenerate these by re-running the pipeline against your own BeamNG
  install

## Doing this for a new car

1. Steps 1-3 above, for the new car's zip and the launcher's intended trim
2. Edit the whole `VEHICLE CONFIG` block at the top of `build_hero_glb.py`
   (change `CAR`, wheel/tire mesh names and source files, the `CORNERS`
   table, `FLAT_PAINT_MATS`, `GLASS_MATS`)
3. Run steps 5-6 above
4. If the car's `.dae` declares a different `<up_axis>` than `Z_UP`,
   Blender's importer will auto-rotate on import and the jbeam-derived hub
   `pos` values won't line up with Blender world space the same way --
   check this before assuming the hub coordinates transfer directly.
