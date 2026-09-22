"""Compatibility alias for physics_demo.io.mesh_scene."""
import importlib as _importlib
import sys as _sys
_impl = _importlib.import_module("physics_demo.io.mesh_scene")
_sys.modules[__name__] = _impl
