import bpy
from bpy.types import Panel


def _not_baking(context):
    """Used by sub-panels' poll() to hide them while a bake is running,
    so the user sees only the progress UI on the main panel."""
    p = getattr(context.scene, 'pbr_bake', None)
    return not (p and p.is_baking)


class PBRBAKE_PT_main(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PBR Bake'
    bl_label = 'PBR Bake Pro'
    bl_idname = 'PBRBAKE_PT_main'

    def draw(self, context):
        layout = self.layout
        props = context.scene.pbr_bake

        # ---- Live progress (shown only while a bake is running) ----
        if props.is_baking:
            box = layout.box()
            head = box.row()
            head.label(text="Baking…", icon='RENDER_STILL')
            head.label(text=f"{props.bake_current}/{props.bake_total}")

            # In-panel progress bar (Blender 4.0+: UILayout.progress)
            try:
                box.progress(
                    factor=props.bake_progress,
                    type='BAR',
                    text=f"{int(props.bake_progress * 100)}%",
                )
            except AttributeError:
                # Fallback for older Blender — text-only progress
                box.label(text=f"Progress: {int(props.bake_progress * 100)}%")

            if props.bake_status:
                box.label(text=props.bake_status)
            box.label(text="Press ESC to cancel", icon='CANCEL')
            return  # hide the rest of the panel while baking

        # ---- Bake button ----
        col = layout.column()
        col.scale_y = 1.6
        mesh_count = sum(1 for o in context.selected_objects if o.type == 'MESH')
        bake_row = col.row()
        bake_row.enabled = mesh_count > 0
        bake_row.operator('pbr_bake.bake_selected', icon='RENDER_STILL',
                          text=f"Bake {mesh_count} Object(s)" if mesh_count else "Bake (no selection)")

        # ---- Preset toggle (UE5 only) ----
        row = layout.row(align=True)
        row.label(text="Preset:")
        row.operator(
            'pbr_bake.preset_ue5',
            text="UE5",
            icon='CHECKBOX_HLT' if props.ue5_preset_active else 'CHECKBOX_DEHLT',
            depress=props.ue5_preset_active,
        )


class PBRBAKE_PT_resolution(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PBR Bake'
    bl_label = 'Resolution & Quality'
    bl_parent_id = 'PBRBAKE_PT_main'

    @classmethod
    def poll(cls, context):
        return _not_baking(context)

    def draw(self, context):
        layout = self.layout
        props = context.scene.pbr_bake

        col = layout.column()
        col.prop(props, 'custom_resolution')
        if props.custom_resolution:
            row = col.row(align=True)
            row.prop(props, 'res_x', text='X')
            row.prop(props, 'res_y', text='Y')
        else:
            col.prop(props, 'resolution', text='Size')

        col.separator()
        col.prop(props, 'samples')
        col.prop(props, 'uv_margin')


class PBRBAKE_PT_maps(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PBR Bake'
    bl_label = 'PBR Maps'
    bl_parent_id = 'PBRBAKE_PT_main'

    @classmethod
    def poll(cls, context):
        return _not_baking(context)

    def draw(self, context):
        layout = self.layout
        props = context.scene.pbr_bake

        row = layout.row(align=True)
        row.operator('pbr_bake.select_all_maps', text='All')
        row.operator('pbr_bake.select_no_maps', text='None')

        col = layout.column(align=True)
        col.prop(props, 'bake_basecolor', icon='COLOR')
        col.prop(props, 'bake_metallic')
        col.prop(props, 'bake_roughness')
        col.prop(props, 'bake_normal', icon='NORMALS_FACE')
        col.prop(props, 'bake_ao')
        col.prop(props, 'bake_emission', icon='LIGHT_SUN')
        col.prop(props, 'bake_alpha')

        layout.separator()
        sub = layout.column()
        sub.enabled = props.bake_ao and props.bake_roughness and props.bake_metallic
        sub.prop(props, 'pack_orm', icon='NODE_COMPOSITING')
        if not sub.enabled:
            sub.label(text="(needs AO + Roughness + Metallic)", icon='INFO')


class PBRBAKE_PT_output(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PBR Bake'
    bl_label = 'Output'
    bl_parent_id = 'PBRBAKE_PT_main'

    @classmethod
    def poll(cls, context):
        return _not_baking(context)

    def draw(self, context):
        layout = self.layout
        props = context.scene.pbr_bake

        layout.prop(props, 'output_dir', text='')
        layout.prop(props, 'file_format')
        layout.prop(props, 'naming_convention')
        layout.operator('pbr_bake.open_output_folder', icon='FILE_FOLDER')


class PBRBAKE_PT_material(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PBR Bake'
    bl_label = 'Material & UVs'
    bl_parent_id = 'PBRBAKE_PT_main'

    @classmethod
    def poll(cls, context):
        return _not_baking(context)

    def draw(self, context):
        layout = self.layout
        props = context.scene.pbr_bake

        layout.prop(props, 'auto_uv_unwrap')
        layout.separator()
        layout.prop(props, 'replace_material')
        sub = layout.column()
        sub.enabled = props.replace_material
        sub.prop(props, 'consolidate_slots')
        sub.prop(props, 'keep_original_backup')


class PBRBAKE_PT_advanced(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PBR Bake'
    bl_label = 'High-Poly to Low-Poly'
    bl_parent_id = 'PBRBAKE_PT_main'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _not_baking(context)

    def draw(self, context):
        layout = self.layout
        props = context.scene.pbr_bake

        layout.prop(props, 'bake_from_active')
        sub = layout.column()
        sub.enabled = props.bake_from_active
        sub.prop(props, 'cage_extrusion')
        sub.prop(props, 'ray_distance')

        if props.bake_from_active:
            box = layout.box()
            box.label(text="How to use:", icon='INFO')
            box.label(text="1. Select all high-poly objects")
            box.label(text="2. Shift-click the low-poly last")
            box.label(text="3. The active object receives the bake")


_classes = (
    PBRBAKE_PT_main,
    PBRBAKE_PT_resolution,
    PBRBAKE_PT_maps,
    PBRBAKE_PT_output,
    PBRBAKE_PT_material,
    PBRBAKE_PT_advanced,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
