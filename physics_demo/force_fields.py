"""Compatibility alias for physics_demo.core.force_fields."""
import importlib as _importlib
import sys as _sys
_impl = _importlib.import_module("physics_demo.core.force_fields")
_sys.modules[__name__] = _impl
