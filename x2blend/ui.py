"""File > Import > DirectX / Daybreak (.x, .dat)."""

import bpy
from bpy_extras.io_utils import ImportHelper

from .build import import_file


class X2BLEND_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.x2blend"
    bl_label = "Import DirectX / Daybreak"
    bl_options = {'REGISTER', 'UNDO'}

    bl_description = "Import a .X model, or a Daybreak character file (NN.dat) with its weapon"

    filename_ext = ""
    filter_glob: bpy.props.StringProperty(default="*.x;*.X;*.dat", options={'HIDDEN'})
    scale: bpy.props.FloatProperty(
        name="Scale", default=0.01, min=1e-6, soft_max=10.0,
        description="Scale of the imported armature object. Keys stay in file units")
    set_scene_timing: bpy.props.BoolProperty(
        name="Set Scene to 60 fps", default=True,
        description="Keys sit on whole frames at 60 fps, which is also real time")

    def execute(self, context):
        try:
            armature, warnings = import_file(self.filepath, self.scale, self.set_scene_timing)
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        for warning in warnings:
            self.report({'WARNING'}, warning)
        self.report({'INFO'}, f"Imported {armature.name}")
        return {'FINISHED'}


def _menu(self, context):
    self.layout.operator(X2BLEND_OT_import.bl_idname, text="DirectX / Daybreak (.x, .dat)")


def register():
    bpy.utils.register_class(X2BLEND_OT_import)
    bpy.types.TOPBAR_MT_file_import.append(_menu)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    bpy.utils.unregister_class(X2BLEND_OT_import)
