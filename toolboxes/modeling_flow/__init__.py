"""Offline, operator-installed image-to-mesh backend for the modeling skill.

Importing this package never imports PyTorch or downloads a model. The heavy
runtime is imported only by ``generate`` or an explicit runtime health check.
"""

from .contracts import FlowError, load_config, load_request
from .meshes import audit_mesh, import_obj, scale_mesh

__all__ = ["FlowError", "load_config", "load_request", "audit_mesh", "import_obj", "scale_mesh"]

