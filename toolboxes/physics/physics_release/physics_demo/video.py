"""Compatibility alias for physics_demo.io.video."""
import importlib as _importlib
import sys as _sys
_impl = _importlib.import_module("physics_demo.io.video")
_sys.modules[__name__] = _impl
