"""Conservative, reproducible removal of tiny detached display components.

This module uses only the Python standard library.  The QQ host keeps a trusted
mirror so that it can independently recompute the policy and submesh proof.
It does not infer semantics, repair surfaces, smooth geometry or prepare a
physics mesh.  A component is removed only when every fixed policy gate passes.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from types import MappingProxyType


class DisplayCleanupError(ValueError):
    """Invalid geometry or an unauthentic cleanup report."""


POLICY_ID = "bounded-floaters/1"
POLICY = MappingProxyType({
    "id": POLICY_ID,
    "main_area_ratio_min": 0.95,
    "component_extent_ratio_max": 0.01,
    "component_area_ratio_max": 0.00005,
    "component_absolute_volume_ratio_max": 0.000001,
    "component_faces_max": 32,
    "surface_gap_main_extent_ratio_min": 0.005,
    "surface_gap_component_extent_factor_min": 2.0,
    "total_removed_area_ratio_max": 0.005,
    "total_removed_absolute_volume_ratio_max": 0.0001,
    "closed_oriented_component_required": True,
    "positive_absolute_volume_required": True,
    "nondegenerate_candidate_faces_required": True,
    "unreferenced_vertices_action": "keep_all",
    "budget_overflow_action": "keep_all",
    "max_triangle_pair_checks": 1_000_000,
    "max_bvh_node_visits": 1_000_000,
    "max_display_vertices": 1_000_000,
    "max_display_faces": 2_000_000,
    "distance_method": "exact_triangle_distance_threshold_with_AABB_BVH",
    "retained_geometry_action": "preserve_coordinates_and_face_order_compact_used_vertices",
})


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _geometry(mesh):
    if not isinstance(mesh, dict) or set(mesh) != {"vertices", "faces"}:
        raise DisplayCleanupError("Geometry must contain only vertices and faces")
    vertices, faces = mesh["vertices"], mesh["faces"]
    if (not isinstance(vertices, list) or not 3 <= len(vertices) <= POLICY["max_display_vertices"]
            or not isinstance(faces, list) or not 1 <= len(faces) <= POLICY["max_display_faces"]):
        raise DisplayCleanupError("Geometry exceeds display cleanup bounds")
    for vertex in vertices:
        if (not isinstance(vertex, (list, tuple)) or len(vertex) != 3
                or any(type(x) not in (int, float) or not math.isfinite(x)
                       or not -1e12 <= x <= 1e12 for x in vertex)):
            raise DisplayCleanupError("Vertices must be bounded finite coordinates")
    for face in faces:
        if (not isinstance(face, (list, tuple)) or len(face) != 3
                or any(type(i) is not int or not 0 <= i < len(vertices) for i in face)
                or len(set(face)) != 3):
            raise DisplayCleanupError("Faces must have three distinct in-range indices")
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


def clean_display_mesh(mesh, mode="conservative"):
    """Return a bounded conservative submesh and its independently verifiable report.

    ``none`` preserves the complete source. All budget failures keep the entire
    source; they never deliver a partially analysed removal. Metadata and units
    belong to the caller, rather than this geometry-only algorithm.
    """
    if mode not in ("conservative", "none"):
        raise DisplayCleanupError("Cleanup mode must be conservative or none")
    vertices, faces = _geometry(mesh)
    components = _components(vertices, faces)
    total_area = math.fsum(c["area"] for c in components)
    total_volume = math.fsum(c["absolute_volume"] for c in components)
    main = max(components, key=lambda c: (c["area"], -c["vertices"][0]))
    main_ratio = main["area"] / total_area if total_area else 0.0
    removed_components, removed = [], []
    analysis_complete, skip_reason = True, None
    if mode == "none":
        skip_reason = "mode_none"
    elif any(not c["faces"] for c in components):
        skip_reason = "unreferenced_vertices"
    elif not total_area or not total_volume or not main["extent"]:
        skip_reason = "insufficient_positive_geometry"
    elif main_ratio < POLICY["main_area_ratio_min"]:
        skip_reason = "main_not_dominant"
    else:
        candidates = [c for c in components if c is not main
                      and 0 < len(c["faces"]) <= POLICY["component_faces_max"]
                      and c["extent"] / main["extent"] <= POLICY["component_extent_ratio_max"]
                      and c["area"] / total_area <= POLICY["component_area_ratio_max"]
                      and 0 < c["absolute_volume"] / total_volume <= POLICY["component_absolute_volume_ratio_max"]
                      and not c["has_degenerate_faces"] and _closed_oriented(c, faces)]
        if not candidates:
            skip_reason = "no_bounded_candidates"
        else:
            triangles = [tuple(vertices[i] for i in faces[index]) for index in main["faces"]]
            boxes = [_box(t) for t in triangles]
            centers = [tuple((box[0][k] + box[1][k]) / 2 for k in range(3)) for box in boxes]
            root = _Node(list(range(len(triangles))), boxes, centers)
            budget = {"nodes": 0, "pairs": 0}
            try:
                for component in candidates:
                    threshold = max(POLICY["surface_gap_main_extent_ratio_min"] * main["extent"],
                                    POLICY["surface_gap_component_extent_factor_min"] * component["extent"])
                    candidate_triangles = [tuple(vertices[i] for i in faces[index])
                                           for index in component["faces"]]
                    if _gap_passes(candidate_triangles, root, triangles, threshold, budget):
                        removed_components.append(component)
            except _AnalysisBudgetExceeded:
                removed_components = []
                analysis_complete, skip_reason = False, "analysis_budget_exceeded"
            removed_area = math.fsum(c["area"] for c in removed_components)
            removed_volume = math.fsum(c["absolute_volume"] for c in removed_components)
            if (removed_area / total_area > POLICY["total_removed_area_ratio_max"]
                    or removed_volume / total_volume > POLICY["total_removed_absolute_volume_ratio_max"]):
                removed_components = []
                skip_reason = "cleanup_budget_exceeded"
            removed = sorted(index for c in removed_components for index in c["faces"])
    output = _compact(vertices, faces, removed)
    removed_area = math.fsum(c["area"] for c in removed_components)
    removed_volume = math.fsum(c["absolute_volume"] for c in removed_components)
    report = {
        "schema_version": "modeling-display-cleanup/1",
        "policy_id": POLICY_ID,
        "policy": dict(POLICY),
        "policy_sha256": _sha(dict(POLICY)),
        "mode": mode,
        "applied": bool(removed),
        "analysis_complete": analysis_complete,
        "skip_reason": skip_reason,
        "raw_geometry_sha256": _sha(mesh),
        "output_geometry_sha256": _sha(output),
        "removed_face_indices": removed,
        "removed_face_indices_sha256": _sha(removed),
        "summary": {
            "source_components": len(components),
            "remaining_components": len(components) - len(removed_components),
            "removed_components": len(removed_components),
            "source_vertices": len(vertices),
            "remaining_vertices": len(output["vertices"]),
            "removed_vertices": len(vertices) - len(output["vertices"]),
            "source_faces": len(faces),
            "remaining_faces": len(output["faces"]),
            "removed_faces": len(removed),
            "source_area": total_area,
            "source_absolute_volume": total_volume,
            "source_main_max_extent": main["extent"],
            "main_area_ratio": main_ratio,
            "removed_area_ratio": removed_area / total_area if total_area else 0.0,
            "removed_absolute_volume_ratio": removed_volume / total_volume if total_volume else 0.0,
        },
    }
    return output, report


def validate_cleanup(raw, clean, report):
    """Independently recompute all policy gates and prove the exact submesh.

    Neither a worker-provided removal list nor its hashes authorise deletion.
    Report fields, canonical types and geometry must exactly match recomputation.
    """
    _geometry(clean)
    if not isinstance(report, dict) or report.get("mode") not in ("conservative", "none"):
        raise DisplayCleanupError("Invalid cleanup report")
    expected_mesh, expected_report = clean_display_mesh(raw, report["mode"])
    try:
        actual_mesh_bytes, report_bytes = _canonical(clean), _canonical(report)
    except (TypeError, ValueError, OverflowError) as error:
        raise DisplayCleanupError("Cleanup proof is not canonical finite JSON") from error
    if actual_mesh_bytes != _canonical(expected_mesh):
        raise DisplayCleanupError("Cleaned geometry is not the authorised source submesh")
    if report_bytes != _canonical(expected_report):
        raise DisplayCleanupError("Cleanup report does not match independent recomputation")
    return True
