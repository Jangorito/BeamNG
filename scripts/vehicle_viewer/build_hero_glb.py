"""Build a lightweight hero-shot GLB (+ Blender preview render) for one
BeamNG vehicle trim, using only the meshes/materials that belong to that
specific default part config -- not the full combined .dae, which bundles
every body kit, race part, and historical variant the car ever shipped with.

This is step 3 of the pipeline in README.md (step 2 is resolve_parts.py,
which produces the resolved_<car>.json this script reads). Before running
this for a NEW car, edit the whole VEHICLE CONFIG block below -- see
README.md for exactly how to derive each value (hub positions, wheel/tire
mesh names, flat-paint material list, etc).

Run inside Blender, headless:
    blender --background --factory-startup --python build_hero_glb.py
"""
import json
import math
from pathlib import Path

import bpy
import mathutils

# =======================================================================
# VEHICLE CONFIG -- edit everything in this block when switching cars.
# See README.md ("Doing this for a new car") for how to derive each value.
# =======================================================================
WORKDIR = Path(r"C:\Users\Jango\workspace\BeamNG\scratch_vehicle_extract")
CAR = "etkc"

VEHICLE_DIR = WORKDIR / f"{CAR}_raw" / "vehicles" / CAR
COMMON_DIR = WORKDIR / "common_raw" / "vehicles" / "common"
DAE_PATH = VEHICLE_DIR / f"{CAR}.dae"
WHEEL_DAE = COMMON_DIR / "wheels" / "wheels_collection" / "etk_wheel.dae"
TIRE_DAE = COMMON_DIR / "tires" / "tires.dae"
WHEEL_MATERIALS_JSON = COMMON_DIR / "wheels" / "etk_wheel.materials.json"
TIRE_MATERIALS_JSON = COMMON_DIR / "tires" / "main.materials.json"

RESOLVED_JSON = WORKDIR / f"resolved_{CAR}.json"
OUT_RENDER = WORKDIR / f"{CAR}_hero_preview.png"
GLB_OUT = WORKDIR / f"{CAR}_hero.glb"

# Exact mesh node names to pull out of WHEEL_DAE / TIRE_DAE.
WHEEL_MESH_NAMES = {"etk_wheel_04a_19x9", "etk_wheel_04a_19x11"}
TIRE_MESH_NAMES = {"tire_01f_19x9_26", "tire_01f_19x11_26"}

# Four corners: name, wheel mesh, tire mesh, hub world position (read from
# <car>_brakes.jbeam -- the same hub pos recurs across every brake-part
# variant for a given corner, so any one of them is a reliable source),
# wheel/tire rim-width scale tweak (from that wheel/tire part's own jbeam
# "scale" field), and whether this corner mirrors the other side's mesh.
CORNERS = [
    # name,  wheel_src,              tire_src,                hub_pos,                    wheel_scale_x, tire_scale_x, mirror
    ("FL", "etk_wheel_04a_19x9",  "tire_01f_19x9_26",  (0.76, -1.295, 0.351), 1.01, 1.05, False),
    ("FR", "etk_wheel_04a_19x9",  "tire_01f_19x9_26",  (-0.76, -1.295, 0.351), 1.01, 1.05, True),
    ("RL", "etk_wheel_04a_19x11", "tire_01f_19x11_26", (0.76, 1.2936, 0.35), 0.91, 0.95, False),
    ("RR", "etk_wheel_04a_19x11", "tire_01f_19x11_26", (-0.76, 1.2936, 0.35), 0.91, 0.95, True),
]

# Material names that should get a flat paint color instead of a (missing
# or broken) real texture -- the base "paint" material's colorMap often
# references a file that doesn't actually exist on disk, because real paint
# comes from BeamNG's skin/palette system instead. See README.md.
FLAT_PAINT_MATS = {"etkc", "etkc_main", "etkc_sporttrim", "etkc_raceparts", "etkc_extra"}
PAINT_COLOR = (0.42, 0.02, 0.02, 1.0)

# Material names that should be built as real transmissive glass rather
# than opaque PBR, beyond the generic "glass"/"windshield" substring match.
GLASS_MATS = {"etkc_glass", "etkc_glass_int", "etkc_glass_lightweight", "etkc_glass_lightweight_int",
              "etkc_headlightglass", "generic_glass_int", "glass_invisible", "etkc_windshield_dmg",
              "etkc_glass_dmg", "etkc_windshield_lightweight_dmg"}

MAX_TEX_SIZE = 1024  # web hero viewer doesn't need more; interior maps were
                      # 4096px and barely visible through tinted glass --
                      # that alone was ~63MB of an 80MB GLB.
# =======================================================================
# END VEHICLE CONFIG
# =======================================================================

resolved = json.loads(RESOLVED_JSON.read_text(encoding="utf-8"))
KEEP_MESH_NAMES = set(resolved["mesh_names"])

# ---------------------------------------------------------------------
# 1. Clean scene + import main vehicle body
# ---------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.collada_import(filepath=str(DAE_PATH))

all_objs = list(bpy.context.scene.objects)
kept, removed = [], []
for obj in all_objs:
    if obj.type != 'MESH':
        continue
    if obj.name in KEEP_MESH_NAMES:
        kept.append(obj)
    else:
        removed.append(obj)
for obj in removed:
    bpy.data.objects.remove(obj, do_unlink=True)
print(f"Body: kept {len(kept)} meshes, removed {len(removed)}")

# ---------------------------------------------------------------------
# 2. Import wheel + tire source files, strip to the two sizes we need
# ---------------------------------------------------------------------
def import_and_filter(dae_path: Path, keep_names: set[str]) -> dict[str, bpy.types.Object]:
    before = set(bpy.context.scene.objects)
    bpy.ops.wm.collada_import(filepath=str(dae_path))
    new_objs = [o for o in bpy.context.scene.objects if o not in before]
    result = {}
    for obj in new_objs:
        if obj.type == 'MESH' and obj.name in keep_names:
            result[obj.name] = obj
        else:
            bpy.data.objects.remove(obj, do_unlink=True)
    return result

wheel_objs = import_and_filter(WHEEL_DAE, WHEEL_MESH_NAMES)
tire_objs = import_and_filter(TIRE_DAE, TIRE_MESH_NAMES)
print(f"Wheel objs: {list(wheel_objs.keys())}, Tire objs: {list(tire_objs.keys())}")

# ---------------------------------------------------------------------
# 3. Build 4 wheel+tire corner assemblies at real hub positions
#    (z-up, no axis remap needed as long as the vehicle .dae declares
#    Z_UP, matching Blender's native convention -- check <car>.dae's
#    <up_axis> element if porting to a new car and this looks wrong)
# ---------------------------------------------------------------------
def world_bbox_center(obj) -> mathutils.Vector:
    pts = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    mn = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (mn + mx) / 2

built_wheels = []
for name, wsrc, tsrc, hub, wscale_x, tscale_x, mirror in CORNERS:
    w = wheel_objs[wsrc].copy()
    w.data = wheel_objs[wsrc].data.copy()
    w.name = f"wheel_{name}"
    bpy.context.collection.objects.link(w)

    t = tire_objs[tsrc].copy()
    t.data = tire_objs[tsrc].data.copy()
    t.name = f"tire_{name}"
    bpy.context.collection.objects.link(t)

    hub_v = mathutils.Vector(hub)

    for obj, sx in ((w, wscale_x), (t, tscale_x)):
        # single functional matrix rebuild from the CURRENT matrix_world,
        # read once up front -- no bpy.ops (origin_set proved flaky when
        # called many times in a tight headless loop: it silently no-op'd
        # on one of the eight objects) and no interleaved location/matrix
        # reads that could see a stale, not-yet-recomputed matrix_world.
        c = world_bbox_center(obj)
        if mirror:
            transform = (mathutils.Matrix.Translation(hub_v)
                         @ mathutils.Matrix.Rotation(math.radians(180), 4, 'Z')
                         @ mathutils.Matrix.Translation(-c))
        else:
            transform = mathutils.Matrix.Translation(hub_v - c)
        obj.matrix_world = transform @ obj.matrix_world
        obj.scale.x *= sx
    built_wheels += [w, t]

print(f"Built {len(built_wheels)} wheel/tire objects")

kept = kept + built_wheels  # include in bounds calc below

# ---------------------------------------------------------------------
# 4. Material resolution (body/glass/lights from vehicle json,
#    wheel/tire from their own shared materials.json files)
# ---------------------------------------------------------------------
main_mats = json.loads((VEHICLE_DIR / "main.materials.json").read_text(encoding="utf-8"))
skin_mats = json.loads((VEHICLE_DIR / "skin.materials.json").read_text(encoding="utf-8"))
wheel_mats = json.loads(WHEEL_MATERIALS_JSON.read_text(encoding="utf-8"))
tire_mats = json.loads(TIRE_MATERIALS_JSON.read_text(encoding="utf-8"))
all_mat_defs = {**main_mats, **skin_mats, **wheel_mats, **tire_mats}

TEX_DIRS = [VEHICLE_DIR, COMMON_DIR / "wheels", COMMON_DIR / "tires"]
disk_by_full_stem: dict[str, Path] = {}
for d in TEX_DIRS:
    if not d.exists():
        continue
    for f in d.iterdir():
        if f.is_file() and f.suffix.lower() in (".dds", ".png"):
            stem = f.name.rsplit(".", 1)[0]
            disk_by_full_stem.setdefault(stem.lower(), f)


def resolve_tex(ref: str | None) -> Path | None:
    if not ref:
        return None
    name = ref.replace("\\", "/").split("/")[-1]
    stem = name.rsplit(".", 1)[0]
    return disk_by_full_stem.get(stem.lower())


image_cache: dict[str, bpy.types.Image] = {}


def get_image(path: Path, non_color: bool = False):
    key = str(path)
    if key in image_cache:
        return image_cache[key]
    img = bpy.data.images.load(key)
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    w, h = img.size
    if w > MAX_TEX_SIZE or h > MAX_TEX_SIZE:
        scale = MAX_TEX_SIZE / max(w, h)
        img.scale(max(1, round(w * scale)), max(1, round(h * scale)))
    image_cache[key] = img
    return img


def find_stage_with_color(mat_def: dict) -> dict | None:
    for stage in mat_def.get("Stages", []):
        if isinstance(stage, dict) and (stage.get("baseColorMap") or stage.get("colorMap")):
            return stage
    return None


built_materials: dict[str, bpy.types.Material] = {}


def build_glass_material(name: str, mat_def: dict | None) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = tree.nodes.get("Principled BSDF")

    bsdf.inputs["Base Color"].default_value = (0.85, 0.9, 0.92, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.04
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = 0.96
    if "IOR" in bsdf.inputs:
        bsdf.inputs["IOR"].default_value = 1.45

    stage = find_stage_with_color(mat_def) if mat_def else None
    if stage:
        color_path = resolve_tex(stage.get("baseColorMap") or stage.get("colorMap"))
        if color_path:
            tnode = tree.nodes.new("ShaderNodeTexImage")
            tnode.image = get_image(color_path)
            tnode.location = (-700, 300)
            mix = tree.nodes.new("ShaderNodeMixRGB")
            mix.location = (-400, 300)
            mix.inputs["Fac"].default_value = 0.12  # subtle tint/dirt only, not full albedo
            mix.inputs["Color1"].default_value = (0.9, 0.93, 0.95, 1.0)
            tree.links.new(tnode.outputs["Color"], mix.inputs["Color2"])
            tree.links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])

    mat.blend_method = 'BLEND'
    mat.show_transparent_back = False
    if hasattr(mat, "use_raytrace_refraction"):
        mat.use_raytrace_refraction = True
    return mat


def build_material(name: str) -> bpy.types.Material:
    if name in built_materials:
        return built_materials[name]

    lname = name.lower()

    if name in GLASS_MATS or ("glass" in lname and "int" not in lname) or "windshield" in lname:
        mat = build_glass_material(name, all_mat_defs.get(name))
        built_materials[name] = mat
        return mat

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    out_x = -600

    if name in FLAT_PAINT_MATS:
        bsdf.inputs["Base Color"].default_value = PAINT_COLOR
        bsdf.inputs["Roughness"].default_value = 0.22
        bsdf.inputs["Metallic"].default_value = 0.15
        if "Coat Weight" in bsdf.inputs:
            bsdf.inputs["Coat Weight"].default_value = 1.0
            bsdf.inputs["Coat Roughness"].default_value = 0.05
        built_materials[name] = mat
        return mat

    mat_def = all_mat_defs.get(name)
    stage = find_stage_with_color(mat_def) if mat_def else None

    if stage:
        color_path = resolve_tex(stage.get("baseColorMap") or stage.get("colorMap"))
        normal_path = resolve_tex(stage.get("normalMap"))
        rough_path = resolve_tex(stage.get("roughnessMap"))
        metal_path = resolve_tex(stage.get("metallicMap"))

        tree = mat.node_tree
        y = 300
        if color_path:
            tnode = tree.nodes.new("ShaderNodeTexImage")
            tnode.image = get_image(color_path)
            tnode.location = (out_x, y)
            tree.links.new(tnode.outputs["Color"], bsdf.inputs["Base Color"])
            y -= 300
        if normal_path:
            tnode = tree.nodes.new("ShaderNodeTexImage")
            tnode.image = get_image(normal_path, non_color=True)
            tnode.location = (out_x - 300, y)
            nmap = tree.nodes.new("ShaderNodeNormalMap")
            nmap.location = (out_x - 100, y)
            tree.links.new(tnode.outputs["Color"], nmap.inputs["Color"])
            tree.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
            y -= 300
        if rough_path:
            tnode = tree.nodes.new("ShaderNodeTexImage")
            tnode.image = get_image(rough_path, non_color=True)
            tnode.location = (out_x, y)
            tree.links.new(tnode.outputs["Color"], bsdf.inputs["Roughness"])
            y -= 300
        else:
            bsdf.inputs["Roughness"].default_value = 0.4
        if metal_path:
            tnode = tree.nodes.new("ShaderNodeTexImage")
            tnode.image = get_image(metal_path, non_color=True)
            tnode.location = (out_x, y)
            tree.links.new(tnode.outputs["Color"], bsdf.inputs["Metallic"])
    else:
        if "light" in lname or "signal" in lname or "chmsl" in lname:
            bsdf.inputs["Base Color"].default_value = (0.9, 0.9, 0.85, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.15
        elif "chrome" in lname or "mirror" in lname:
            bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
            bsdf.inputs["Metallic"].default_value = 1.0
            bsdf.inputs["Roughness"].default_value = 0.2
        else:
            bsdf.inputs["Base Color"].default_value = (0.08, 0.08, 0.08, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.5

    built_materials[name] = mat
    return mat


for obj in kept:
    if not obj.data.materials:
        continue
    for slot in obj.material_slots:
        if slot.material is None:
            continue
        slot.material = build_material(slot.material.name)

# ---------------------------------------------------------------------
# 4b. Export GLB (car geometry only -- no Blender lights/ground/camera;
#     the three.js viewer rebuilds lighting itself via RoomEnvironment)
# ---------------------------------------------------------------------
bpy.ops.object.select_all(action='DESELECT')
for obj in kept:
    obj.select_set(True)
bpy.ops.export_scene.gltf(
    filepath=str(GLB_OUT),
    use_selection=True,
    export_format='GLB',
    export_materials='EXPORT',
    export_yup=True,  # three.js/glTF convention is Y-up; Blender is Z-up natively
    export_apply=True,
)
print(f"Exported GLB: {GLB_OUT} ({GLB_OUT.stat().st_size / 1e6:.2f} MB)")

# ---------------------------------------------------------------------
# 5. Scene bounds
# ---------------------------------------------------------------------
min_v = mathutils.Vector((1e9, 1e9, 1e9))
max_v = mathutils.Vector((-1e9, -1e9, -1e9))
for obj in kept:
    for corner in obj.bound_box:
        wc = obj.matrix_world @ mathutils.Vector(corner)
        min_v.x, min_v.y, min_v.z = min(min_v.x, wc.x), min(min_v.y, wc.y), min(min_v.z, wc.z)
        max_v.x, max_v.y, max_v.z = max(max_v.x, wc.x), max(max_v.y, wc.y), max(max_v.z, wc.z)
center = (min_v + max_v) / 2
size = max_v - min_v
diag = max(size.x, size.y, size.z)
print(f"Bounds: min={min_v}, max={max_v}, size={size}")

# ground at wheel-contact level, not body min (body may have aero pieces
# lower, or -- as happened once -- a corrupted bbox throws this off; this
# is the lowest point of the actual wheel/tire geometry, not a guess)
wheel_bottoms = [
    (obj.matrix_world @ mathutils.Vector(c)).z
    for obj in built_wheels for c in obj.bound_box
]
ground_z = min(wheel_bottoms) if wheel_bottoms else min_v.z

# ---------------------------------------------------------------------
# 6. Studio environment: multiple area lights acting as softbox panels.
#    EEVEE Next ray-traces these as sharp/glossy reflections in glass
#    and clearcoat paint directly (no probe bake needed), unlike plain
#    emissive mesh planes which only contribute via baked irradiance.
#    (This whole section is Blender-preview-only -- it does NOT end up
#    in the GLB, which was already exported above. The three.js viewer
#    rebuilds lighting itself via RoomEnvironment; see viewer.html.)
# ---------------------------------------------------------------------
def add_area_light(name, location, rot_euler, energy, size, color=(1, 1, 1)):
    light_data = bpy.data.lights.new(name=name, type='AREA')
    light_data.energy = energy
    light_data.size = size
    light_data.color = color
    obj = bpy.data.objects.new(name, light_data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = rot_euler
    # light the scene and show up in reflections, but never as a flat
    # visible shape to the camera itself (matters for a low panel near
    # ground level, which can otherwise peek through the gap under the
    # car -- visible_camera=False is NOT respected by EEVEE Next for area
    # lights the way it is for mesh objects, so keep lights well clear of
    # anything the camera could see straight through, don't rely on this)
    obj.visible_camera = False
    return obj

add_area_light("key", (center.x + diag * 1.1, center.y - diag * 1.2, center.z + diag * 0.9),
                (math.radians(55), 0, math.radians(35)), 3500, diag * 0.9, (1.0, 0.97, 0.9))
add_area_light("fill", (center.x - diag * 1.3, center.y - diag * 0.5, center.z + diag * 0.6),
                (math.radians(65), 0, math.radians(-55)), 900, diag * 1.1, (0.82, 0.88, 1.0))
add_area_light("rim", (center.x - diag * 0.1, center.y + diag * 1.6, center.z + diag * 1.0),
                (math.radians(-65), 0, math.radians(5)), 2400, diag * 0.7, (0.85, 0.9, 1.0))
add_area_light("overhead", (center.x, center.y, center.z + diag * 1.8),
                (0, 0, 0), 1500, diag * 1.4, (1.0, 1.0, 1.0))

# ground plane (visible, receives shadow) -- Blender preview only
bpy.ops.mesh.primitive_plane_add(size=diag * 6, location=(center.x, center.y, ground_z))
ground = bpy.context.active_object
ground.name = "ground"
gmat = bpy.data.materials.new("ground_mat")
gmat.use_nodes = True
gmat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.015, 0.015, 0.018, 1.0)
gmat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.35
ground.data.materials.append(gmat)

world = bpy.data.worlds.new("studio_world")
bpy.context.scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.01, 0.01, 0.012, 1.0)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6

# ---------------------------------------------------------------------
# 7. Camera (Blender preview only)
# ---------------------------------------------------------------------
cam_data = bpy.data.cameras.new("hero_cam")
cam_data.lens = 85
cam_obj = bpy.data.objects.new("hero_cam", cam_data)
bpy.context.collection.objects.link(cam_obj)
cam_dist = diag * 1.9
cam_obj.location = (center.x + cam_dist * 0.75, center.y - cam_dist * 1.0, center.z + diag * 0.3)
bpy.context.scene.camera = cam_obj
direction = center - cam_obj.location
cam_obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

# ---------------------------------------------------------------------
# 8. Render settings - EEVEE Next with raytracing for real reflections
# ---------------------------------------------------------------------
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE_NEXT'
scene.render.resolution_x = 1600
scene.render.resolution_y = 1000
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = str(OUT_RENDER)
scene.eevee.taa_render_samples = 128
if hasattr(scene.eevee, "use_raytracing"):
    scene.eevee.use_raytracing = True
scene.view_settings.view_transform = 'AgX'
scene.view_settings.look = 'AgX - Medium High Contrast'

bpy.ops.render.render(write_still=True)
print(f"Rendered to {OUT_RENDER}")
