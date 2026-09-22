"""Scene normalization, validation, capability discovery, and cost planning."""

from __future__ import annotations

import copy
import math
from typing import Any

from physics_demo.agent_contract import PROTOCOL_VERSION
from physics_demo.core.force_fields import acceleration_bound as force_acceleration_bound
from physics_demo.limits import (
    MAX_WALL_TIME_S,
    MAX_ABS_COORDINATE,
    MAX_ACCELERATION,
    MAX_COLLIDERS,
    MAX_ENTITIES,
    MAX_FRAME_PARTICLE_SAMPLES,
    MAX_FORCE_FIELDS,
    MAX_INITIAL_PARTICLE_VOLUME_OVERLAP,
    MAX_PARTICLE_SPACING,
    MAX_SCENE_DEPTH,
    MAX_SCENE_NODES,
    MAX_SHAPE_EXTENT,
    MAX_SPEED,
    MAX_WORLD_SPAN,
    MIN_PARTICLE_SPACING,
    QUALITY_LIMITS,
)
from physics_demo.core.math3d import finite_number, finite_vec
from physics_demo.core.liquids import LIQUID_PRESETS, LIQUID_PRESET_NAMES, LIQUID_PRESET_VERSION
from physics_demo.io.planning import estimate_particle_count
from physics_demo.analysis.queries import CAPABILITIES as QUERY_CAPABILITIES, validate_queries
from physics_demo.io.slider_schema import normalize_slider, validate_slider_scene
from physics_demo.io.mesh_scene import MESH_CAPABILITIES, MESH_FIELDS, MAX_MESH_SCENE_NODES, normalize_entity as normalize_mesh_entity, validate_scene as validate_mesh_scene
from physics_demo.io.connections import CAPABILITIES as CONNECTION_CAPABILITIES, validate_scene as validate_connections
from physics_demo.io.coupled import CAPABILITIES as COUPLED_CAPABILITIES, RIGID_FIELDS, enabled as coupled_enabled, normalize_rigid, validate_scene as validate_coupled


CAPABILITIES: dict[str, Any] = {
    "version": "1.0.0",
    "systems": {"types": ["pendulum", "double_pendulum"],
                "entry": "physics_system(spec_json), or build_system/run_system in Python",
                "parameters": ["type", "lengths", "masses", "angles", "angular_velocities", "gravity", "duration", "dt", "output_fps", "quality", "wall_time_s"],
                "units": "SI; angles in radians from downward vertical; link angular velocities are absolute world values",
                "observations": "Every bob records centroid x/y/z and speed at full solver macro steps; load_run exposes series and sampled states.",
                "boundary": "Factories assemble generic point masses and rods; examples are optional callers. Numerical convergence and finite-window sensitivity do not prove chaos or permanent stability."},
    "quantitative_queries": QUERY_CAPABILITIES,
    "agent_interface": {
        "protocol_version": PROTOCOL_VERSION,
        "preferred_flow": [
            "physics_capabilities",
            "obtain a complete scene_json with physics_system or physics_example, or author scene-v1",
            "after scene_json exists and its fluid entity ID is known, use physics_liquid then physics_patch for a named liquid",
            "physics_prepare",
            "physics_simulate",
            "physics_query (predeclared data)",
        ],
        "success_condition": "status=completed, ok=true, and quality_gate.passed=true",
    },
    "validation_modes": {
        "visual": "Deliver complete verified videos; precision-only failures remain warnings and invalidate numerical answers.",
        "strict": "Require all numerical checks. Use for requested measurements or accuracy; the raw API compatibility default.",
        "field": "budget.validation; the toolbox defaults to visual",
    },
    "dimension": "3D simulation rendered through one fixed auto-fit 2D projection per video",
    "engines": {
        "coupled": COUPLED_CAPABILITIES,
        "connections": CONNECTION_CAPABILITIES,
        "mesh": MESH_CAPABILITIES,
        "slider": {
            "algorithm": "event-split analytic translation on a common x rail",
            "use_for": ["slider separation time", "1D collisions", "Coulomb friction stopping"],
            "accuracy": "quantitative for the declared 1D rail model; no rotation or general rigid-body coupling",
            "limits": {"max_sliders": 16, "standalone_only": True},
        },
        "point_mass": {
            "algorithm": "velocity Verlet with Plummer softening",
            "use_for": ["three-body motion", "small N-body gravity"],
            "accuracy": "quantitative for the configured softened point-mass model",
            "limits": {"max_bodies": 64},
        },
        "particle_gravity": {
            "algorithm": "C11 Barnes-Hut octree with Plummer softening; theta=0 direct reference",
            "enable": "interactions.mutual_gravity=true on native fluid/granular scenes",
            "controls": {"gravity_G": "explicit G", "softening": "metres, >=1e-6",
                         "particle_gravity_density": "shared reference kg/m3, default 1000",
                         "gravity_theta": "0 direct; visual default 0.5; strict default 0"},
            "mass": "each particle has density * effective_spacing^3; disclose represented mass after planning",
            "limits": "no particle/point-mass exchange, mesh/rigid coupling or Python fallback; incompressible visual matter, not stellar gas or calibrated astrophysical hydro",
        },
        "fluid": {
            "algorithm": "native C11 DFSPH density/divergence projection, consistent cubic kernel, and Akinci surface tension",
            "use_for": ["drops", "pools", "liquid blobs", "flow around simple solids"],
            "accuracy": "higher-resolution incompressible visual simulation with density/divergence residual diagnostics; not calibrated engineering CFD",
            "presets": {
                name: {
                    "aliases": list(value["aliases"]),
                    "properties": dict(value["properties"]),
                    "appearance": value["appearance"],
                    "scope": value["scope"],
                    "native_required": value["native_required"],
                }
                for name, value in LIQUID_PRESETS.items()
            },
            "preset_version": LIQUID_PRESET_VERSION,
            "preset_interface": "After a complete scene_json exists, call physics_liquid(preset, entity_id) and apply its operations with physics_patch; the liquid response does not contain scene_json.",
            "preset_coefficients": "viscosity and surface_tension are stable model controls, not calibrated SI material measurements",
            "spacing_range_m": [MIN_PARTICLE_SPACING, MAX_PARTICLE_SPACING],
        },
        "granular": {
            "algorithm": "position-based particle contacts with wetness-weakened cohesion",
            "use_for": ["sand piles", "sandcastle collapse", "qualitative erosion"],
            "accuracy": "visual proxy; not a geotechnical constitutive model",
        },
        "rigid": {
            "algorithm": "semi-implicit translation plus positional contacts",
            "use_for": ["static spheres and boxes", "Python-reference dynamic spheres"],
            "accuracy": "native runs accept static rigid geometry; no rotational rigid-body solver",
        },
        "force_field": {
            "algorithm": "targeted analytic acceleration evaluated in the native C11 hot loop",
            "use_for": ["timed pushes and geysers", "radial blasts or attraction", "vortices and whirlpools"],
            "accuracy": "prescribed acceleration field; it does not resolve air, pumps, electromagnetism, or two-way field coupling",
        },
    },
    "automatic_interactions": [
        "fluid-fluid density",
        "fluid-solid contact",
        "sand-solid contact",
        "fluid-sand drag and wetting",
        "sphere reaction to particle contact",
    ],
    "force_fields": {
        "types": ["uniform", "radial", "vortex"],
        "max_fields": MAX_FORCE_FIELDS,
        "max_acceleration_m_s2": MAX_ACCELERATION,
        "target_types": ["fluid", "granular", "point_mass"],
    },
    "colliders": {
        "types": ["plane", "sphere", "box", "capsule"],
        "box_appearances": ["solid", "glass"],
        "appearance_scope": "rendering only; collision geometry is unchanged",
    },
    "quality_levels": QUALITY_LIMITS,
    "backends": ["auto", "native", "python"],
    "budget_behavior": "planning predicts p50/p90 wall time, bounds frame samples, coarsens particles when useful, and refuses plans that still cannot fit the hard deadline",
    "resource_limits": {
        "max_entities": MAX_ENTITIES,
        "max_colliders": MAX_COLLIDERS,
        "max_force_fields": MAX_FORCE_FIELDS,
        "max_initial_particle_volume_overlap": MAX_INITIAL_PARTICLE_VOLUME_OVERLAP,
        "max_frame_particle_samples": MAX_FRAME_PARTICLE_SAMPLES,
        "max_physical_duration_s": 30,
        "max_wall_time_s": MAX_WALL_TIME_S,
    },
    "unsupported": [
        "engineering-grade multiphase CFD",
        "air-resolved splash thresholds",
        "fracture or calibrated soil mechanics",
        "cloth/membrane water balloons",
        "general articulated joints",
        "continuous particle emitters or sinks",
        "arbitrary executable force expressions",
    ],
}


def _issue(code: str, path: str, message: str, suggestion: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "path": path, "message": message}
    if suggestion:
        item["suggestion"] = suggestion
    return item


def _number(value: Any, default: float) -> float:
    return float(value) if finite_number(value) else default


def _vec(value: Any, default: list[float]) -> list[float]:
    return [float(x) for x in value] if finite_vec(value) else list(default)


def _normalize_fields(value: dict[str, Any], defaults: dict[str, Any], path: str, errors: list[dict[str, Any]]) -> None:
    """Check then normalize finite scalars/vectors, preserving field order."""
    for key, default in defaults.items():
        vector = isinstance(default, list)
        valid = finite_vec if vector else finite_number
        if key in value and not valid(value[key]):
            code, message = ("vec3", "Expected exactly three finite numbers.") if vector else ("finite_number", "Expected one finite JSON number.")
            errors.append(_issue(code, f"{path}.{key}", message))
        value[key] = _vec(value.get(key), default) if vector else _number(value.get(key), default)


def _defaults(value: dict[str, Any], defaults: dict[str, tuple[Any, str]], assumptions: list[str]) -> None:
    """Keep omitted-field disclosures even when an invalid scene is returned."""
    for key, (default, note) in defaults.items():
        if key not in value:
            value[key] = default
            assumptions.append(note)


def _coordinates(value: list[float], path: str, label: str, errors: list[dict[str, Any]]) -> None:
    if any(abs(component) > MAX_ABS_COORDINATE for component in value):
        errors.append(_issue("coordinate_range", path, f"{label} components must stay within ±{MAX_ABS_COORDINATE:g} m."))


def _shape_extent(shape: dict[str, Any], path: str, errors: list[dict[str, Any]], radius: float = 0.5, size: float = 1.0) -> None:
    """Shared sphere/box dimensions for particle sources, rigid bodies and colliders."""
    if shape["type"] == "sphere":
        _normalize_fields(shape, {"radius": radius}, path, errors)
        if shape["radius"] <= 0.0:
            errors.append(_issue("radius", f"{path}.radius", "radius must be positive."))
        elif shape["radius"] > MAX_SHAPE_EXTENT:
            errors.append(_issue("shape_extent", f"{path}.radius", f"radius must be at most {MAX_SHAPE_EXTENT:g} m."))
    else:
        _normalize_fields(shape, {"size": [size] * 3}, path, errors)
        if any(x <= 0.0 for x in shape["size"]):
            errors.append(_issue("size", f"{path}.size", "All box sizes must be positive."))
        if any(x > MAX_SHAPE_EXTENT for x in shape["size"]):
            errors.append(_issue("shape_extent", f"{path}.size", f"Box sizes must be at most {MAX_SHAPE_EXTENT:g} m per axis."))


def _nonnegative(value: Any, key: str, path: str, limit: float, errors: list[dict[str, Any]]) -> None:
    number = _number(value, -1.0)
    if number < 0.0:
        errors.append(_issue(key, f"{path}.{key}", f"{key} must be non-negative."))
    elif number > limit:
        errors.append(_issue(f"{key}_range", f"{path}.{key}", f"{key} must be at most {limit:g}."))


def _particle_properties(entity: dict[str, Any], path: str, errors: list[dict[str, Any]], assumptions: list[str]) -> None:
    properties = entity.setdefault("properties", {})
    if not isinstance(properties, dict):
        errors.append(_issue("properties", f"{path}.properties", "properties must be an object."))
        properties = entity["properties"] = {}
    path += ".properties"
    defaults = (
        {
            "viscosity": (LIQUID_PRESETS[entity["preset"]]["properties"]["viscosity"], "fluid viscosity"),
            "surface_tension": (LIQUID_PRESETS[entity["preset"]]["properties"]["surface_tension"], "surface-tension coefficient"),
        }
        if entity["type"] == "fluid" else
        {"friction": (0.55, "sand friction"), "cohesion": (0.18, "sand cohesion")}
    )
    _unknown_fields(properties, set(defaults), path, errors)
    # Properties retain their supplied JSON numbers, unlike normalized geometry.
    for key in defaults:
        if key in properties and not finite_number(properties[key]):
            errors.append(_issue("finite_number", f"{path}.{key}", "Expected one finite JSON number."))
    _defaults(properties, {key: (default, f"Used {label} {default} for {entity['id']!r}.") for key, (default, label) in defaults.items()}, assumptions)
    for key in defaults:
        if key == "cohesion":
            if not 0.0 <= _number(properties[key], -1.0) <= 1.0:
                errors.append(_issue("cohesion", f"{path}.cohesion", "cohesion must be in [0, 1]."))
        else:
            _nonnegative(properties[key], key, path, 5.0 if key == "friction" else 10.0, errors)


def _check_structure(root: Any) -> dict[str, Any] | None:
    entities = root.get("entities", []) if isinstance(root, dict) else []
    has_mesh = isinstance(entities, list) and any(isinstance(e, dict) and e.get("type") == "mesh" for e in entities[:MAX_ENTITIES])
    node_limit = MAX_MESH_SCENE_NODES if has_mesh else MAX_SCENE_NODES
    stack: list[tuple[Any, int, str]] = [(root, 0, "$")]
    nodes = 0
    seen: set[int] = set()
    while stack:
        value, depth, path = stack.pop()
        nodes += 1
        if nodes > node_limit:
            return _issue("scene_too_large", path, f"Scene exceeds {node_limit} values.", "Split it into smaller bounded scenes.")
        if depth > MAX_SCENE_DEPTH:
            return _issue("scene_too_deep", path, f"Scene nesting exceeds depth {MAX_SCENE_DEPTH}.")
        if isinstance(value, (dict, list)):
            identity = id(value)
            if identity in seen:
                return _issue("cyclic_scene", path, "Scene contains a cyclic Python container and cannot be serialized as JSON.")
            seen.add(identity)
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    return _issue("object_key", path, "Every object key must be a string.")
                if any(0xD800 <= ord(character) <= 0xDFFF for character in key):
                    return _issue("unicode_scalar", path, "Object keys may not contain UTF-16 surrogate code points.")
                stack.append((child, depth + 1, f"{path}.{key}"))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                stack.append((child, depth + 1, f"{path}[{index}]"))
        elif isinstance(value, str) and any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            return _issue("unicode_scalar", path, "Strings may not contain UTF-16 surrogate code points.")
    return None


def _vector_magnitude(value: list[float]) -> float:
    return math.sqrt(sum(component * component for component in value))


def _unknown_fields(value: dict[str, Any], allowed: set[str], path: str, errors: list[dict[str, Any]]) -> None:
    for key in sorted(set(value) - allowed):
        full_path = f"{path}.{key}" if path else key
        errors.append(_issue("unknown_field", full_path, f"Unknown field {key!r}.", "Remove it or query capabilities for the supported contract."))


def _effective_scene_choices(scene: dict[str, Any]) -> list[str]:
    """Build stable disclosures from the normalized scene itself.

    These remain identical when a prepared scene is passed to simulate, unlike
    provenance notes that depend on whether a field was omitted before
    normalization.
    """
    world = scene["world"]
    budget = scene["budget"]
    interactions = scene["interactions"]
    choices = [
        (
            "Effective world choice: gravity=" + repr(world["gravity"]) +
            f" m/s², duration={world['duration']!r} s, dt={world['dt']!r} s, "
            f"output_fps={world['output_fps']!r}, bounds={world['bounds']!r}."
        ),
        (
            f"Effective execution choice: quality={budget['quality']!r}, "
            f"backend={budget['backend']!r}, wall_time_s={budget['wall_time_s']!r}."
        ),
        (
            "Effective interaction choice: "
            f"mutual_gravity={interactions['mutual_gravity']!r}, "
            f"gravity_G={interactions['gravity_G']!r}, "
            f"softening={interactions['softening']!r}, "
            f"water_sand_drag={interactions['water_sand_drag']!r}, "
            f"wetting_rate={interactions['wetting_rate']!r}."
        ),
        (
            f"Effective scene geometry: {len(scene['entities'])} entities, "
            f"{len(scene['colliders'])} colliders, and "
            f"{len(scene['force_fields'])} analytic force fields."
        ),
    ]
    particle_motion = "spacing={spacing!r} m, velocity={velocity!r} m/s, "
    rigid_motion = "mass={mass!r}, position={position!r} m, velocity={velocity!r} m/s"
    descriptions = {
        "fluid": particle_motion + "preset={preset!r}, viscosity={properties[viscosity]!r}, surface_tension={properties[surface_tension]!r}",
        "granular": particle_motion + "friction={properties[friction]!r}, cohesion={properties[cohesion]!r}",
        "point_mass": rigid_motion + ", fixed={fixed!r}",
        "rigid": rigid_motion,
        "rigid_body": rigid_motion + ", orientation(wxyz)={orientation!r}, angular_velocity(world)={angular_velocity!r} rad/s, fixed={fixed!r}",
        "mesh": "motion={motion!r}, position={position!r} m, velocity={velocity!r} m/s, mass={mass!r} kg, edge_compliance={edge_compliance!r}, bending_compliance={bending_compliance!r}, volume_compliance={volume_compliance!r}, damping={damping!r} s⁻¹, friction={friction!r}, thickness={thickness!r} m, pinned_vertices={pinned_vertices!r}",
        "slider": (
            "mass={mass!r} kg, position={position!r} m, velocity={velocity!r} m/s, size={size!r} m, "
            "acceleration={acceleration!r} m/s², friction={friction!r}, restitution={restitution!r}"
        ),
    }
    for entity in scene["entities"]:
        kind = entity["type"]
        detail = descriptions[kind].format_map(entity)
        choices.append(f"Effective {kind} choice for entity {entity['id']!r}: {detail}.")
        if kind == "fluid":
            scope = LIQUID_PRESETS[entity["preset"]]["scope"]
            choices.append(
                f"Liquid preset model boundary for entity {entity['id']!r}: {scope}"
            )
    glass_colliders = [
        collider["id"] for collider in scene["colliders"]
        if collider.get("type") == "box" and collider.get("appearance") == "glass"
    ]
    if glass_colliders:
        choices.append(
            "Effective render choice: glass box colliders=" + repr(glass_colliders) +
            "; appearance does not alter collision physics."
        )
    return choices


def normalize_and_validate(raw: Any) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    assumptions: list[str] = []
    structure_issue = _check_structure(raw) if isinstance(raw, dict) else _issue("scene_type", "$", "Scene must be a JSON object.")
    if structure_issue:
        return {
            "valid": False,
            "errors": [structure_issue],
            "warnings": [],
            "assumptions": [],
            "scene": None,
        }

    scene = copy.deepcopy(raw)
    _unknown_fields(scene, {"version", "name", "world", "budget", "entities", "colliders", "interactions", "force_fields", "queries", "mesh_settings", "connections", "connection_settings", "coupling"}, "", errors)
    _defaults(scene, {
        "version": (1, "Used scene schema version 1."),
        "name": ("agent-physics-scene", "Used the generated scene name 'agent-physics-scene'."),
    }, assumptions)
    if not isinstance(scene["version"], int) or isinstance(scene["version"], bool) or scene["version"] != 1:
        errors.append(_issue("version", "version", "Only scene version 1 is supported."))
    if not isinstance(scene["name"], str) or not scene["name"].strip() or len(scene["name"]) > 128:
        errors.append(_issue("name", "name", "name must be a non-empty string of at most 128 characters."))
    world = scene.setdefault("world", {})
    if not isinstance(world, dict):
        errors.append(_issue("world_type", "world", "world must be an object."))
        world = scene["world"] = {}
    _unknown_fields(world, {"gravity", "duration", "dt", "output_fps", "bounds"}, "world", errors)
    _defaults(world, {
        "gravity": ([0.0, -9.81, 0.0], "Used Earth gravity [0, -9.81, 0] m/s²."),
        "duration": (2.0, "Used a 2 second physical duration."),
        "dt": (1.0 / 90.0, "Used a 1/90 second fixed timestep."),
        "output_fps": (24.0, "Used 24 video frames per second."),
        "bounds": ({"min": [-3.0, 0.0, -3.0], "max": [3.0, 5.0, 3.0]}, "Used default world bounds [-3,0,-3] to [3,5,3] metres."),
    }, assumptions)
    _normalize_fields(world, {"gravity": [0.0, -9.81, 0.0], "duration": 2.0, "dt": 1.0 / 90.0, "output_fps": 24.0}, "world", errors)
    bounds = world["bounds"]
    if not isinstance(bounds, dict):
        errors.append(_issue("bounds_type", "world.bounds", "bounds must be an object with min and max vectors."))
        bounds = world["bounds"] = {"min": [-3.0, 0.0, -3.0], "max": [3.0, 5.0, 3.0]}
    _unknown_fields(bounds, {"min", "max"}, "world.bounds", errors)
    _normalize_fields(bounds, {"min": [-3.0, 0.0, -3.0], "max": [3.0, 5.0, 3.0]}, "world.bounds", errors)
    if not (0.0 < world["duration"] <= 30.0):
        errors.append(_issue("duration_range", "world.duration", "duration must be in (0, 30] seconds.", "Use 2.0 for a short demo."))
    if not (1e-4 <= world["dt"] <= 0.05):
        errors.append(_issue("dt_range", "world.dt", "dt must be between 0.0001 and 0.05 seconds.", "Use 0.011111 for particle scenes."))
    if not (1.0 <= world["output_fps"] <= 60.0):
        errors.append(_issue("fps_range", "world.output_fps", "output_fps must be in [1, 60]."))
    elif not world["output_fps"].is_integer():
        errors.append(_issue("fps_integer", "world.output_fps", "output_fps must be an integer value from 1 to 60."))
    if any(bounds["min"][axis] >= bounds["max"][axis] for axis in range(3)):
        errors.append(_issue("bounds_order", "world.bounds", "Every bounds.min component must be below bounds.max."))
    if any(abs(value) > MAX_ABS_COORDINATE for value in bounds["min"] + bounds["max"]):
        errors.append(_issue("coordinate_range", "world.bounds", f"Bounds coordinates must stay within ±{MAX_ABS_COORDINATE:g} m."))
    if any(bounds["max"][axis] - bounds["min"][axis] > MAX_WORLD_SPAN for axis in range(3)):
        errors.append(_issue("world_span", "world.bounds", f"Each world span must be at most {MAX_WORLD_SPAN:g} m."))
    if _vector_magnitude(world["gravity"]) > MAX_ACCELERATION:
        errors.append(_issue("gravity_range", "world.gravity", f"Gravity magnitude must be at most {MAX_ACCELERATION:g} m/s²."))

    budget = scene.setdefault("budget", {})
    if not isinstance(budget, dict):
        errors.append(_issue("budget_type", "budget", "budget must be an object."))
        budget = scene["budget"] = {}
    _unknown_fields(budget, {"wall_time_s", "quality", "backend", "validation"}, "budget", errors)
    _defaults(budget, {
        "wall_time_s": (55.0, "Used a 55 second wall-clock budget."),
        "quality": ("preview", "Used preview quality."),
        "backend": ("auto", "Used the native C11 backend when available, with the Python solver as a fallback."),
    }, assumptions)
    _normalize_fields(budget, {"wall_time_s": 55.0}, "budget", errors)
    if not isinstance(budget["quality"], str) or budget["quality"] not in QUALITY_LIMITS:
        errors.append(_issue("quality", "budget.quality", "quality must be preview, balanced, or high."))
    if not isinstance(budget["backend"], str) or budget["backend"] not in {"auto", "native", "python"}:
        errors.append(_issue("backend", "budget.backend", "backend must be auto, native, or python."))
    if "validation" in budget and budget["validation"] not in ("visual", "strict"):
        errors.append(_issue("validation_mode", "budget.validation", "validation must be visual or strict."))
    if not (1.0 <= budget["wall_time_s"] <= MAX_WALL_TIME_S):
        errors.append(_issue("budget_range", "budget.wall_time_s", "wall_time_s must be in [1, 300]."))

    entities = scene.setdefault("entities", [])
    if not isinstance(entities, list) or not entities:
        errors.append(_issue("entities", "entities", "At least one entity is required."))
        entities = scene["entities"] = []
    elif len(entities) > MAX_ENTITIES:
        errors.append(_issue("entity_limit", "entities", f"At most {MAX_ENTITIES} entities are supported.", "Merge compatible particle volumes or split the request into multiple videos."))
    seen: set[str] = set()
    entity_kinds: dict[str, str] = {}
    point_masses = 0
    for index, entity in enumerate(entities):
        path = f"entities[{index}]"
        if not isinstance(entity, dict):
            errors.append(_issue("entity_type", path, "Each entity must be an object."))
            continue
        kind = entity.get("type")
        if not isinstance(kind, str) or kind not in {"point_mass", "fluid", "granular", "rigid", "rigid_body", "slider", "mesh"}:
            errors.append(_issue("entity_kind", f"{path}.type", "type must be point_mass, fluid, granular, rigid, slider, or mesh."))
            continue
        entity_allowed = {
            "point_mass": {"id", "type", "mass", "position", "velocity", "fixed", "collision_radius"},
            "fluid": {"id", "type", "shape", "spacing", "velocity", "properties", "preset"},
            "granular": {"id", "type", "shape", "spacing", "velocity", "properties"},
            "rigid": {"id", "type", "shape", "position", "velocity", "mass"},
            "slider": {"id", "type", "position", "velocity", "size", "mass", "acceleration", "friction", "restitution"},
            "mesh": MESH_FIELDS,
            "rigid_body": RIGID_FIELDS,
        }[kind]
        _unknown_fields(entity, entity_allowed, path, errors)
        missing_velocity = "velocity" not in entity
        _normalize_fields(entity, {"velocity": [0.0, 0.0, 0.0]}, path, errors)
        if "id" not in entity:
            entity["id"] = f"{kind}_{index}"
            assumptions.append(f"Generated entity id {entity['id']!r}.")
        if not isinstance(entity["id"], str) or not entity["id"].strip() or len(entity["id"]) > 128:
            errors.append(_issue("entity_id", f"{path}.id", "id must be a non-empty string of at most 128 characters."))
            entity["id"] = f"invalid_{index}"
        if entity["id"] in seen:
            errors.append(_issue("duplicate_id", f"{path}.id", f"Duplicate id {entity['id']!r}."))
        seen.add(entity["id"])
        entity_kinds[entity["id"]] = kind
        if missing_velocity:
            assumptions.append(f"Used zero initial velocity for {entity['id']!r}.")
        if _vector_magnitude(entity["velocity"]) > MAX_SPEED:
            errors.append(_issue("velocity_range", f"{path}.velocity", f"Initial speed must be at most {MAX_SPEED:g} m/s."))
        if kind == "rigid_body":
            normalize_rigid(entity, path, errors)
        elif kind == "mesh":
            normalize_mesh_entity(entity, path, errors)
        elif kind == "slider":
            normalize_slider(entity, path, errors)
        elif kind == "point_mass":
            point_masses += 1
            _normalize_fields(entity, {"position": [0.0, 0.0, 0.0], "mass": 1.0}, path, errors)
            if "fixed" in entity and not isinstance(entity["fixed"], bool):
                errors.append(_issue("boolean", f"{path}.fixed", "fixed must be a JSON boolean."))
            entity["fixed"] = entity.get("fixed", False) if isinstance(entity.get("fixed", False), bool) else False
            if entity["mass"] <= 0.0:
                errors.append(_issue("mass", f"{path}.mass", "point_mass mass must be positive."))
            elif entity["mass"] > 1.0e12:
                errors.append(_issue("mass_range", f"{path}.mass", "point_mass mass must be at most 1e12 model units."))
            _coordinates(entity["position"], f"{path}.position", "Position", errors)
        elif kind in {"fluid", "granular"}:
            if kind == "fluid":
                if "preset" not in entity:
                    entity["preset"] = "water"
                    assumptions.append(f"Used liquid preset 'water' for {entity['id']!r}.")
                if not isinstance(entity["preset"], str) or entity["preset"] not in LIQUID_PRESET_NAMES:
                    errors.append(_issue(
                        "liquid_preset", f"{path}.preset",
                        f"preset must be one of: {', '.join(LIQUID_PRESET_NAMES)}.",
                        "Call physics_liquid with the desired canonical preset.",
                    ))
                    entity["preset"] = "water"
            shape = entity.setdefault("shape", {})
            if not isinstance(shape, dict) or not isinstance(shape.get("type"), str) or shape.get("type") not in {"sphere", "box"}:
                errors.append(_issue("volume_shape", f"{path}.shape", "Particle volumes require a sphere or box shape."))
                continue
            shape_fields = {"type", "center", "radius"} if shape["type"] == "sphere" else {"type", "center", "size"}
            _unknown_fields(shape, shape_fields, f"{path}.shape", errors)
            _normalize_fields(shape, {"center": [0.0, 1.0, 0.0]}, f"{path}.shape", errors)
            _coordinates(shape["center"], f"{path}.shape.center", "Center", errors)
            _shape_extent(shape, f"{path}.shape", errors, radius=0.3, size=0.5)
            _normalize_fields(entity, {"spacing": 0.12}, path, errors)
            if not (MIN_PARTICLE_SPACING <= entity["spacing"] <= MAX_PARTICLE_SPACING):
                errors.append(_issue(
                    "spacing",
                    f"{path}.spacing",
                    f"spacing must be in [{MIN_PARTICLE_SPACING:g}, {MAX_PARTICLE_SPACING:g}] m.",
                ))
            _particle_properties(entity, path, errors, assumptions)
        else:
            _normalize_fields(entity, {"position": [0.0, 1.0, 0.0], "mass": 0.0}, path, errors)
            if entity["mass"] < 0.0:
                errors.append(_issue("mass", f"{path}.mass", "rigid mass must be non-negative; use zero for static."))
            elif entity["mass"] > 1.0e12:
                errors.append(_issue("mass_range", f"{path}.mass", "rigid mass must be at most 1e12 model units."))
            _coordinates(entity["position"], f"{path}.position", "Position", errors)
            shape = entity.setdefault("shape", {"type": "sphere", "radius": 0.5})
            if not isinstance(shape, dict) or not isinstance(shape.get("type"), str) or shape.get("type") not in {"sphere", "box"}:
                errors.append(_issue("rigid_shape", f"{path}.shape", "Rigid shapes must be sphere or box."))
            else:
                dimension = "radius" if shape["type"] == "sphere" else "size"
                _unknown_fields(shape, {"type", dimension}, f"{path}.shape", errors)
                _shape_extent(shape, f"{path}.shape", errors)
                if shape["type"] == "box" and entity["mass"] > 0:
                    errors.append(_issue(
                        "unsupported_dynamic_box",
                        f"{path}.mass",
                        "Dynamic rigid boxes are unsupported because rotational box dynamics are unavailable.",
                        "Use mass 0 for a static box, or use a dynamic sphere on the Python reference backend.",
                    ))

    if point_masses > 64:
        errors.append(_issue("body_limit", "entities", "At most 64 point masses are supported."))
    native_only_preset = any(
        LIQUID_PRESETS[entity.get("preset", "water")]["native_required"]
        if isinstance(entity, dict) and entity.get("type") == "fluid"
        else False
        for entity in entities
    )
    dynamic_spheres = [
        entity["id"]
        for entity in entities
        if isinstance(entity, dict)
        and entity.get("type") == "rigid"
        and isinstance(entity.get("shape"), dict)
        and entity["shape"].get("type") == "sphere"
        and finite_number(entity.get("mass"))
        and entity.get("mass", 0.0) > 0.0
    ]
    if native_only_preset and dynamic_spheres:
        errors.append(_issue(
            "unsupported_liquid_dynamic_rigid_combination",
            "entities",
            "Honey, glue, and molten_lead require native liquid execution, while legacy dynamic rigid spheres require the Python reference backend.",
            "Make the legacy rigid sphere a static obstacle with mass 0, or remodel it as rigid_body and enable coupling.",
        ))
    elif native_only_preset and budget.get("backend") == "python":
        errors.append(_issue(
            "unsupported_python_liquid_preset", "budget.backend",
            "Honey, glue, and molten_lead presets require the native C11 liquid solver.",
            "Use backend 'auto' or 'native'; the Python reference has different viscosity behavior and no equivalent surface-tension model.",
        ))

    particle_volumes: list[tuple[list[float], list[float], list[float]]] = []
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("type") not in ("fluid", "granular"):
            continue
        shape = entity.get("shape")
        if not isinstance(shape, dict) or shape.get("type") not in ("sphere", "box"):
            continue
        center = shape.get("center")
        sphere = shape["type"] == "sphere"
        dimensions = [shape.get("radius")] * 3 if sphere else shape.get("size")
        if not finite_vec(center) or not finite_vec(dimensions) or any(value <= 0 for value in dimensions):
            continue
        half = [float(value) * (1.0 if sphere else 0.5) for value in dimensions]
        center_values = [float(value) for value in center]
        particle_volumes.append((
            center_values,
            [center_values[axis] - half[axis] for axis in range(3)],
            [center_values[axis] + half[axis] for axis in range(3)],
        ))
    maximum_initial_overlap = 0
    for center, _, _ in particle_volumes:
        overlap = sum(
            all(lower[axis] < center[axis] < upper[axis] for axis in range(3))
            for _, lower, upper in particle_volumes
        )
        maximum_initial_overlap = max(maximum_initial_overlap, overlap)
    if maximum_initial_overlap > MAX_INITIAL_PARTICLE_VOLUME_OVERLAP:
        errors.append(_issue(
            "initial_particle_overlap_limit",
            "entities",
            f"At most {MAX_INITIAL_PARTICLE_VOLUME_OVERLAP} particle volumes may overlap at one volume centre; found {maximum_initial_overlap}.",
            "Merge coincident sources into one volume or separate their initial positions before simulation.",
        ))

    colliders = scene.setdefault("colliders", [])
    if not isinstance(colliders, list):
        errors.append(_issue("colliders", "colliders", "colliders must be an array."))
        colliders = scene["colliders"] = []
    elif len(colliders) > MAX_COLLIDERS:
        errors.append(_issue("collider_limit", "colliders", f"At most {MAX_COLLIDERS} colliders are supported."))
    for index, collider in enumerate(colliders):
        path = f"colliders[{index}]"
        if not isinstance(collider, dict) or not isinstance(collider.get("type"), str) or collider.get("type") not in {"plane", "sphere", "box", "capsule"}:
            errors.append(_issue("collider", path, "Collider type must be plane, sphere, box, or capsule."))
            continue
        allowed = {
            "plane": {"id", "type", "normal", "offset", "friction", "water_adhesion"},
            "sphere": {"id", "type", "center", "radius", "friction"},
            "box": {"id", "type", "center", "size", "friction", "appearance"},
            "capsule": {"id", "type", "a", "b", "radius", "friction"},
        }[collider["type"]]
        _unknown_fields(collider, allowed, path, errors)
        collider.setdefault("id", f"collider_{index}")
        if not isinstance(collider["id"], str) or not collider["id"].strip() or len(collider["id"]) > 128:
            errors.append(_issue("collider_id", f"{path}.id", "id must be a non-empty string of at most 128 characters."))
        if collider["type"] == "plane":
            _normalize_fields(collider, {"normal": [0.0, 1.0, 0.0], "offset": 0.0}, path, errors)
            if _vector_magnitude(collider["normal"]) <= 1.0e-12:
                errors.append(_issue("normal", f"{path}.normal", "Plane normal must be non-zero."))
            if abs(collider["offset"]) > MAX_ABS_COORDINATE:
                errors.append(_issue("coordinate_range", f"{path}.offset", f"Plane offset must stay within ±{MAX_ABS_COORDINATE:g} m."))
            if "water_adhesion" in collider:
                if not finite_number(collider["water_adhesion"]):
                    errors.append(_issue(
                        "finite_number", f"{path}.water_adhesion",
                        "Expected one finite JSON number.",
                    ))
                    collider["water_adhesion"] = 0.0
                else:
                    collider["water_adhesion"] = float(collider["water_adhesion"])
                    _nonnegative(
                        collider["water_adhesion"], "water_adhesion", path,
                        MAX_ACCELERATION, errors,
                    )
        elif collider["type"] in {"sphere", "box"}:
            _normalize_fields(collider, {"center": [0.0, 0.0, 0.0]}, path, errors)
            _shape_extent(collider, path, errors)
            if collider["type"] == "box":
                appearance = collider.get("appearance", "solid")
                if not isinstance(appearance, str) or appearance not in {"solid", "glass"}:
                    errors.append(_issue(
                        "collider_appearance",
                        f"{path}.appearance",
                        "Box appearance must be solid or glass.",
                    ))
                    appearance = "solid"
                collider["appearance"] = appearance
        else:
            _normalize_fields(collider, {"a": [0.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0], "radius": 0.25}, path, errors)
            if collider["a"] == collider["b"]:
                errors.append(_issue("capsule_segment", path, "Capsule endpoints a and b must differ."))
            if collider["radius"] <= 0.0 or collider["radius"] > MAX_SHAPE_EXTENT:
                errors.append(_issue("radius", f"{path}.radius", f"Capsule radius must be in (0, {MAX_SHAPE_EXTENT:g}]."))
        coordinate_values = []
        for key in ("center", "a", "b"):
            coordinate_values.extend(collider.get(key, []))
        if any(abs(value) > MAX_ABS_COORDINATE for value in coordinate_values):
            errors.append(_issue("coordinate_range", path, f"Collider coordinates must stay within ±{MAX_ABS_COORDINATE:g} m."))
        _normalize_fields(collider, {"friction": 0.2}, path, errors)
        _nonnegative(collider["friction"], "friction", path, 5.0, errors)

    combined_plane_adhesion = sum(
        collider.get("water_adhesion", 0.0)
        for collider in colliders
        if isinstance(collider, dict)
        and collider.get("type") == "plane"
        and finite_number(collider.get("water_adhesion", 0.0))
        and collider.get("water_adhesion", 0.0) > 0.0
    )
    if combined_plane_adhesion > MAX_ACCELERATION:
        errors.append(_issue(
            "combined_water_adhesion", "colliders",
            f"Plane water_adhesion controls can sum to {combined_plane_adhesion:.3g} m/s²; "
            f"the limit is {MAX_ACCELERATION:g} m/s².",
        ))

    force_fields = scene.setdefault("force_fields", [])
    if not isinstance(force_fields, list):
        errors.append(_issue("force_fields", "force_fields", "force_fields must be an array."))
        force_fields = scene["force_fields"] = []
    elif len(force_fields) > MAX_FORCE_FIELDS:
        errors.append(_issue("force_field_limit", "force_fields", f"At most {MAX_FORCE_FIELDS} force fields are supported."))
    field_ids: set[str] = set()
    target_acceleration_bounds: dict[str, float] = {}
    for index, field in enumerate(force_fields):
        path = f"force_fields[{index}]"
        if not isinstance(field, dict) or not isinstance(field.get("type"), str) or field.get("type") not in {"uniform", "radial", "vortex"}:
            errors.append(_issue("force_field", path, "Force-field type must be uniform, radial, or vortex."))
            continue
        kind = field["type"]
        allowed = {
            "uniform": {"id", "type", "targets", "acceleration", "start_time", "end_time"},
            "radial": {"id", "type", "targets", "center", "strength", "radius", "start_time", "end_time"},
            "vortex": {"id", "type", "targets", "center", "axis", "strength", "radius", "inward_strength", "start_time", "end_time"},
        }[kind]
        _unknown_fields(field, allowed, path, errors)
        field.setdefault("id", f"field_{index}")
        if not isinstance(field["id"], str) or not field["id"].strip() or len(field["id"]) > 128:
            errors.append(_issue("force_field_id", f"{path}.id", "id must be a non-empty string of at most 128 characters."))
            field["id"] = f"invalid_field_{index}"
        if field["id"] in field_ids:
            errors.append(_issue("duplicate_field_id", f"{path}.id", f"Duplicate force-field id {field['id']!r}."))
        field_ids.add(field["id"])
        targets = field.get("targets")
        if not isinstance(targets, list) or not targets:
            errors.append(_issue("force_targets", f"{path}.targets", "targets must be a non-empty array of entity IDs."))
            targets = field["targets"] = []
        elif len(targets) > MAX_ENTITIES:
            errors.append(_issue("force_targets", f"{path}.targets", f"A field may target at most {MAX_ENTITIES} entities."))
        valid_targets: list[str] = []
        for target_index, target in enumerate(targets):
            target_path = f"{path}.targets[{target_index}]"
            if not isinstance(target, str) or not target:
                errors.append(_issue("force_target", target_path, "Target must be a non-empty entity ID string."))
                continue
            target_kind = entity_kinds.get(target)
            if target_kind is None:
                errors.append(_issue("unknown_force_target", target_path, f"No entity has id {target!r}."))
                continue
            if target_kind == "rigid":
                errors.append(_issue("unsupported_force_target", target_path, "Force fields cannot target rigid entities in the native backend."))
                continue
            if target in valid_targets:
                errors.append(_issue("duplicate_force_target", target_path, f"Target {target!r} appears more than once."))
                continue
            valid_targets.append(target)
        field["targets"] = valid_targets
        _normalize_fields(field, {"start_time": 0.0, "end_time": world["duration"]}, path, errors)
        if not (0.0 <= field["start_time"] < field["end_time"] <= world["duration"]):
            errors.append(_issue("force_time_window", path, "Force-field time window must satisfy 0 <= start_time < end_time <= world.duration."))
        field_acceleration_bound = 0.0
        if kind == "uniform":
            _normalize_fields(field, {"acceleration": [0.0, 0.0, 0.0]}, path, errors)
            field_acceleration_bound = _vector_magnitude(field["acceleration"])
            if field_acceleration_bound > MAX_ACCELERATION:
                errors.append(_issue("force_acceleration", f"{path}.acceleration", f"Acceleration magnitude must be at most {MAX_ACCELERATION:g} m/s²."))
        else:
            _normalize_fields(field, {"center": [0.0, 0.0, 0.0], "strength": 0.0, "radius": 1.0}, path, errors)
            _coordinates(field["center"], f"{path}.center", "Center", errors)
            if not (0.0 < field["radius"] <= MAX_SHAPE_EXTENT):
                errors.append(_issue("force_radius", f"{path}.radius", f"radius must be in (0, {MAX_SHAPE_EXTENT:g}] m."))
            field_acceleration_bound = abs(field["strength"])
            if abs(field["strength"]) > MAX_ACCELERATION:
                errors.append(_issue("force_acceleration", f"{path}.strength", f"Absolute strength must be at most {MAX_ACCELERATION:g} m/s²."))
            if kind == "vortex":
                _normalize_fields(field, {"axis": [0.0, 1.0, 0.0], "inward_strength": 0.0}, path, errors)
                if _vector_magnitude(field["axis"]) <= 1.0e-12:
                    errors.append(_issue("force_axis", f"{path}.axis", "Vortex axis must be non-zero."))
                if not (0.0 <= field["inward_strength"] <= MAX_ACCELERATION):
                    errors.append(_issue("force_acceleration", f"{path}.inward_strength", f"inward_strength must be in [0, {MAX_ACCELERATION:g}] m/s²."))
                field_acceleration_bound = force_acceleration_bound(field)
        for target in valid_targets:
            target_acceleration_bounds[target] = target_acceleration_bounds.get(target, 0.0) + field_acceleration_bound
    for target, bound in target_acceleration_bounds.items():
        if bound > MAX_ACCELERATION:
            errors.append(_issue("combined_force_acceleration", "force_fields", f"Fields targeting {target!r} can sum to {bound:.3g} m/s²; the limit is {MAX_ACCELERATION:g} m/s²."))

    interactions = scene.setdefault("interactions", {})
    if not isinstance(interactions, dict):
        errors.append(_issue("interactions_type", "interactions", "interactions must be an object."))
        interactions = scene["interactions"] = {}
    _unknown_fields(interactions, {"mutual_gravity", "gravity_G", "softening", "water_sand_drag", "wetting_rate", "particle_gravity_density", "gravity_theta"}, "interactions", errors)
    defaults = {
        "mutual_gravity": point_masses >= 2,
        "gravity_G": 1.0,
        "particle_gravity_density": 1000.0,
        "gravity_theta": 0.0 if budget.get("validation", "strict") == "strict" else 0.5,
        "softening": 0.02,
        "water_sand_drag": 0.16,
        "wetting_rate": 1.8,
    }
    for key, value in defaults.items():
        interactions.setdefault(key, value)
    if not isinstance(interactions["mutual_gravity"], bool):
        errors.append(_issue("boolean", "interactions.mutual_gravity", "mutual_gravity must be a JSON boolean."))
    interactions["mutual_gravity"] = interactions["mutual_gravity"] if isinstance(interactions["mutual_gravity"], bool) else defaults["mutual_gravity"]
    _normalize_fields(interactions, {key: value for key, value in defaults.items() if key != "mutual_gravity"}, "interactions", errors)
    for key in ("gravity_G", "softening", "water_sand_drag", "wetting_rate"):
        if interactions[key] < 0.0:
            errors.append(_issue("interaction_range", f"interactions.{key}", f"{key} must be non-negative."))
    if interactions["gravity_G"] > 1.0e6:
        errors.append(_issue("interaction_range", "interactions.gravity_G", "gravity_G must be at most 1e6."))
    if interactions["softening"] > 100.0:
        errors.append(_issue("interaction_range", "interactions.softening", "softening must be at most 100 m."))
    if interactions["water_sand_drag"] > 10.0:
        errors.append(_issue("interaction_range", "interactions.water_sand_drag", "water_sand_drag must be at most 10."))
    if interactions["wetting_rate"] > 100.0:
        errors.append(_issue("interaction_range", "interactions.wetting_rate", "wetting_rate must be at most 100 s⁻¹."))
    if interactions["mutual_gravity"] and point_masses >= 2 and interactions["softening"] < 1.0e-6:
        errors.append(_issue("gravity_singularity", "interactions.softening", "Mutual gravity with multiple bodies requires softening of at least 1e-6 m."))

    requested = estimate_particle_count(scene)
    if not 0 < interactions["particle_gravity_density"] <= 30000:
        errors.append(_issue("gravity_density", "interactions.particle_gravity_density", "Use a finite reference density in (0, 30000] kg/m3."))
    if not 0 <= interactions["gravity_theta"] <= 0.7:
        errors.append(_issue("gravity_theta", "interactions.gravity_theta", "Use 0 for direct gravity, or a tree opening angle in (0, 0.7]."))
    if interactions["mutual_gravity"] and requested:
        if interactions["softening"] < 1e-6:
            errors.append(_issue("gravity_singularity", "interactions.softening", "Particle self-gravity requires explicit Plummer softening of at least 1e-6 m."))
        if point_masses or dynamic_spheres or budget["backend"] == "python":
            errors.append(_issue("particle_gravity_route", "interactions.mutual_gravity", "Particle self-gravity requires native/auto, fluid/granular matter and static colliders. Point-mass/particle gravitational exchange and dynamic rigid spheres are not implemented."))
        warnings.append(_issue("particle_gravity_model", "interactions", "Self-gravity acts on every particle with mass density*effective_spacing^3. One reference density is shared by all materials; liquid presets do not change it. Tree forces are approximate; theta=0 evaluates direct pairs."))
    quality = budget["quality"] if isinstance(budget["quality"], str) and budget["quality"] in QUALITY_LIMITS else "preview"
    limit = QUALITY_LIMITS[quality]["particles"]
    if requested > limit:
        warnings.append(_issue(
            "particle_budget",
            "entities",
            f"Requested approximately {requested} particles; the {quality} budget caps this at {limit} by increasing spacing.",
        ))
    if point_masses and requested and not interactions["mutual_gravity"] and not coupled_enabled(scene):
        warnings.append(_issue("decoupled_gravity", "entities", "Point-mass gravity and particle matter share the scene but are not mutually coupled in this demo."))
    if dynamic_spheres and not native_only_preset:
        if budget["backend"] == "native":
            errors.append(_issue(
                "unsupported_native_dynamic_rigid",
                "budget.backend",
                "Dynamic rigid spheres currently require the Python reference backend.",
                "Use backend 'auto' or 'python', or make the rigid sphere static with mass 0.",
            ))
        else:
            warnings.append(_issue(
                "python_dynamic_rigid",
                "entities",
                f"Dynamic rigid spheres {dynamic_spheres!r} select the slower Python reference backend.",
            ))

    if not errors:
        validate_mesh_scene(scene, errors, allow_coupling=coupled_enabled(scene))
        errors.extend(validate_slider_scene(scene))
        if coupled_enabled(scene):
            validate_coupled(scene, errors)
        else:
            validate_connections(scene, errors)
            if any("collision_radius" in e for e in entities):
                errors.append(_issue("coupling_required", "coupling", "Point collision radii require a coupled scene."))
    if not errors:
        errors.extend(validate_queries(scene))
    if not errors:
        assumptions = _effective_scene_choices(scene)
        if scene.get("queries"):
            assumptions.append("Predeclared quantitative queries: " + repr(scene["queries"]) + "; sampled at every full solver macro step, independent of video output.")
        if any(e["type"] == "slider" for e in entities):
            assumptions.append("Slider model: common x rail, y/z fixed, no rotation; world bounds are framing only; Coulomb rail friction and prescribed x acceleration.")
        if coupled_enabled(scene):
            assumptions.append("Coupled model: shared-clock DFSPH/XPBD and finite-mass two-way contact reactions, not full dynamic boundary-pressure FSI or calibrated buoyancy. Solid connections are straight collision capsules; optional mass is split between point endpoints. Point masses still use explicit fields, while particles, meshes and rigid_body receive world gravity. Point/link contact has no resolved spin friction. Effective coupling settings: " + repr(scene["coupling"]))
            assumptions.append("Effective attachments and collision geometry: " + repr(scene["connections"]))
            for e in entities:
                if e["type"]=="rigid_body":
                    assumptions.append("Rigid body geometry/pivot: " + repr({k:e[k] for k in ("id","shape","pivot") if k in e}))
        elif scene.get("connections"):
            assumptions.append("Connection model: ideal massless Hooke springs with axial dashpots, fixed-length rods and inelastic tension-only ropes between point masses. No collision, bending, fracture or water/mesh coupling. World gravity and bounds do not act on these points; only explicit force_fields provide external acceleration.")
            assumptions.append("Effective connections: " + repr(scene["connections"]) + "; requested solver settings: " + repr(scene["connection_settings"]))
        if any(e["type"] == "mesh" for e in entities):
            assumptions.append("Mesh model: elastic triangle surface with stretch/bending and closed global volume constraints; no calibrated solid stress, self-collision, cutting or general edge-edge CCD. Effective solver settings: " + repr(scene["mesh_settings"]))
            for e in (e for e in entities if e["type"] == "mesh"):
                assumptions.append(f"Mesh provenance for {e['id']!r}: " + repr(e["mesh"]["metadata"].get("provenance")) + "; unseen image depth is a modeling assumption, not recovered geometry.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "assumptions": assumptions,
        "scene": scene,
    }
