"""Bounded geometry admission for simulation candidates, without mesh repair.

Distances are exact point-to-triangle queries at a deterministic finite set of
points. Their maximum is a sampled estimate, never a certified Hausdorff bound.
Acceptance covers rest geometry and these explicit tolerances, not thin feature
preservation, material calibration, self-collision, or scene readiness.
"""
from __future__ import annotations

import heapq
import importlib
import math
import os
from pathlib import Path
import sys
from types import MappingProxyType

from .contracts import FlowError, digest, file_digest, MAX_SIMULATION_FACES, MAX_SIMULATION_VERTICES
from .meshes import audit_mesh, validate_mesh
from .surface_cleanup import _point_triangle_distance2


POLICY = MappingProxyType({
    "id": "bounded-simulation-candidate/1",
    "samples_per_direction": 32,
    "max_sampled_distance_source_extent_ratio": 0.01,
    "max_relative_volume_change": 0.02,
    "max_bbox_extent_change_source_extent_ratio": 0.01,
    "max_bbox_center_change_source_extent_ratio": 0.01,
    "declared_extent_relative_tolerance": 0.000001,
    "surface_xpbd_min_volume_m3_exclusive": 1e-15,
    "max_source_vertices": 150_000,
    "max_source_faces": 300_000,
    "max_candidate_vertices": MAX_SIMULATION_VERTICES,
    "max_candidate_faces": MAX_SIMULATION_FACES,
    "max_bvh_build_nodes": 100_000,
    "max_bvh_node_visits": 250_000,
    "max_point_triangle_checks": 100_000,
    "bvh_leaf_faces": 8,
    "thin_structure": "not_verified",
    "aperture": "not_verified",
    "source_self_intersections": "not_verified",
    "distance_certification": "sampled_estimate; not_certified_Hausdorff",
})
PURPOSES = ("surface_xpbd", "liquid_volume")


class _BudgetExceeded(Exception):
    pass


class _EngineUnavailable(Exception):
    pass


class _Budget:
    def __init__(self, policy):
        self.policy = policy
        self.counts = {"bvh_build_nodes": 0, "bvh_node_visits": 0, "point_triangle_checks": 0}

    def use(self, name):
        if self.counts[name] >= self.policy["max_" + name]:
            raise _BudgetExceeded("The " + name + " limit was reached; use a smaller admitted source or a separate explicit policy.")
        self.counts[name] += 1


def _engine_module():
    """Use the verified host pin when present; never select an engine from input."""
    trusted = os.environ.get("PHYSICS_PIPELINE_PHYSICS_ROOT")
    expected = None
    if trusted is not None:
        root = Path(trusted)
        if not root.is_absolute():
            raise _EngineUnavailable("The host physics root must be absolute.")
        expected = root.resolve() / "physics_release" / "physics_demo"
        if not (expected / "core" / "meshes.py").is_file():
            raise _EngineUnavailable("The pinned physics geometry module is unavailable.")
        loaded = sys.modules.get("physics_demo")
        if loaded is not None:
            try:
                matched = Path(loaded.__file__).resolve().parent == expected
            except (AttributeError, TypeError, ValueError):
                matched = False
            if not matched:
                raise _EngineUnavailable("This process already loaded a different physics package; use the pinned isolated worker.")
        release = str(expected.parent)
        if release not in sys.path:
            sys.path.insert(0, release)
    try:
        module = importlib.import_module("physics_demo.core.meshes")
        path = Path(module.__file__).resolve()
        if expected is not None and not path.is_relative_to(expected):
            raise _EngineUnavailable("The loaded geometry auditor is outside the host physics pin.")
        if not path.is_file() or not callable(module.audit_mesh):
            raise _EngineUnavailable("The physics geometry auditor has no verifiable implementation.")
        return module, file_digest(path)
    except (ImportError, OSError, AttributeError, TypeError, ValueError) as error:
        raise _EngineUnavailable("A verifiable physics geometry auditor is required: " + str(error)) from error


def _spread10(value):
    value = (value | (value << 16)) & 0x030000FF
    value = (value | (value << 8)) & 0x0300F00F
    value = (value | (value << 4)) & 0x030C30C3
    return (value | (value << 2)) & 0x09249249


def _point_box_distance2(point, box):
    return sum(max(0.0, box[0][axis] - point[axis], point[axis] - box[1][axis]) ** 2
               for axis in range(3))


class _SurfaceIndex:
    """Morton-ordered AABB tree: one sort, bounded build, exact nearest queries."""
    def __init__(self, mesh, budget, bounds):
        self.vertices, self.faces = mesh["vertices"], mesh["faces"]
        self.budget, self.nodes = budget, []
        boxes, ordered = [], []
        extent = [bounds[1][axis] - bounds[0][axis] for axis in range(3)]
        for index, face in enumerate(self.faces):
            triangle = [self.vertices[v] for v in face]
            low = tuple(min(p[k] for p in triangle) for k in range(3))
            high = tuple(max(p[k] for p in triangle) for k in range(3))
            boxes.append((low, high))
            quantized = [min(1023, max(0, int(1023 * ((low[k] + high[k]) * 0.5 - bounds[0][k]) / extent[k])))
                         if extent[k] else 0 for k in range(3)]
            key = _spread10(quantized[0]) | (_spread10(quantized[1]) << 1) | (_spread10(quantized[2]) << 2)
            ordered.append((key, index))
        ordered.sort()
        indices = [index for _, index in ordered]
        leaf_size = budget.policy["bvh_leaf_faces"]

        def build(start, end):
            budget.use("bvh_build_nodes")
            if end - start <= leaf_size:
                selected = indices[start:end]
                box = (tuple(min(boxes[i][0][k] for i in selected) for k in range(3)),
                       tuple(max(boxes[i][1][k] for i in selected) for k in range(3)))
                node = (box, None, None, selected)
            else:
                middle = (start + end) // 2
                left, right = build(start, middle), build(middle, end)
                a, b = self.nodes[left][0], self.nodes[right][0]
                box = (tuple(min(a[0][k], b[0][k]) for k in range(3)),
                       tuple(max(a[1][k], b[1][k]) for k in range(3)))
                node = (box, left, right, None)
            self.nodes.append(node)
            return len(self.nodes) - 1

        self.root = build(0, len(indices))

    def nearest2(self, point):
        best = math.inf
        pending = [(_point_box_distance2(point, self.nodes[self.root][0]), self.root)]
        while pending:
            lower, index = heapq.heappop(pending)
            self.budget.use("bvh_node_visits")
            if lower > best:
                continue
            _, left, right, faces = self.nodes[index]
            if faces is not None:
                for face_index in faces:
                    self.budget.use("point_triangle_checks")
                    triangle = [self.vertices[v] for v in self.faces[face_index]]
                    best = min(best, _point_triangle_distance2(point, triangle))
                    if best == 0.0:
                        return 0.0
            else:
                for child in (left, right):
                    lower = _point_box_distance2(point, self.nodes[child][0])
                    if lower <= best:
                        heapq.heappush(pending, (lower, child))
        return best


def _samples(mesh, limit):
    """Include axis extrema, then index-stratified vertices and face centroids."""
    vertices, faces = mesh["vertices"], mesh["faces"]
    vertex_count = min(len(vertices), max(6, limit // 2))
    selected = []
    for axis in range(3):
        for function in (min, max):
            index = function(range(len(vertices)), key=lambda i: (vertices[i][axis], -i))
            if index not in selected:
                selected.append(index)
    for step in range(vertex_count):
        index = step * len(vertices) // vertex_count
        if index not in selected:
            selected.append(index)
    selected = selected[:vertex_count]
    points = [tuple(vertices[i]) for i in selected]
    face_count = min(len(faces), limit - len(points))
    for step in range(face_count):
        face = faces[step * len(faces) // face_count]
        points.append(tuple(math.fsum(vertices[i][k] for i in face) / 3.0 for k in range(3)))
    return points


def _direction(points, target, source_extent):
    values = [math.sqrt(target.nearest2(point)) for point in points]
    return {"samples": len(values), "max_distance": max(values),
            "rms_distance": math.sqrt(math.fsum(d * d for d in values) / len(values)),
            "max_source_extent_ratio": max(values) / source_extent}


def evaluate_candidate(source, candidate, purpose="surface_xpbd", scale=None) -> dict:
    """Return a content-bound geometry decision; neither input is ever changed.

    ``scale`` optionally uses the existing flow scale metadata (units=m,
    physical_extent_m and scale_axis). With no metadata only the common source
    coordinate frame is checked; no physical units are inferred. The two purposes
    currently share the engine's single closed-surface capacity and tolerances.
    """
    policy = dict(POLICY)
    report = {"schema_version": "candidate-quality/1", "purpose": purpose,
              "status": "rejected", "accepted": False, "code": None, "errors": [],
              "policy": policy, "source_sha256": None, "candidate_sha256": None,
              "geometry_hash_scope": "validated_vertices_faces; float_coordinates",
              "source_preserved": True, "candidate_modified": False,
              "thin_structure": "not_verified", "aperture": "not_verified",
              "physical_accuracy": "not_verified", "scene_ready": False,
              "source_self_intersections": "not_verified",
              "numeric_scale": {"status": "not_verified", "reason": "physical_units_not_verified"},
              "engine_audit": {"status": "not_performed"},
              "sampled_distance": {"status": "not_performed", "certified_hausdorff": False}}

    def fail(code, message, status="rejected"):
        report.update(status=status, accepted=False, code=code)
        report["errors"].append({"code": code, "message": message})
        return report

    if purpose not in PURPOSES:
        return fail("unsupported_purpose", "Choose surface_xpbd or liquid_volume; no other simulation representation is supported.", "unsupported")
    for label, mesh in (("source", source), ("candidate", candidate)):
        if isinstance(mesh, dict) and isinstance(mesh.get("vertices"), list) and isinstance(mesh.get("faces"), list):
            if (len(mesh["vertices"]) > policy["max_" + label + "_vertices"]
                    or len(mesh["faces"]) > policy["max_" + label + "_faces"]):
                return fail(label + "_capacity_exceeded", "The " + label + " exceeds the explicit quality-audit capacity; use a bounded separate asset.", "budget_exhausted")
        try:
            normalized = validate_mesh(mesh)
            report[label + "_sha256"] = digest(normalized)
            audited = audit_mesh(normalized, simulation=(label == "candidate"))
            report[label + "_audit"] = audited
        except (FlowError, ValueError, TypeError, OverflowError) as error:
            return fail("invalid_" + label + "_mesh", "Supply valid finite triangle geometry: " + str(error))
        if label == "source":
            source = normalized
            report["source_bbox"] = [audited["bbox_min"], audited["bbox_max"]]
        else:
            candidate = normalized
        if not audited["closed_oriented_single_component"]:
            return fail(label + "_topology_rejected", "The " + label + " must be one closed, consistently oriented nondegenerate surface; no repair or component removal is performed.")
        if audited["signed_volume"] <= 0:
            return fail(label + "_nonpositive_volume", "Outward orientation and positive enclosed volume are required; explicitly orient the source before candidate evaluation.")
    original, reduced = report["source_audit"], report["candidate_audit"]
    if original["euler_characteristic"] != reduced["euler_characteristic"]:
        return fail("candidate_topology_changed", "Euler characteristic changed; retain handles and use another representation instead of filling holes.")
    length = max(original["extent"])
    extent_change = max(abs(a - b) for a, b in zip(original["extent"], reduced["extent"])) / length
    center_change = max(abs((original["bbox_min"][k] + original["bbox_max"][k])
                            - (reduced["bbox_min"][k] + reduced["bbox_max"][k])) * 0.5
                        for k in range(3)) / length
    relative_volume = abs(reduced["signed_volume"] - original["signed_volume"]) / original["signed_volume"]
    report["volume_change"] = {"source_volume": original["signed_volume"], "candidate_volume": reduced["signed_volume"],
                               "relative_absolute_change": relative_volume,
                               "limit": policy["max_relative_volume_change"]}
    report["scale_check"] = {"units": "not_verified" if scale is None else "m",
                             "bbox_extent_change_source_extent_ratio": extent_change,
                             "bbox_center_change_source_extent_ratio": center_change,
                             "declared_extent_verified": False}
    if purpose == "liquid_volume":
        report["numeric_scale"] = {"status": "not_applicable",
                                  "reason": "surface_xpbd_volume_floor_does_not_qualify_liquid_particle_initialization"}
    if scale is not None:
        if (not isinstance(scale, dict) or scale.get("units", "m") != "m"
                or scale.get("scale_axis", "max") not in ("max", "x", "y", "z")
                or type(scale.get("physical_extent_m")) not in (int, float)
                or not 0 < scale["physical_extent_m"] <= 10000
                or not math.isfinite(scale["physical_extent_m"])):
            return fail("invalid_physical_scale", "Provide explicit metre extent and max/x/y/z scale axis, or omit scale for coordinate-only evaluation.")
        axis, extent = scale.get("scale_axis", "max"), scale["physical_extent_m"]
        values = [max(item["extent"]) if axis == "max" else item["extent"][("x", "y", "z").index(axis)]
                  for item in (original, reduced)]
        report["scale_check"].update(physical_extent_m=extent, scale_axis=axis,
                                     source_declared_extent_relative_error=abs(values[0] - extent) / extent,
                                     candidate_declared_extent_relative_error=abs(values[1] - extent) / extent)
        if any(abs(value - extent) / extent > policy["declared_extent_relative_tolerance"] for value in values):
            return fail("declared_scale_mismatch", "Source and candidate must match the declared physical extent in the same coordinate frame.")
        report["scale_check"]["declared_extent_verified"] = True
        if purpose == "surface_xpbd":
            floor = policy["surface_xpbd_min_volume_m3_exclusive"]
            report["numeric_scale"] = {"status": "passed" if reduced["signed_volume"] > floor else "rejected",
                                       "scope": "surface_xpbd_scene_volume_floor_only",
                                       "minimum_volume_m3_exclusive": floor,
                                       "candidate_volume_m3": reduced["signed_volume"]}
            if reduced["signed_volume"] <= floor:
                return fail("candidate_numeric_scale_rejected", "The surface XPBD scene requires volume greater than 1e-15 cubic metres; use a supported physical scale.")
    if extent_change > policy["max_bbox_extent_change_source_extent_ratio"] or center_change > policy["max_bbox_center_change_source_extent_ratio"]:
        return fail("candidate_scale_changed", "Bounding extent or position changed beyond policy; keep the source frame instead of hiding deformation through rescaling.")
    if relative_volume > policy["max_relative_volume_change"]:
        return fail("candidate_volume_changed", "Enclosed volume changed beyond policy; choose a more faithful candidate before assigning mass or filling particles.")
    try:
        module, sha = _engine_module()
        report["engine_audit"] = {"status": "running", "module_sha256": sha,
                                  "scope": "candidate_rest_geometry; require_closed=True",
                                  "limits": {name: getattr(module, name, None) for name in
                                             ("MAX_PAIR_CHECKS", "MAX_AUDIT_SECONDS", "MAX_VERTICES", "MAX_TRIANGLES")}}
        metadata = module.audit_mesh(candidate["vertices"], candidate["faces"], require_closed=True)
        if not isinstance(metadata, dict) or not metadata.get("self_intersection_checked") or not metadata.get("closed"):
            return fail("unsupported_engine_audit", "The engine did not attest a complete closed-surface intersection audit.", "unsupported")
        report["engine_audit"].update(status="passed", metadata=metadata)
        if purpose == "surface_xpbd" and report["scale_check"]["declared_extent_verified"]:
            engine_volume = metadata.get("signed_volume")
            if type(engine_volume) not in (int, float) or not math.isfinite(engine_volume):
                return fail("unsupported_engine_audit", "The engine did not return a finite closed-surface volume.", "unsupported")
            report["numeric_scale"]["engine_candidate_volume_m3"] = engine_volume
            if engine_volume <= policy["surface_xpbd_min_volume_m3_exclusive"]:
                report["numeric_scale"]["status"] = "rejected"
                return fail("candidate_numeric_scale_rejected", "The engine volume must exceed 1e-15 cubic metres for the surface XPBD scene.")
    except _EngineUnavailable as error:
        report["engine_audit"]["status"] = "unavailable"
        return fail("engine_auditor_unavailable", str(error), "unsupported")
    except ValueError as error:
        message = str(error)
        report["engine_audit"]["status"] = "not_verified" if "budget" in message.lower() else "rejected"
        if "budget" in message.lower():
            return fail("engine_audit_budget_exhausted", message, "budget_exhausted")
        code = ("candidate_self_intersection" if "self-intersect" in message.lower()
                else "candidate_nonmanifold_or_disconnected" if "connected surface" in message.lower() or "non-manifold" in message.lower()
                else "candidate_engine_geometry_rejected")
        return fail(code, message)
    except (ImportError, OSError, RuntimeError, TypeError, AttributeError) as error:
        report["engine_audit"]["status"] = "unavailable"
        return fail("engine_auditor_unavailable", "The geometry auditor could not complete: " + str(error), "unsupported")
    budget = _Budget(policy)
    report["sampled_distance"] = {"status": "running", "certified_hausdorff": False,
        "method": "deterministic_axis_extrema_and_index_stratified_vertices_and_face_centroids; exact_nearest_AABB_BVH",
        "coverage": "finite_samples; not_area_uniform; unsampled_features_can_be_missed",
        "source_extent": length, "limit_source_extent_ratio": policy["max_sampled_distance_source_extent_ratio"]}
    try:
        source_index = _SurfaceIndex(source, budget, report["source_bbox"])
        candidate_index = _SurfaceIndex(candidate, budget, [reduced["bbox_min"], reduced["bbox_max"]])
        forward = _direction(_samples(source, policy["samples_per_direction"]), candidate_index, length)
        backward = _direction(_samples(candidate, policy["samples_per_direction"]), source_index, length)
        ratio = max(forward["max_source_extent_ratio"], backward["max_source_extent_ratio"])
        report["sampled_distance"].update(status="measured", source_to_candidate=forward,
            candidate_to_source=backward, max_source_extent_ratio=ratio, work=dict(budget.counts))
        if ratio > policy["max_sampled_distance_source_extent_ratio"]:
            return fail("candidate_sampled_distance_exceeded", "Sampled surface deviation exceeds policy; increase candidate fidelity. Thin structures and apertures remain unverified even below this limit.")
    except _BudgetExceeded as error:
        report["sampled_distance"].update(status="not_verified", work=dict(budget.counts))
        return fail("distance_audit_budget_exhausted", str(error), "budget_exhausted")
    report.update(status="accepted", accepted=True, code="candidate_quality_passed")
    return report
