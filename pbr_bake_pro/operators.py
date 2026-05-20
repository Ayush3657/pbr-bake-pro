import os
import gc
import bpy
from bpy.types import Operator


MAP_DEFS = {
    'BASECOLOR': {
        'suffix': {'UE': '_BC',  'UNITY': '_Albedo',           'STANDARD': '_BaseColor'},
        'colorspace': 'sRGB',
        'is_data': False,
        'has_alpha': True,
        'method': 'DIFFUSE',
    },
    'METALLIC': {
        'suffix': {'UE': '_M',   'UNITY': '_Metallic',         'STANDARD': '_Metallic'},
        'colorspace': 'Non-Color',
        'is_data': True,
        'has_alpha': False,
        'method': 'EMIT_INPUT',
        'principled_input': 'Metallic',
    },
    'ROUGHNESS': {
        'suffix': {'UE': '_R',   'UNITY': '_Roughness',        'STANDARD': '_Roughness'},
        'colorspace': 'Non-Color',
        'is_data': True,
        'has_alpha': False,
        'method': 'ROUGHNESS',
    },
    'NORMAL': {
        'suffix': {'UE': '_N',   'UNITY': '_Normal',           'STANDARD': '_Normal'},
        'colorspace': 'Non-Color',
        'is_data': True,
        'has_alpha': False,
        'method': 'NORMAL',
    },
    'AO': {
        'suffix': {'UE': '_AO',  'UNITY': '_Occlusion',        'STANDARD': '_AO'},
        'colorspace': 'Non-Color',
        'is_data': True,
        'has_alpha': False,
        'method': 'AO',
    },
    'EMISSION': {
        'suffix': {'UE': '_E',   'UNITY': '_Emission',         'STANDARD': '_Emission'},
        'colorspace': 'sRGB',
        'is_data': False,
        'has_alpha': False,
        'method': 'EMIT',
    },
    'ALPHA': {
        'suffix': {'UE': '_A',   'UNITY': '_Alpha',            'STANDARD': '_Alpha'},
        'colorspace': 'Non-Color',
        'is_data': True,
        'has_alpha': False,
        'method': 'EMIT_INPUT',
        'principled_input': 'Alpha',
    },
}

FORMAT_EXT = {
    'PNG':      '.png',
    'TARGA':    '.tga',
    'JPEG':     '.jpg',
    'OPEN_EXR': '.exr',
    'TIFF':     '.tif',
}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _suffix(map_type, naming):
    return MAP_DEFS[map_type]['suffix'][naming]


def _prefix(naming):
    return "T_" if naming == 'UE' else ""


def _ext(fmt):
    return FORMAT_EXT.get(fmt, '.png')


def _find_principled(mat):
    if not mat or not mat.use_nodes:
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None


def _find_output(mat):
    if not mat or not mat.use_nodes:
        return None
    fallback = None
    for n in mat.node_tree.nodes:
        if n.type == 'OUTPUT_MATERIAL':
            if n.is_active_output:
                return n
            fallback = n
    return fallback


def _ensure_uvs(obj, auto_unwrap, margin_px):
    me = obj.data
    if me.uv_layers:
        return True
    if not auto_unwrap:
        return False

    bpy.context.view_layer.objects.active = obj
    prev_mode = obj.mode
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        island_margin = max(0.001, margin_px / 1024.0)
        try:
            bpy.ops.uv.smart_project(island_margin=island_margin)
        except Exception:
            bpy.ops.uv.smart_project()
    finally:
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass
        if prev_mode not in ('OBJECT',):
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except Exception:
                pass
    return len(me.uv_layers) > 0


def _create_image(name, width, height, is_data, alpha=False):
    existing = bpy.data.images.get(name)
    if existing is not None:
        try:
            bpy.data.images.remove(existing)
        except Exception:
            pass
    img = bpy.data.images.new(
        name=name,
        width=width,
        height=height,
        alpha=alpha,
        float_buffer=False,
        is_data=is_data,
    )
    return img


def _add_image_node(mat, img):
    nt = mat.node_tree
    node = nt.nodes.new('ShaderNodeTexImage')
    node.image = img
    node.label = "PBR_BAKE_TARGET"
    return node


def _setup_emit_input_bake(mat, input_name):
    """Reroute a Principled BSDF input through an Emission shader so it can be baked via EMIT."""
    nt = mat.node_tree
    principled = _find_principled(mat)
    output = _find_output(mat)
    if principled is None or output is None:
        return None
    target = principled.inputs.get(input_name)
    if target is None:
        return None

    emit = nt.nodes.new('ShaderNodeEmission')
    emit.label = "PBR_BAKE_EMIT"

    if target.is_linked:
        src = target.links[0].from_socket
        nt.links.new(src, emit.inputs['Color'])
    else:
        v = target.default_value
        try:
            f = float(v)
            emit.inputs['Color'].default_value = (f, f, f, 1.0)
        except TypeError:
            try:
                emit.inputs['Color'].default_value = (v[0], v[1], v[2], 1.0)
            except Exception:
                emit.inputs['Color'].default_value = (0.0, 0.0, 0.0, 1.0)

    orig_socket = None
    if output.inputs['Surface'].is_linked:
        orig_socket = output.inputs['Surface'].links[0].from_socket
    nt.links.new(emit.outputs['Emission'], output.inputs['Surface'])
    return (emit, orig_socket)


def _restore_emit_input_bake(mat, state):
    if state is None:
        return
    emit, orig_socket = state
    nt = mat.node_tree
    output = _find_output(mat)
    if emit.name in nt.nodes:
        try:
            nt.nodes.remove(emit)
        except Exception:
            pass
    if orig_socket is not None and output is not None:
        try:
            nt.links.new(orig_socket, output.inputs['Surface'])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Modal bake operator
# ---------------------------------------------------------------------------

class PBRBAKE_OT_bake_selected(Operator):
    bl_idname = "pbr_bake.bake_selected"
    bl_label = "Bake Selected"
    bl_description = "Bake PBR maps for selected objects. ESC cancels mid-bake"
    bl_options = {'REGISTER'}

    _timer = None

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    # ----- entry -----

    def invoke(self, context, event):
        props = context.scene.pbr_bake
        scene = context.scene

        maps = self._maps_to_bake(props)
        if not maps:
            self.report({'ERROR'}, "Select at least one PBR map to bake")
            return {'CANCELLED'}

        self._original_engine = scene.render.engine
        self._original_samples = None
        try:
            scene.render.engine = 'CYCLES'
            self._original_samples = scene.cycles.samples
            scene.cycles.samples = props.samples
        except Exception as e:
            scene.render.engine = self._original_engine
            self.report({'ERROR'}, f"Cycles unavailable: {e}")
            return {'CANCELLED'}

        self._original_selection = list(context.selected_objects)
        self._original_active = context.view_layer.objects.active

        self._width = props.res_x if props.custom_resolution else int(props.resolution)
        self._height = props.res_y if props.custom_resolution else int(props.resolution)

        out_dir = bpy.path.abspath(props.output_dir)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            self._restore_engine(scene)
            self.report({'ERROR'}, f"Cannot create output folder: {e}")
            return {'CANCELLED'}
        self._out_dir = out_dir
        self._props = props
        self._maps = maps

        if props.bake_from_active:
            if self._original_active is None or self._original_active.type != 'MESH':
                self._restore_engine(scene)
                self.report({'ERROR'}, "Selected-to-Active needs an active mesh (the low-poly target)")
                return {'CANCELLED'}
            target_objs = [self._original_active]
            hp_to_lp = True
        else:
            target_objs = [o for o in self._original_selection if o.type == 'MESH']
            hp_to_lp = False
            if not target_objs:
                self._restore_engine(scene)
                self.report({'ERROR'}, "No mesh objects selected")
                return {'CANCELLED'}

        self._tasks = self._build_tasks(target_objs, hp_to_lp)
        self._task_idx = 0
        self._total_tasks = len(self._tasks)
        self._obj_state = {}
        self._errors = []
        self._success_count = 0
        self._cancelled = False

        wm = context.window_manager
        wm.progress_begin(0, self._total_tasks)
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        self._set_header(context, f"PBR Bake: starting {self._total_tasks} tasks. ESC to cancel.")
        return {'RUNNING_MODAL'}

    # ----- modal loop -----

    def modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            self._cancelled = True
            self._finish(context)
            self.report({'WARNING'}, "PBR Bake cancelled")
            return {'CANCELLED'}

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        if self._task_idx >= self._total_tasks:
            self._finish(context)
            if self._errors:
                first = self._errors[0]
                more = f" (+{len(self._errors)-1} more)" if len(self._errors) > 1 else ""
                self.report({'WARNING'}, f"Baked {self._success_count} OK / {len(self._errors)} failed. {first}{more}")
            else:
                self.report({'INFO'}, f"Baked {self._success_count} object(s) → {self._out_dir}")
            return {'FINISHED'}

        task = self._tasks[self._task_idx]
        kind = task['kind']
        obj_name = task['obj'].name if task.get('obj') else '?'

        try:
            if kind == 'INIT_OBJ':
                self._set_header(context,
                    f"[{self._task_idx+1}/{self._total_tasks}] Preparing {obj_name}")
                self._task_init_object(context, task)
            elif kind == 'BAKE':
                self._set_header(context,
                    f"[{self._task_idx+1}/{self._total_tasks}] Baking {task['map']} for {obj_name} "
                    f"({self._width}x{self._height} @ {self._props.samples} spp)")
                self._task_bake(context, task)
            elif kind == 'FINISH_OBJ':
                self._set_header(context,
                    f"[{self._task_idx+1}/{self._total_tasks}] Finalizing {obj_name}")
                self._task_finish_object(context, task)
                self._success_count += 1
        except Exception as e:
            self._errors.append(f"{obj_name}/{kind}: {e}")
            print(f"[PBR Bake] FAIL on {obj_name}/{kind}: {e}")
            self._skip_remaining_for_object(obj_name)

        self._task_idx += 1
        context.window_manager.progress_update(self._task_idx)
        return {'PASS_THROUGH'}

    # ----- task plan -----

    def _maps_to_bake(self, props):
        out = []
        if props.bake_basecolor: out.append('BASECOLOR')
        if props.bake_metallic:  out.append('METALLIC')
        if props.bake_roughness: out.append('ROUGHNESS')
        if props.bake_normal:    out.append('NORMAL')
        if props.bake_ao:        out.append('AO')
        if props.bake_emission:  out.append('EMISSION')
        if props.bake_alpha:     out.append('ALPHA')
        return out

    def _build_tasks(self, objects, hp_to_lp):
        tasks = []
        for obj in objects:
            tasks.append({'kind': 'INIT_OBJ', 'obj': obj, 'hp_to_lp': hp_to_lp})
            for m in self._maps:
                tasks.append({'kind': 'BAKE', 'obj': obj, 'map': m, 'hp_to_lp': hp_to_lp})
            tasks.append({'kind': 'FINISH_OBJ', 'obj': obj, 'hp_to_lp': hp_to_lp})
        return tasks

    def _skip_remaining_for_object(self, obj_name):
        """Advance past every remaining task for the current object after a failure."""
        i = self._task_idx + 1
        while i < self._total_tasks and self._tasks[i].get('obj') and self._tasks[i]['obj'].name == obj_name:
            i += 1
        self._task_idx = i - 1  # outer loop adds +1

    # ----- task implementations -----

    def _task_init_object(self, context, task):
        obj = task['obj']
        self._activate_only(context, obj)

        if not _ensure_uvs(obj, self._props.auto_uv_unwrap, self._props.uv_margin):
            raise RuntimeError("no UVs (enable Auto Unwrap or unwrap manually)")

        if not obj.material_slots or all(s.material is None for s in obj.material_slots):
            if task['hp_to_lp']:
                mat = bpy.data.materials.new(name=f"{obj.name}_BakeTarget")
                mat.use_nodes = True
                obj.data.materials.append(mat)
            else:
                raise RuntimeError("no materials")

        originals = {}
        work_mats = []
        for i, slot in enumerate(obj.material_slots):
            if slot.material is None:
                continue
            originals[i] = slot.material
            work = slot.material.copy()
            work.name = slot.material.name + "_BakeWork"
            if not work.use_nodes:
                work.use_nodes = True
            slot.material = work
            work_mats.append(work)

        self._obj_state[obj.name] = {
            'originals': originals,
            'work_mats': work_mats,
            'baked_images': {},
        }

    def _task_bake(self, context, task):
        obj = task['obj']
        map_type = task['map']
        state = self._obj_state.get(obj.name)
        if state is None:
            raise RuntimeError("init step did not run")

        if task['hp_to_lp']:
            for o in self._original_selection:
                try:
                    o.select_set(True)
                except Exception:
                    pass
            context.view_layer.objects.active = obj
        else:
            self._activate_only(context, obj)

        img = self._bake_one_map(context, obj, state['work_mats'], map_type,
                                 self._width, self._height, self._out_dir, self._props,
                                 use_selected_to_active=task['hp_to_lp'])
        state['baked_images'][map_type] = img
        gc.collect()

    def _task_finish_object(self, context, task):
        obj = task['obj']
        state = self._obj_state.get(obj.name)
        if state is None:
            raise RuntimeError("init step did not run")

        baked_images = state['baked_images']

        orm_image = None
        if self._props.pack_orm:
            try:
                orm_image = self._build_orm(obj, baked_images, self._width, self._height,
                                            self._out_dir, self._props)
            except Exception as e:
                self._errors.append(f"{obj.name}/ORM: {e}")
                print(f"[PBR Bake] ORM pack failed for {obj.name}: {e}")

        base_name = list(state['originals'].values())[0].name if state['originals'] else obj.name
        new_mat = self._build_pbr_material(base_name, baked_images, orm_image, self._props)

        if self._props.replace_material:
            self._apply_new_material(obj, new_mat, state['originals'], self._props)
        else:
            for i, m in state['originals'].items():
                if i < len(obj.material_slots):
                    obj.material_slots[i].material = m

        for w in state['work_mats']:
            if w and w.users == 0:
                try:
                    bpy.data.materials.remove(w)
                except Exception:
                    pass

        self._obj_state.pop(obj.name, None)
        gc.collect()

    # ----- per-map bake -----

    def _bake_one_map(self, context, obj, work_mats, map_type, width, height, out_dir, props,
                      use_selected_to_active=False):
        scene = context.scene
        cycles = scene.cycles
        bake_settings = scene.render.bake

        m = MAP_DEFS[map_type]
        prefix = _prefix(props.naming_convention)
        suffix = _suffix(map_type, props.naming_convention)
        img_name = f"{prefix}{obj.name}{suffix}"
        img_path = os.path.join(out_dir, img_name + _ext(props.file_format))

        img = _create_image(img_name, width, height, m['is_data'], alpha=m['has_alpha'])
        img.filepath_raw = img_path
        img.file_format = props.file_format
        try:
            img.colorspace_settings.name = m['colorspace']
        except Exception:
            pass

        img_nodes = []
        for mat in work_mats:
            n = _add_image_node(mat, img)
            for nn in mat.node_tree.nodes:
                nn.select = False
            n.select = True
            mat.node_tree.nodes.active = n
            img_nodes.append((mat, n))

        emit_states = []
        if m['method'] == 'EMIT_INPUT':
            for mat in work_mats:
                state = _setup_emit_input_bake(mat, m['principled_input'])
                if state is not None:
                    emit_states.append((mat, state))
            bake_type = 'EMIT'
        else:
            bake_type = m['method']

        cycles.bake_type = bake_type

        if bake_type == 'DIFFUSE':
            bake_settings.use_pass_direct = False
            bake_settings.use_pass_indirect = False
            bake_settings.use_pass_color = True
        if bake_type == 'NORMAL':
            bake_settings.normal_space = 'TANGENT'
            bake_settings.normal_r = 'POS_X'
            bake_settings.normal_g = 'POS_Y'
            bake_settings.normal_b = 'POS_Z'

        bake_settings.margin = props.uv_margin
        try:
            bake_settings.margin_type = 'EXTEND'
        except Exception:
            pass

        try:
            kwargs = {'type': bake_type}
            if use_selected_to_active:
                kwargs['use_selected_to_active'] = True
                kwargs['cage_extrusion'] = props.cage_extrusion
                if props.ray_distance > 0:
                    kwargs['max_ray_distance'] = props.ray_distance
            bpy.ops.object.bake(**kwargs)
        finally:
            for mat, state in emit_states:
                _restore_emit_input_bake(mat, state)
            for mat, node in img_nodes:
                if node.name in mat.node_tree.nodes:
                    try:
                        mat.node_tree.nodes.remove(node)
                    except Exception:
                        pass

        try:
            img.save()
        except Exception as e:
            raise RuntimeError(f"failed to save {img_name}: {e}")

        return img

    # ----- ORM channel-pack (numpy, memory-safe) -----

    def _build_orm(self, obj, baked_images, width, height, out_dir, props):
        ao    = baked_images.get('AO')
        rough = baked_images.get('ROUGHNESS')
        metal = baked_images.get('METALLIC')
        if not (ao and rough and metal):
            return None

        try:
            import numpy as np
        except ImportError:
            raise RuntimeError("numpy not available — ORM packing skipped")

        prefix = _prefix(props.naming_convention)
        img_name = f"{prefix}{obj.name}_ORM"
        ext = _ext(props.file_format)
        img_path = os.path.join(out_dir, img_name + ext)

        packed = _create_image(img_name, width, height, is_data=True, alpha=False)

        n = width * height * 4
        # One reused scratch buffer + one output buffer = ~512MB peak at 4K (vs ~1.6GB for list())
        out = np.empty(n, dtype=np.float32)
        tmp = np.empty(n, dtype=np.float32)

        ao.pixels.foreach_get(tmp)
        out[0::4] = tmp[0::4]      # R = AO

        rough.pixels.foreach_get(tmp)
        out[1::4] = tmp[0::4]      # G = Roughness

        metal.pixels.foreach_get(tmp)
        out[2::4] = tmp[0::4]      # B = Metallic

        out[3::4] = 1.0            # A = opaque

        packed.pixels.foreach_set(out)
        packed.update()
        del out, tmp
        gc.collect()

        packed.filepath_raw = img_path
        packed.file_format = props.file_format
        try:
            packed.save()
        except Exception as e:
            raise RuntimeError(f"failed to save ORM: {e}")
        return packed

    # ----- material rebuild -----

    def _build_pbr_material(self, base_name, baked_images, orm_image, props):
        new_mat = bpy.data.materials.new(name=f"{base_name}_Baked")
        new_mat.use_nodes = True
        nt = new_mat.node_tree
        for n in list(nt.nodes):
            nt.nodes.remove(n)

        output = nt.nodes.new('ShaderNodeOutputMaterial')
        output.location = (900, 0)
        principled = nt.nodes.new('ShaderNodeBsdfPrincipled')
        principled.location = (500, 0)
        nt.links.new(principled.outputs[0], output.inputs['Surface'])

        x = -700
        y = 500

        def add_tex(img, data=True):
            nonlocal y
            tex = nt.nodes.new('ShaderNodeTexImage')
            tex.image = img
            tex.location = (x, y)
            if data:
                try:
                    tex.image.colorspace_settings.name = 'Non-Color'
                except Exception:
                    pass
            y -= 280
            return tex

        if 'BASECOLOR' in baked_images:
            tex = add_tex(baked_images['BASECOLOR'], data=False)
            try:
                tex.image.colorspace_settings.name = 'sRGB'
            except Exception:
                pass
            nt.links.new(tex.outputs['Color'], principled.inputs['Base Color'])

        if orm_image is not None and props.pack_orm:
            orm_tex = add_tex(orm_image, data=True)
            sep = nt.nodes.new('ShaderNodeSeparateColor')
            sep.location = (orm_tex.location.x + 280, orm_tex.location.y)
            nt.links.new(orm_tex.outputs['Color'], sep.inputs['Color'])
            nt.links.new(sep.outputs[1], principled.inputs['Roughness'])
            nt.links.new(sep.outputs[2], principled.inputs['Metallic'])
        else:
            if 'METALLIC' in baked_images:
                t = add_tex(baked_images['METALLIC'])
                nt.links.new(t.outputs['Color'], principled.inputs['Metallic'])
            if 'ROUGHNESS' in baked_images:
                t = add_tex(baked_images['ROUGHNESS'])
                nt.links.new(t.outputs['Color'], principled.inputs['Roughness'])

        if 'NORMAL' in baked_images:
            t = add_tex(baked_images['NORMAL'])
            nm = nt.nodes.new('ShaderNodeNormalMap')
            nm.location = (t.location.x + 280, t.location.y)
            nt.links.new(t.outputs['Color'], nm.inputs['Color'])
            nt.links.new(nm.outputs['Normal'], principled.inputs['Normal'])

        if 'EMISSION' in baked_images:
            t = add_tex(baked_images['EMISSION'], data=False)
            try:
                t.image.colorspace_settings.name = 'sRGB'
            except Exception:
                pass
            emis_input = principled.inputs.get('Emission Color') or principled.inputs.get('Emission')
            if emis_input is not None:
                nt.links.new(t.outputs['Color'], emis_input)

        if 'ALPHA' in baked_images:
            t = add_tex(baked_images['ALPHA'])
            alpha_input = principled.inputs.get('Alpha')
            if alpha_input is not None:
                nt.links.new(t.outputs['Color'], alpha_input)
            for attr, value in (('blend_method', 'CLIP'), ('surface_render_method', 'DITHERED')):
                try:
                    setattr(new_mat, attr, value)
                except Exception:
                    pass

        return new_mat

    def _apply_new_material(self, obj, new_mat, originals, props):
        if props.consolidate_slots:
            while len(obj.data.materials) > 1:
                obj.data.materials.pop(index=len(obj.data.materials) - 1)
            if len(obj.data.materials) == 1:
                obj.data.materials[0] = new_mat
            else:
                obj.data.materials.append(new_mat)
        else:
            for slot in obj.material_slots:
                slot.material = new_mat

        if not props.keep_original_backup:
            for orig in originals.values():
                if orig.users == 0:
                    try:
                        bpy.data.materials.remove(orig)
                    except Exception:
                        pass

    # ----- state / UI helpers -----

    def _activate_only(self, context, obj):
        for o in bpy.data.objects:
            try:
                o.select_set(False)
            except Exception:
                pass
        try:
            obj.select_set(True)
        except Exception:
            pass
        context.view_layer.objects.active = obj

    def _set_header(self, context, msg):
        for area in context.screen.areas:
            try:
                area.header_text_set(msg)
            except Exception:
                pass
        print(f"[PBR Bake] {msg}")

    def _clear_header(self, context):
        for area in context.screen.areas:
            try:
                area.header_text_set(None)
            except Exception:
                pass

    def _restore_engine(self, scene):
        try:
            scene.render.engine = self._original_engine
        except Exception:
            pass
        if self._original_samples is not None:
            try:
                scene.cycles.samples = self._original_samples
            except Exception:
                pass

    def _finish(self, context):
        scene = context.scene

        # Restore any partially-baked objects (cancel path)
        for obj_name, state in list(self._obj_state.items()):
            obj = bpy.data.objects.get(obj_name)
            if obj is not None:
                for i, orig in state['originals'].items():
                    if i < len(obj.material_slots):
                        try:
                            obj.material_slots[i].material = orig
                        except Exception:
                            pass
            for w in state['work_mats']:
                if w and w.users == 0:
                    try:
                        bpy.data.materials.remove(w)
                    except Exception:
                        pass
        self._obj_state.clear()

        self._restore_engine(scene)

        for o in bpy.data.objects:
            try:
                o.select_set(False)
            except Exception:
                pass
        for o in getattr(self, '_original_selection', []) or []:
            try:
                o.select_set(True)
            except Exception:
                pass
        if getattr(self, '_original_active', None) is not None:
            try:
                context.view_layer.objects.active = self._original_active
            except Exception:
                pass

        wm = context.window_manager
        if self._timer is not None:
            try:
                wm.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None
        try:
            wm.progress_end()
        except Exception:
            pass

        self._clear_header(context)
        for area in context.screen.areas:
            try:
                area.tag_redraw()
            except Exception:
                pass
        gc.collect()


# ---------------------------------------------------------------------------
# Misc operators
# ---------------------------------------------------------------------------

class PBRBAKE_OT_open_output_folder(Operator):
    bl_idname = "pbr_bake.open_output_folder"
    bl_label = "Open Output Folder"
    bl_description = "Open the texture output folder in the system file browser"

    def execute(self, context):
        import sys
        import subprocess
        path = bpy.path.abspath(context.scene.pbr_bake.output_dir)
        if not os.path.exists(path):
            try:
                os.makedirs(path, exist_ok=True)
            except Exception as e:
                self.report({'ERROR'}, f"Cannot create folder: {e}")
                return {'CANCELLED'}
        try:
            if sys.platform == 'win32':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            self.report({'ERROR'}, f"Cannot open folder: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class PBRBAKE_OT_select_all_maps(Operator):
    bl_idname = "pbr_bake.select_all_maps"
    bl_label = "All Maps"
    bl_description = "Enable every PBR map"

    def execute(self, context):
        p = context.scene.pbr_bake
        p.bake_basecolor = True
        p.bake_metallic = True
        p.bake_roughness = True
        p.bake_normal = True
        p.bake_ao = True
        p.bake_emission = True
        p.bake_alpha = True
        return {'FINISHED'}


class PBRBAKE_OT_select_no_maps(Operator):
    bl_idname = "pbr_bake.select_no_maps"
    bl_label = "None"
    bl_description = "Disable every PBR map"

    def execute(self, context):
        p = context.scene.pbr_bake
        p.bake_basecolor = False
        p.bake_metallic = False
        p.bake_roughness = False
        p.bake_normal = False
        p.bake_ao = False
        p.bake_emission = False
        p.bake_alpha = False
        return {'FINISHED'}


class PBRBAKE_OT_preset_ue5(Operator):
    bl_idname = "pbr_bake.preset_ue5"
    bl_label = "Unreal Engine 5"
    bl_description = "Preset: BaseColor + Normal + ORM (packed), UE naming, PNG"

    def execute(self, context):
        p = context.scene.pbr_bake
        p.naming_convention = 'UE'
        p.file_format = 'PNG'
        p.bake_basecolor = True
        p.bake_metallic = True
        p.bake_roughness = True
        p.bake_normal = True
        p.bake_ao = True
        p.bake_emission = False
        p.bake_alpha = False
        p.pack_orm = True
        return {'FINISHED'}


class PBRBAKE_OT_preset_unity(Operator):
    bl_idname = "pbr_bake.preset_unity"
    bl_label = "Unity URP"
    bl_description = "Preset: Albedo + Normal + Metallic + Roughness, Unity naming"

    def execute(self, context):
        p = context.scene.pbr_bake
        p.naming_convention = 'UNITY'
        p.file_format = 'PNG'
        p.bake_basecolor = True
        p.bake_metallic = True
        p.bake_roughness = True
        p.bake_normal = True
        p.bake_ao = True
        p.bake_emission = False
        p.bake_alpha = False
        p.pack_orm = False
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    PBRBAKE_OT_bake_selected,
    PBRBAKE_OT_open_output_folder,
    PBRBAKE_OT_select_all_maps,
    PBRBAKE_OT_select_no_maps,
    PBRBAKE_OT_preset_ue5,
    PBRBAKE_OT_preset_unity,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
