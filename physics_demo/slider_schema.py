"""Compatibility alias for physics_demo.io.slider_schema."""
import importlib as _importlib
import sys as _sys
_impl = _importlib.import_module("physics_demo.io.slider_schema")
_sys.modules[__name__] = _impl
