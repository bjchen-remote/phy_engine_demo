"""Optional private host SDK discovery for operator-run development harnesses.

This module is not copied into runtime packages and accepts no task input.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


HOST_SOURCE_ENV = "QQ_SIMULATOR_HOST_SOURCE"
SDK_FILES = ("__init__.py", "config.py", "input_media.py", "release_runner.py", "toolbox.py")


class HostSourceUnavailable(RuntimeError):
    """A requested integration harness has no compatible private host source."""


def resolve_host_source(explicit: str | Path | None = None, *, repository: Path | None = None,
                        environ: dict | None = None, required: bool = True) -> Path | None:
    """Prefer a CLI path, then environment, then the private sibling checkout.

    Both the private project root and its src directory are accepted. Explicit
    invalid choices fail even when optional, so integration tests never conceal
    a broken configured SDK by silently falling back or skipping.
    """
    environment = os.environ if environ is None else environ
    repository = repository or Path(__file__).resolve().parents[1]
    selected = explicit if explicit is not None else environment.get(HOST_SOURCE_ENV)
    configured = selected is not None
    root = (Path(selected).expanduser() if configured else
            repository / "qq-simulator-agent-opencode" / "src").resolve()
    for source in (root, root / "src"):
        package = source / "qq_simulator_agent"
        if all((package / name).is_file() for name in SDK_FILES):
            return source
    if not configured and not required:
        return None
    raise HostSourceUnavailable(
        "Private QQ host SDK unavailable at " + str(root) + ". Supply --host-source PATH "
        "or " + HOST_SOURCE_ENV + " (private project root or src directory). "
        "Standalone public toolbox tests do not require this SDK.")


def activate_host_source(source: Path) -> None:
    """Bind development imports to the selected SDK, without starting a client."""
    package = sys.modules.get("qq_simulator_agent")
    if package is not None:
        loaded = getattr(package, "__file__", None)
        if not loaded or Path(loaded).resolve().parent != source / "qq_simulator_agent":
            raise HostSourceUnavailable("A different private QQ host SDK is already imported")
    sys.path.insert(0, str(source))
