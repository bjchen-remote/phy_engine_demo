"""Compatibility alias for physics_demo.core.meshes."""
import importlib as _importlib
import sys as _sys
_impl = _importlib.import_module("physics_demo.core.meshes")
_sys.modules[__name__] = _impl
