"""Compatibility alias for physics_demo.analysis.observers."""
import importlib as _importlib
import sys as _sys
_impl = _importlib.import_module("physics_demo.analysis.observers")
_sys.modules[__name__] = _impl
