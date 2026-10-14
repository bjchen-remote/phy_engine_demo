"""Bounded display surface cleanup, independently reproducible with stdlib.

Whole-component removal is conservative and geometric, without subject labels.
Taubin smoothing preserves triangle connectivity and limits coordinate changes;
self-intersections and reconstruction accuracy are not certified.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from types import MappingProxyType


class SurfaceCleanupError(ValueError):
    """Invalid geometry or an unauthentic surface cleanup proof."""


POLICY_ID = "bounded-surface/1"
POLICY = MappingProxyType({
    "id": POLICY_ID,
    "main_area_ratio_min": 0.95,
    "component_extent_ratio_max": 0.01,
    "component_area_ratio_max": 0.00005,
    "component_faces_max": 32,
    "surface_gap_main_extent_ratio_min": 0.005,
    "surface_gap_component_extent_factor_min": 2.0,
    "total_removed_area_ratio_max": 0.005,
    "total_removed_faces_ratio_max": 0.005,
    "total_removed_faces_absolute_allowance": 32,
    "component_topology_requirement": "whole_detached_component; open_and_flat_allowed",
    "degenerate_retained_faces_action": "keep_and_freeze_incident_vertices_and_one_ring",
    "budget_overflow_action": "keep_all_components",
    "max_triangle_pair_checks": 1_000_000,
    "max_bvh_node_visits": 1_000_000,
    "max_display_vertices": 1_000_000,
    "max_display_faces": 2_000_000,
    "max_surface_vertices": 150_000,
    "max_surface_faces": 300_000,
    "distance_method": "exact_triangle_distance_threshold_with_AABB_BVH",
    "smoothing_method": "uniform_laplacian_taubin",
    "smoothing_pairs": 6,
    "lambda": 0.5,
    "mu": -0.53,
    "crease_angle_degrees": 85.0,
    "minimum_triangle_angle_degrees": 2.5,
    "feature_freeze_rings": 1,
    "max_displacement_main_extent_ratio": 0.01,
    "max_displacement_mean_edge_ratio": 0.8,
    "triangle_area_ratio_min": 0.2,
    "orientation_dot_ratio_min": 0.000001,
    "unsafe_proposal_retry_limit": 4,
    "retained_topology_action": "preserve_face_order_winding_and_indices_after_component_compaction",
    "self_intersections": "not_certified",
})


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _geometry(mesh):
    if not isinstance(mesh, dict) or set(mesh) != {"vertices", "faces"}:
        raise SurfaceCleanupError("Geometry must contain only vertices and faces")
    vertices, faces = mesh["vertices"], mesh["faces"]
    if (not isinstance(vertices, list) or not 3 <= len(vertices) <= POLICY["max_display_vertices"]
            or not isinstance(faces, list) or not 1 <= len(faces) <= POLICY["max_display_faces"]):
        raise SurfaceCleanupError("Geometry exceeds display cleanup bounds")
    for vertex in vertices:
        if (not isinstance(vertex, (list, tuple)) or len(vertex) != 3
                or any(type(x) not in (int, float) or not math.isfinite(x)
                       or not -1e12 <= x <= 1e12 for x in vertex)):
            raise SurfaceCleanupError("Vertices must be bounded finite coordinates")
    for face in faces:
        if (not isinstance(face, (list, tuple)) or len(face) != 3
                or any(type(i) is not int or not 0 <= i < len(vertices) for i in face)
                or len(set(face)) != 3):
            raise SurfaceCleanupError("Faces must have three distinct in-range indices")
    return vertices, faces


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, t):
    return (a[0] * t, a[1] * t, a[2] * t)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length2(a):
    return _dot(a, a)


def _box(points):
    return (tuple(min(p[k] for p in points) for k in range(3)),
            tuple(max(p[k] for p in points) for k in range(3)))


def _box_distance2(a, b):
    return math.fsum(max(0.0, a[0][k] - b[1][k], b[0][k] - a[1][k]) ** 2
                     for k in range(3))


def _point_segment_distance2(p, a, b):
    delta = _sub(b, a)
    length = _length2(delta)
    t = min(1.0, max(0.0, _dot(_sub(p, a), delta) / length)) if length else 0.0
    return _length2(_sub(p, _add(a, _mul(delta, t))))


def _point_triangle_distance2(p, t):
    # Voronoi regions of the triangle (including a degenerate edge fallback).
    a, b, c = t
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    if _length2(_cross(ab, ac)) == 0.0:
        return min(_point_segment_distance2(p, a, b),
                   _point_segment_distance2(p, b, c),
                   _point_segment_distance2(p, c, a))
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return _length2(ap)
    bp = _sub(p, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return _length2(bp)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        return _length2(_sub(p, _add(a, _mul(ab, d1 / (d1 - d3)))))
    cp = _sub(p, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return _length2(cp)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        return _length2(_sub(p, _add(a, _mul(ac, d2 / (d2 - d6)))))
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and d4 - d3 >= 0.0 and d5 - d6 >= 0.0:
        bc = _sub(c, b)
        return _length2(_sub(p, _add(b, _mul(bc, (d4 - d3) / ((d4 - d3) + (d5 - d6))))))
    denom = va + vb + vc
    if not denom:
        return min(_point_segment_distance2(p, a, b),
                   _point_segment_distance2(p, b, c),
                   _point_segment_distance2(p, c, a))
    closest = _add(a, _add(_mul(ab, vb / denom), _mul(ac, vc / denom)))
    return _length2(_sub(p, closest))


def _segment_distance2(p1, q1, p2, q2):
    # Closest points of two bounded segments; do not use a scale-dependent
    # epsilon that would erase very small geometry after unit normalization.
    u, v, w = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, b, c, d, e = _dot(u, u), _dot(u, v), _dot(v, v), _dot(u, w), _dot(v, w)
    if not a:
        return _point_segment_distance2(p1, p2, q2)
    if not c:
        return _point_segment_distance2(p2, p1, q1)
    denom = a * c - b * b
    s = min(1.0, max(0.0, (b * e - c * d) / denom)) if denom > 0.0 else 0.0
    t = (b * s + e) / c
    if t < 0.0:
        t, s = 0.0, min(1.0, max(0.0, -d / a))
    elif t > 1.0:
        t, s = 1.0, min(1.0, max(0.0, (b - d) / a))
    return _length2(_add(w, _sub(_mul(u, s), _mul(v, t))))


def _segment_intersects_triangle(p, q, triangle):
    # Moller-Trumbore intersection. Coplanar contact is detected separately by
    # point/triangle and edge/edge distances in _triangle_distance2.
    a, b, c = triangle
    direction, e1, e2 = _sub(q, p), _sub(b, a), _sub(c, a)
    h = _cross(direction, e2)
    determinant = _dot(e1, h)
    if determinant == 0.0:
        return False
    inverse = 1.0 / determinant
    s = _sub(p, a)
    u = inverse * _dot(s, h)
    if u < 0.0 or u > 1.0:
        return False
    r = _cross(s, e1)
    v = inverse * _dot(direction, r)
    if v < 0.0 or u + v > 1.0:
        return False
    t = inverse * _dot(e2, r)
    return 0.0 <= t <= 1.0


def _triangle_distance2(a, b):
    for k in range(3):
        if (_segment_intersects_triangle(a[k], a[(k + 1) % 3], b)
                or _segment_intersects_triangle(b[k], b[(k + 1) % 3], a)):
            return 0.0
    value = min(*(_point_triangle_distance2(p, b) for p in a),
                *(_point_triangle_distance2(p, a) for p in b))
    for i in range(3):
        for j in range(3):
            value = min(value, _segment_distance2(a[i], a[(i + 1) % 3], b[j], b[(j + 1) % 3]))
    return value


class _Node:
    __slots__ = ("box", "indices", "left", "right")

    def __init__(self, indices, boxes, centers):
        self.box = (tuple(min(boxes[i][0][k] for i in indices) for k in range(3)),
                    tuple(max(boxes[i][1][k] for i in indices) for k in range(3)))
        self.left = self.right = None
        if len(indices) <= 12:
            self.indices = indices
        else:
            axis = max(range(3), key=lambda k: self.box[1][k] - self.box[0][k])
            indices.sort(key=lambda i: (centers[i][axis], i))
            middle = len(indices) // 2
            self.indices = None
            self.left = _Node(indices[:middle], boxes, centers)
            self.right = _Node(indices[middle:], boxes, centers)


class _AnalysisBudgetExceeded(Exception):
    pass


def _gap_passes(component_triangles, root, triangles, threshold, budget):
    # Traverse only boxes whose exact lower-bound distance is below the gate.
    # Every surviving triangle pair is tested exactly; no vertex sampling or
    # bounding-box approximation is accepted as the final surface predicate.
    threshold2 = threshold * threshold
    component_box = _box([p for triangle in component_triangles for p in triangle])
    pending = [root]
    while pending:
        node = pending.pop()
        budget["nodes"] += 1
        if budget["nodes"] > POLICY["max_bvh_node_visits"]:
            raise _AnalysisBudgetExceeded
        if _box_distance2(component_box, node.box) >= threshold2:
            continue
        if node.indices is None:
            pending.extend((node.right, node.left))
            continue
        for index in node.indices:
            b = triangles[index]
            bbox = _box(b)
            for a in component_triangles:
                if _box_distance2(_box(a), bbox) >= threshold2:
                    continue
                budget["pairs"] += 1
                if budget["pairs"] > POLICY["max_triangle_pair_checks"]:
                    raise _AnalysisBudgetExceeded
                if _triangle_distance2(a, b) < threshold2:
                    return False
    return True


def _components(vertices, faces):
    parent, rank = list(range(len(vertices))), [0] * len(vertices)

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a, b):
        a, b = find(a), find(b)
        if a == b:
            return
        if rank[a] < rank[b]:
            a, b = b, a
        parent[b] = a
        if rank[a] == rank[b]:
            rank[a] += 1

    for a, b, c in faces:
        union(a, b)
        union(a, c)
    groups = {}
    for index in range(len(vertices)):
        groups.setdefault(find(index), {"vertices": [], "faces": []})["vertices"].append(index)
    for index, face in enumerate(faces):
        groups[find(face[0])]["faces"].append(index)
    result = sorted(groups.values(), key=lambda c: c["vertices"][0])
    for component in result:
        points = [vertices[i] for i in component["vertices"]]
        component["box"] = _box(points)
        component["extent"] = max(component["box"][1][k] - component["box"][0][k] for k in range(3))
        areas, volumes = [], []
        for index in component["faces"]:
            a, b, c = (vertices[i] for i in faces[index])
            areas.append(math.sqrt(_length2(_cross(_sub(b, a), _sub(c, a)))) / 2.0)
            volumes.append(_dot(a, _cross(b, c)) / 6.0)
        component["area"] = math.fsum(areas)
        component["absolute_volume"] = abs(math.fsum(volumes))
        component["has_degenerate_faces"] = any(area == 0.0 for area in areas)
    return result


def _closed_oriented(component, faces):
    counts, winding = Counter(), Counter()
    for index in component["faces"]:
        a, b, c = faces[index]
        for x, y in ((a, b), (b, c), (c, a)):
            edge = (min(x, y), max(x, y))
            counts[edge] += 1
            winding[edge] += 1 if x < y else -1
    return bool(counts) and all(n == 2 and winding[edge] == 0 for edge, n in counts.items())


def _compact(vertices, faces, removed):
    if not removed:
        return {"vertices": [list(v) for v in vertices], "faces": [list(f) for f in faces]}
    removed_set = set(removed)
    kept = [face for index, face in enumerate(faces) if index not in removed_set]
    used = sorted({i for face in kept for i in face})
    mapping = {old: new for new, old in enumerate(used)}
    return {"vertices": [list(vertices[i]) for i in used],
            "faces": [[mapping[i] for i in face] for face in kept]}


def _normalized(vertices):
    low, high = _box(vertices)
    extent = max(high[k] - low[k] for k in range(3))
    if extent <= 0.0:
        return [tuple(v) for v in vertices], (0.0, 0.0, 0.0), 1.0
    origin = tuple(low[k] + (high[k] - low[k]) / 2 for k in range(3))
    return [tuple((v[k] - origin[k]) / extent for k in range(3)) for v in vertices], origin, extent


def _topology(mesh):
    vertices, faces = _geometry(mesh)
    points, _, _ = _normalized(vertices)
    counts, winding, seen, used = Counter(), Counter(), set(), set()
    duplicate = degenerate = 0
    for face in faces:
        key = tuple(sorted(face))
        duplicate += key in seen
        seen.add(key)
        a, b, c = (points[i] for i in face)
        degenerate += _length2(_cross(_sub(b, a), _sub(c, a))) == 0.0
        used.update(face)
        for x, y in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = min(x, y), max(x, y)
            counts[edge] += 1
            winding[edge] += 1 if x < y else -1
    components = _components(points, faces)
    boundary = sum(value == 1 for value in counts.values())
    nonmanifold = sum(value > 2 for value in counts.values())
    inconsistent = sum(value == 2 and winding[edge] != 0 for edge, value in counts.items())
    return {"vertices": len(vertices), "faces": len(faces), "edges": len(counts),
            "components": sum(bool(c["faces"]) for c in components),
            "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "inconsistent_winding_edges": inconsistent, "duplicate_faces": duplicate,
            "zero_area_faces": degenerate, "unreferenced_vertices": len(vertices) - len(used),
            "euler_characteristic": len(used) - len(counts) + len(faces),
            "closed_oriented": bool(counts) and boundary == nonmanifold == inconsistent == duplicate == degenerate == 0}


def _remove_components(vertices, faces):
    points, _, normalizing_extent = _normalized(vertices)
    components = _components(points, faces)
    main = max(components, key=lambda c: (c["area"], -c["vertices"][0]))
    total_area = math.fsum(c["area"] for c in components)
    main_ratio = main["area"] / total_area if total_area else 0.0
    removed_components = []
    complete, reason = True, None
    budget = {"nodes": 0, "pairs": 0}
    candidate_count = 0
    if any(not c["faces"] for c in components):
        reason = "unreferenced_vertices"
    elif not total_area or not main["extent"]:
        reason = "insufficient_positive_geometry"
    elif main_ratio < POLICY["main_area_ratio_min"]:
        reason = "main_not_dominant"
    else:
        candidates = [c for c in components if c is not main
                      and 0 < len(c["faces"]) <= POLICY["component_faces_max"]
                      and c["extent"] / main["extent"] <= POLICY["component_extent_ratio_max"]
                      and c["area"] / total_area <= POLICY["component_area_ratio_max"]]
        candidate_count = len(candidates)
        if not candidates:
            reason = "no_bounded_candidates"
        else:
            triangles = [tuple(points[i] for i in faces[index]) for index in main["faces"]]
            boxes = [_box(t) for t in triangles]
            centers = [tuple((box[0][k] + box[1][k]) / 2 for k in range(3)) for box in boxes]
            root = _Node(list(range(len(triangles))), boxes, centers)
            try:
                for component in candidates:
                    threshold = max(POLICY["surface_gap_main_extent_ratio_min"] * main["extent"],
                                    POLICY["surface_gap_component_extent_factor_min"] * component["extent"])
                    triangles_c = [tuple(points[i] for i in faces[index]) for index in component["faces"]]
                    if _gap_passes(triangles_c, root, triangles, threshold, budget):
                        removed_components.append(component)
            except _AnalysisBudgetExceeded:
                removed_components = []
                complete, reason = False, "analysis_budget_exceeded"
            removed_area = math.fsum(c["area"] for c in removed_components)
            removed_faces = sum(len(c["faces"]) for c in removed_components)
            face_budget = max(POLICY["total_removed_faces_absolute_allowance"],
                              math.floor(len(faces) * POLICY["total_removed_faces_ratio_max"]))
            if removed_area / total_area > POLICY["total_removed_area_ratio_max"] or removed_faces > face_budget:
                removed_components = []
                reason = "cleanup_budget_exceeded"
    removed = sorted(index for c in removed_components for index in c["faces"])
    removed_set = set(removed)
    used = sorted({index for face_index, face in enumerate(faces) if face_index not in removed_set for index in face}) if removed else list(range(len(vertices)))
    output = _compact(vertices, faces, removed)
    return output, used, removed, {
        "analysis_complete": complete, "skip_reason": reason,
        "candidate_components": candidate_count, "removed_components": len(removed_components),
        "source_components": len(components), "remaining_components": len(components) - len(removed_components),
        "source_area": total_area * normalizing_extent ** 2,
        "source_main_max_extent": main["extent"] * normalizing_extent,
        "main_area_ratio": main_ratio,
        "removed_area_ratio": math.fsum(c["area"] for c in removed_components) / total_area if total_area else 0.0,
        "removed_open_or_degenerate_components": sum(not _closed_oriented(c, faces) or c["has_degenerate_faces"] for c in removed_components),
        "distance_budget": budget,
    }


def _smoothing_graph(points, faces):
    neighbors = [set() for _ in points]
    edges, normals, seen = {}, [], {}
    protected = {reason: set() for reason in ("boundary", "nonmanifold", "inconsistent_winding", "crease", "narrow_triangle", "degenerate", "duplicate")}
    min_angle_cos = math.cos(math.radians(POLICY["minimum_triangle_angle_degrees"]))
    for index, face in enumerate(faces):
        a, b, c = (points[i] for i in face)
        normal = _cross(_sub(b, a), _sub(c, a))
        normals.append(normal)
        if _length2(normal) == 0.0:
            protected["degenerate"].update(face)
        key = tuple(sorted(face))
        if key in seen:
            protected["duplicate"].update(face)
        seen[key] = index
        for x, y, z in ((face[0], face[1], face[2]), (face[1], face[2], face[0]), (face[2], face[0], face[1])):
            neighbors[x].update((y, z))
            edge = min(x, y), max(x, y)
            edges.setdefault(edge, []).append((index, 1 if x < y else -1))
            u, v = _sub(points[y], points[x]), _sub(points[z], points[x])
            length = math.sqrt(_length2(u) * _length2(v))
            if length and _dot(u, v) / length > min_angle_cos:
                protected["narrow_triangle"].update(face)
    crease_cos = math.cos(math.radians(POLICY["crease_angle_degrees"]))
    for edge, entries in edges.items():
        if len(entries) == 1:
            protected["boundary"].update(edge)
        elif len(entries) > 2:
            protected["nonmanifold"].update(edge)
        elif entries[0][1] == entries[1][1]:
            protected["inconsistent_winding"].update(edge)
        else:
            first, second = (normals[item[0]] for item in entries)
            length = math.sqrt(_length2(first) * _length2(second))
            if length and _dot(first, second) / length < crease_cos:
                protected["crease"].update(edge)
    seed = set().union(*protected.values())
    frozen = seed | {neighbor for index in seed for neighbor in neighbors[index]}
    frozen.update(index for index, adjacent in enumerate(neighbors) if len(adjacent) < 3)
    adjacency = [sorted(adjacent) for adjacent in neighbors]
    return adjacency, frozen, normals, {reason: len(indices) for reason, indices in protected.items()}


def _unsafe_faces(points, faces, original_normals):
    unsafe = []
    minimum_area2 = POLICY["triangle_area_ratio_min"] ** 2
    for index, face in enumerate(faces):
        reference = original_normals[index]
        reference2 = _length2(reference)
        if not reference2:
            continue
        a, b, c = (points[i] for i in face)
        normal = _cross(_sub(b, a), _sub(c, a))
        if (_length2(normal) < minimum_area2 * reference2
                or _dot(normal, reference) <= POLICY["orientation_dot_ratio_min"] * reference2):
            unsafe.append(face)
    return unsafe


def _smooth(mesh, main_extent):
    original, origin, normalization_extent = _normalized(mesh["vertices"])
    points = list(original)
    faces = mesh["faces"]
    adjacency, frozen, normals, reason_counts = _smoothing_graph(points, faces)
    hard_limit = main_extent / normalization_extent * POLICY["max_displacement_main_extent_ratio"]
    limits = [min(hard_limit, POLICY["max_displacement_mean_edge_ratio"] *
                  math.fsum(math.sqrt(_length2(_sub(points[i], points[j]))) for j in adjacent) / len(adjacent))
              if adjacent else 0.0 for i, adjacent in enumerate(adjacency)]
    eligible = [i for i in range(len(points)) if i not in frozen and limits[i] > 0.0]
    rejected_steps = clamped = orientation_freeze_count = 0
    accepted_steps = 0
    for _ in range(POLICY["smoothing_pairs"]):
        for factor in (POLICY["lambda"], POLICY["mu"]):
            proposal = list(points)
            for index in eligible:
                if index in frozen:
                    continue
                average = tuple(math.fsum(points[j][axis] for j in adjacency[index]) / len(adjacency[index]) for axis in range(3))
                candidate = _add(points[index], _mul(_sub(average, points[index]), factor))
                delta = _sub(candidate, original[index])
                distance = math.sqrt(_length2(delta))
                if distance > limits[index]:
                    candidate = _add(original[index], _mul(delta, limits[index] / distance))
                    clamped += 1
                proposal[index] = candidate
            for _retry in range(POLICY["unsafe_proposal_retry_limit"]):
                unsafe = _unsafe_faces(proposal, faces, normals)
                if not unsafe:
                    points = proposal
                    accepted_steps += 1
                    break
                affected = {i for face in unsafe for i in face if proposal[i] != points[i]}
                if not affected:
                    rejected_steps += 1
                    break
                orientation_freeze_count += len(affected - frozen)
                frozen.update(affected)
                for index in affected:
                    proposal[index] = points[index]
            else:
                rejected_steps += 1
    output_vertices = [list(vertex) for vertex in mesh["vertices"]]
    moved = []
    displacements = []
    for index, point in enumerate(points):
        distance = math.sqrt(_length2(_sub(point, original[index]))) * normalization_extent
        if distance > normalization_extent * 1e-15:
            output_vertices[index] = [origin[axis] + point[axis] * normalization_extent for axis in range(3)]
            moved.append(index)
            # Report displacement of actual emitted coordinates, not a pre-rounding intermediate.
            displacements.append(math.sqrt(_length2(_sub(output_vertices[index], mesh["vertices"][index]))))
    output = {"vertices": output_vertices, "faces": [list(face) for face in faces]}
    # Cross products need the same original affine frame for an orientation proof.
    after_in_original_frame = [tuple((v[k] - origin[k]) / normalization_extent for k in range(3)) for v in output["vertices"]]
    if _unsafe_faces(after_in_original_frame, faces, normals) or _topology(output) != _topology(mesh):
        output = {"vertices": [list(v) for v in mesh["vertices"]], "faces": [list(f) for f in faces]}
        moved, displacements = [], []
        final_rejected = True
    else:
        final_rejected = False
    return output, moved, {
        "eligible_vertices": len(eligible), "protected_vertices": len(frozen),
        "protected_seed_reason_counts": reason_counts,
        "orientation_frozen_vertices": orientation_freeze_count,
        "accepted_half_steps": accepted_steps, "rejected_half_steps": rejected_steps,
        "displacement_clamp_count": clamped, "final_smoothing_rejected": final_rejected,
        "moved_vertices": len(moved), "max_displacement": max(displacements, default=0.0),
        "rms_displacement": math.sqrt(math.fsum(d * d for d in displacements) / len(displacements)) if displacements else 0.0,
        "max_displacement_main_extent_ratio": max(displacements, default=0.0) / main_extent if main_extent else 0.0,
        "triangle_orientation_preserved": True, "retained_topology_preserved": True,
    }


def _budget_preserved(mesh):
    """Keep oversize valid input exactly; do not allocate adjacency or BVH data."""
    vertices, faces = mesh["vertices"], mesh["faces"]
    output = {"vertices": [list(vertex) for vertex in vertices], "faces": [list(face) for face in faces]}
    low, high = _box(vertices)
    extent = max(high[k] - low[k] for k in range(3))
    identity = {"encoding": "identity", "vertex_count": len(vertices)}
    smoothing = {"eligible_vertices": 0, "protected_vertices": len(vertices),
                 "protected_seed_reason_counts": {}, "orientation_frozen_vertices": 0,
                 "accepted_half_steps": 0, "rejected_half_steps": 0,
                 "displacement_clamp_count": 0, "final_smoothing_rejected": False,
                 "moved_vertices": 0, "max_displacement": 0.0, "rms_displacement": 0.0,
                 "max_displacement_main_extent_ratio": 0.0,
                 "triangle_orientation_preserved": True, "retained_topology_preserved": True}
    topology = {"analysis": "not_performed; exact_source_geometry_preserved"}
    return output, {
        "schema_version": "modeling-surface-cleanup/1", "policy_id": POLICY_ID,
        "policy": dict(POLICY), "policy_sha256": _sha(dict(POLICY)), "mode": "surface",
        "applied": False, "analysis_complete": False, "skip_reason": "surface_operation_budget_exceeded",
        "raw_geometry_sha256": _sha(mesh), "output_geometry_sha256": _sha(output),
        "removed_face_indices": [], "removed_face_indices_sha256": _sha([]),
        "original_to_output_vertex_indices": None, "vertex_mapping_encoding": "identity",
        "vertex_mapping_sha256": _sha(identity),
        "moved_original_vertex_indices": [], "moved_original_vertex_indices_sha256": _sha([]),
        "retained_face_indices_sha256": _sha([list(face) for face in faces]),
        "retained_topology_before": topology, "retained_topology_after": dict(topology),
        "smoothing": smoothing,
        "summary": {"analysis_complete": False, "skip_reason": "surface_operation_budget_exceeded",
                    "candidate_components": None, "removed_components": 0,
                    "source_components": None, "remaining_components": None,
                    "source_area": None, "source_main_max_extent": extent, "main_area_ratio": None,
                    "removed_area_ratio": 0.0, "removed_open_or_degenerate_components": 0,
                    "distance_budget": {"nodes": 0, "pairs": 0},
                    "source_vertices": len(vertices), "remaining_vertices": len(vertices), "removed_vertices": 0,
                    "source_faces": len(faces), "remaining_faces": len(faces), "removed_faces": 0, **smoothing},
        "limitations": ["The fixed surface operation budget was exceeded; valid source geometry is preserved exactly without smoothing or component analysis."],
    }


def clean_surface_mesh(mesh, mode="surface"):
    """Remove bounded detached debris and smooth safely within a fixed policy.

    Only surface mode is accepted; legacy conservative/none contracts remain in
    their existing module. No semantic label or worker-provided knob changes
    this deterministic geometry policy.
    """
    if mode != "surface":
        raise SurfaceCleanupError("Surface cleanup mode must be surface")
    vertices, faces = _geometry(mesh)
    if len(vertices) > POLICY["max_surface_vertices"] or len(faces) > POLICY["max_surface_faces"]:
        return _budget_preserved(mesh)
    retained, used_original, removed, removal = _remove_components(vertices, faces)
    output, moved, smoothing = _smooth(retained, removal["source_main_max_extent"])
    removed_set = set(removed)
    mapping = [None] * len(vertices)
    for new_index, old_index in enumerate(used_original):
        mapping[old_index] = new_index
    kept_faces_original = [list(face) for i, face in enumerate(faces) if i not in removed_set]
    report = {
        "schema_version": "modeling-surface-cleanup/1", "policy_id": POLICY_ID,
        "policy": dict(POLICY), "policy_sha256": _sha(dict(POLICY)), "mode": mode,
        "applied": bool(removed or moved), "analysis_complete": removal["analysis_complete"],
        "skip_reason": removal["skip_reason"],
        "raw_geometry_sha256": _sha(mesh), "output_geometry_sha256": _sha(output),
        "removed_face_indices": removed, "removed_face_indices_sha256": _sha(removed),
        "original_to_output_vertex_indices": mapping, "vertex_mapping_encoding": "explicit",
        "vertex_mapping_sha256": _sha(mapping),
        "moved_original_vertex_indices": [used_original[index] for index in moved],
        "moved_original_vertex_indices_sha256": _sha([used_original[index] for index in moved]),
        "retained_face_indices_sha256": _sha(kept_faces_original),
        "retained_topology_before": _topology(retained), "retained_topology_after": _topology(output),
        "smoothing": smoothing,
        "summary": {**removal, "source_vertices": len(vertices), "remaining_vertices": len(output["vertices"]),
                    "removed_vertices": len(vertices) - len(output["vertices"]),
                    "source_faces": len(faces), "remaining_faces": len(output["faces"]),
                    "removed_faces": len(removed), **smoothing},
        "limitations": [
            "Geometry rules cannot distinguish a tiny detached accessory from debris.",
            "Smoothing changes retained coordinates within fixed bounds; hair, anatomy and reference fidelity are not certified.",
            "Retained connectivity, orientation and zero-area counts are checked; self-intersections are not certified.",
            "No retained connected faces are deleted, no holes are filled, and no unseen surface is reconstructed.",
        ],
    }
    return output, report


def validate_surface_cleanup(raw, clean, report):
    """Recompute the complete algorithm and compare exact canonical proof bytes."""
    _geometry(clean)
    if not isinstance(report, dict) or report.get("mode") != "surface":
        raise SurfaceCleanupError("Invalid surface cleanup report")
    expected_mesh, expected_report = clean_surface_mesh(raw)
    try:
        mesh_bytes, report_bytes = _canonical(clean), _canonical(report)
    except (TypeError, ValueError, OverflowError) as error:
        raise SurfaceCleanupError("Surface cleanup proof must be canonical finite JSON") from error
    if mesh_bytes != _canonical(expected_mesh):
        raise SurfaceCleanupError("Surface geometry differs from independent bounded recomputation")
    if report_bytes != _canonical(expected_report):
        raise SurfaceCleanupError("Surface cleanup report differs from independent bounded recomputation")
    return True
