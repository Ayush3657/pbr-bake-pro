bl_info = {
    "name": "PBR Bake Pro",
    "author": "PBR Bake Pro",
    "version": (1, 3, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar (N) > PBR Bake",
    "description": "Bake materials to PBR textures for Unreal Engine 5, Unity, and other game engines",
    "category": "Material",
    "doc_url": "",
    "tracker_url": "",
}

from . import properties
from . import operators
from . import ui

_modules = (properties, operators, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()


if __name__ == "__main__":
    register()
