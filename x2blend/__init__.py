"""X2Blend: DirectX .X models in Blender, with the file's own keys as F-curves."""


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
