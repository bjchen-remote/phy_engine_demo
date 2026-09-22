"""Select the native solver while retaining the Python implementation as oracle."""

from __future__ import annotations

import time
from typing import Any

from physics_demo.core.engine import run_scene as run_scene_python
from physics_demo.core.liquids import LIQUID_PRESETS
from physics_demo.core.native_backend import NativeBackendUnavailable, NativeSimulationError, run_scene_native
from physics_demo.io.planning import timing_estimate


class FallbackBudgetExceeded(NativeSimulationError):
    """The native route failed and the safe Python fallback no longer fits."""


def run_scene(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> dict[str, Any]:
    requested = plan.get("requested_backend", scene.get("budget", {}).get("backend", "auto"))
    selected = plan.get("backend", "native")
    particle_gravity = bool(scene["interactions"]["mutual_gravity"] and plan.get("planned_particles"))
    native_liquid_preset = any(
        entity.get("type") == "fluid"
        and LIQUID_PRESETS[entity.get("preset", "water")]["native_required"]
        for entity in scene.get("entities", [])
    )
    if selected == "coupled":
        from physics_demo.core.coupled_solver import run_scene_coupled
        return run_scene_coupled(scene, plan, deadline)
    if selected == "connections":
        from physics_demo.core.connections_solver import run_scene_connections
        return run_scene_connections(scene, plan, deadline)
    if selected == "mesh":
        from physics_demo.core.mesh_solver import run_scene_mesh
        return run_scene_mesh(scene, plan, deadline)
    if selected == "slider":
        from physics_demo.core.sliders import run_scene as run_sliders
        return run_sliders(scene, plan, deadline)
    if selected == "python" and particle_gravity:
        raise NativeBackendUnavailable("Particle self-gravity requires the native solver; no force-dropping fallback is permitted.")
    if selected == "python" and native_liquid_preset:
        raise NativeBackendUnavailable(
            "Non-water liquid presets require the native C11 liquid solver; "
            "the Python reference has different viscosity behavior and no equivalent surface-tension model."
        )
    fallback_reason = None
    if selected != "python":
        try:
            return run_scene_native(scene, plan, deadline)
        except (NativeBackendUnavailable, NativeSimulationError) as error:
            if requested == "native" or native_liquid_preset or particle_gravity:
                raise
            fallback_timing = timing_estimate(scene, {**plan, "backend": "python"}, include_video=False)
            remaining = max(0.0, deadline - time.monotonic())
            if fallback_timing["solver_p90_s"] > remaining:
                raise FallbackBudgetExceeded(
                    "Native execution failed and the calibrated Python fallback p90 "
                    f"({fallback_timing['solver_p90_s']:.3f} s) exceeds the remaining "
                    f"deadline ({remaining:.3f} s)."
                ) from error
            fallback_reason = str(error)
    result = run_scene_python(scene, plan, deadline)
    result["diagnostics"]["backend"] = "python-reference"
    if fallback_reason is not None:
        result["diagnostics"]["native_fallback"] = fallback_reason
    return result
