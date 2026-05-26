import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    EnumProperty,
    IntProperty,
    BoolProperty,
    StringProperty,
    FloatProperty,
    PointerProperty,
)
from bpy.types import PropertyGroup


RESOLUTION_ITEMS = [
    ('256',  '256 x 256',   ''),
    ('512',  '512 x 512',   ''),
    ('1024', '1024 x 1024', ''),
    ('2048', '2048 x 2048', ''),
    ('4096', '4096 x 4096', ''),
    ('8192', '8192 x 8192', ''),
]

FORMAT_ITEMS = [
    ('PNG',      'PNG',  'Lossless, 8/16-bit, alpha supported'),
    ('TARGA',    'TGA',  'Lossless, alpha supported'),
    ('JPEG',     'JPEG', 'Lossy, smaller files, no alpha'),
    ('OPEN_EXR', 'EXR',  '32-bit float, HDR data'),
    ('TIFF',     'TIFF', 'Lossless, 16-bit supported'),
]

NAMING_ITEMS = [
    ('UE',       'Unreal Engine', 'T_Object_BC, T_Object_N, T_Object_ORM'),
    ('UNITY',    'Unity',         'Object_Albedo, Object_Normal, Object_MetallicSmoothness'),
    ('STANDARD', 'Standard',      'Object_BaseColor, Object_Normal, Object_Roughness'),
]


class PBRBakeProperties(PropertyGroup):

    # --- Resolution ---
    resolution: EnumProperty(
        name="Resolution",
        items=RESOLUTION_ITEMS,
        default='2048',
    )
    custom_resolution: BoolProperty(
        name="Custom Resolution",
        description="Use a non-square or non-power-of-two resolution",
        default=False,
    )
    res_x: IntProperty(name="Width",  default=2048, min=8, max=16384, subtype='PIXEL')
    res_y: IntProperty(name="Height", default=2048, min=8, max=16384, subtype='PIXEL')

    # --- Quality ---
    samples: IntProperty(
        name="Bake Samples",
        description="Cycles samples per pixel during bake. Higher = cleaner but slower",
        default=8,
        min=1,
        max=4096,
    )
    uv_margin: IntProperty(
        name="UV Margin (px)",
        description="Bleed in pixels around UV islands to avoid seams",
        default=16,
        min=0,
        max=128,
    )

    # --- Maps ---
    bake_basecolor: BoolProperty(name="Base Color", default=True)
    bake_metallic:  BoolProperty(name="Metallic",   default=True)
    bake_roughness: BoolProperty(name="Roughness",  default=True)
    bake_normal:    BoolProperty(name="Normal",     default=True)
    bake_ao:        BoolProperty(name="Ambient Occlusion", default=True)
    bake_emission:  BoolProperty(name="Emission",   default=False)
    bake_alpha:     BoolProperty(name="Alpha",      default=False)

    pack_orm: BoolProperty(
        name="Pack ORM Texture",
        description="Pack AO/Roughness/Metallic into a single RGB texture (R=AO, G=Roughness, B=Metallic). Standard channel-packing for Unreal Engine 5",
        default=True,
    )

    # --- Output ---
    output_dir: StringProperty(
        name="Output Folder",
        description="Where to save baked textures. Relative paths (// prefix) are relative to the .blend file",
        subtype='DIR_PATH',
        default="//baked_textures/",
    )
    file_format: EnumProperty(
        name="Format",
        items=FORMAT_ITEMS,
        default='PNG',
    )
    naming_convention: EnumProperty(
        name="Naming",
        items=NAMING_ITEMS,
        default='UE',
    )

    # --- UVs ---
    auto_uv_unwrap: BoolProperty(
        name="Auto Unwrap if Missing",
        description="If an object has no UV map, automatically generate one with Smart UV Project before baking",
        default=True,
    )
    wrap_uvs_to_unit: BoolProperty(
        name="Wrap UVs Outside [0,1]",
        description=(
            "Translate UV islands that lie outside the 0-1 bake space back into it before baking. "
            "Fixes black patches caused by tileable textures with UVs exceeding bounds. "
            "Original UVs are restored after the bake completes"
        ),
        default=True,
    )

    # --- Material replacement ---
    replace_material: BoolProperty(
        name="Replace With Baked Material",
        description="After baking, swap the object's material(s) for a new PBR material using the baked textures. Ready for FBX/glTF export",
        default=True,
    )
    keep_original_backup: BoolProperty(
        name="Keep Original as Backup",
        description="Don't delete the original material data-block. Lets you revert manually",
        default=True,
    )
    consolidate_slots: BoolProperty(
        name="Consolidate Material Slots",
        description="Collapse all material slots on the object into a single slot using the baked material. Ignored when Per-Slot Baking is on",
        default=True,
    )
    bake_per_slot: BoolProperty(
        name="Per Material Slot",
        description=(
            "Bake each material slot to its own complete texture set with its own baked PBR material. "
            "Use when an object has multiple distinct materials (e.g. wood + fabric) "
            "that should stay separate after baking. Cycles writes per-material based on each "
            "material's active image-texture node, so all slots are baked together in one pass"
        ),
        default=False,
    )

    # --- Selected to Active (HP -> LP) ---
    bake_from_active: BoolProperty(
        name="Selected to Active (HP→LP)",
        description="Bake from selected high-poly objects onto the active low-poly object. Useful for normal maps",
        default=False,
    )
    cage_extrusion: FloatProperty(
        name="Cage Extrusion",
        description="Distance to extrude the low-poly mesh outward when raycasting",
        default=0.01,
        min=0.0,
        max=1.0,
        unit='LENGTH',
        precision=4,
    )
    ray_distance: FloatProperty(
        name="Max Ray Distance",
        description="Maximum raycast distance (0 = unlimited)",
        default=0.0,
        min=0.0,
        max=10.0,
        unit='LENGTH',
        precision=4,
    )

    # --- Preset state ---
    # True means the UE5 preset is currently active (settings match it).
    # Defaults match the UE5 preset, so default value is True.
    ue5_preset_active: BoolProperty(
        name="UE5 Preset Active",
        description="Whether the UE5 preset is currently engaged",
        default=True,
    )

    # --- Runtime bake state (not user-editable, drives the UI progress bar) ---
    is_baking: BoolProperty(default=False)
    bake_progress: FloatProperty(default=0.0, min=0.0, max=1.0, subtype='FACTOR')
    bake_status: StringProperty(default="")
    bake_current: IntProperty(default=0, min=0)
    bake_total: IntProperty(default=0, min=0)


@persistent
def _reset_runtime_state(_dummy):
    """Clear modal-only state on file load — prevents the panel from showing
    a 'Baking...' progress bar that's left over from a previous session."""
    for scene in bpy.data.scenes:
        p = getattr(scene, 'pbr_bake', None)
        if p is None:
            continue
        p.is_baking = False
        p.bake_progress = 0.0
        p.bake_status = ""
        p.bake_current = 0
        p.bake_total = 0


def register():
    bpy.utils.register_class(PBRBakeProperties)
    bpy.types.Scene.pbr_bake = PointerProperty(type=PBRBakeProperties)
    if _reset_runtime_state not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_reset_runtime_state)


def unregister():
    if _reset_runtime_state in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_reset_runtime_state)
    del bpy.types.Scene.pbr_bake
    bpy.utils.unregister_class(PBRBakeProperties)
