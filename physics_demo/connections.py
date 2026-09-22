"""Compatibility alias for physics_demo.io.connections."""
import importlib as _importlib
import sys as _sys
_impl = _importlib.import_module("physics_demo.io.connections")
_sys.modules[__name__] = _impl
