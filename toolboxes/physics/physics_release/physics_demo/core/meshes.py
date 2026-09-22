"""Bounded, deterministic triangle assets; image interpretation belongs to the agent.

Recipes and raw vertices use metres in local coordinates. Closed output is outward
wound. Auditing rejects ambiguous or expensive geometry instead of silently repairing
it: no remeshing, pixel inference, external files, or executable modeling language.
"""
from __future__ import annotations

import math
import time
import hashlib
import json
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from physics_demo.core.math3d import cross, dot, finite_number, norm, sub

MAX_VERTICES = 4096
MAX_TRIANGLES = 8192
MAX_PAIR_CHECKS = 250_000
MAX_AUDIT_SECONDS = 2.0
MAX_COORDINATE = 1000.0
EPSILON = 1e-9
_AUDIT_CACHE: OrderedDict = OrderedDict()
RECIPE_FIELDS = {
    "raw": {"vertices", "triangles"}, "ellipsoid": {"radii", "segments", "rings"},
    "box": {"size", "subdivisions"}, "torus": {"major_radius", "minor_radius", "segments", "tube_segments"},
    "lathe": {"profile", "segments"}, "extrusion": {"contour", "depth"},
    "cloth": {"size", "subdivisions"},
}


def _number(value: Any, label: str, low: float = EPSILON, high: float = MAX_COORDINATE) -> float:
    if not finite_number(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number in [{low:g}, {high:g}].")
    return float(value)


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in [{low}, {high}].")
    return value


def _vector(value: Any, size: int, label: str, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{label} must contain {size} numbers.")
    return [_number(v, label, EPSILON if positive else -MAX_COORDINATE) for v in value]


def _capacity(vertices: int, triangles: int) -> None:
    if vertices > MAX_VERTICES or triangles > MAX_TRIANGLES:
        raise ValueError(f"Mesh exceeds {MAX_VERTICES} vertices or {MAX_TRIANGLES} triangles; simplify first.")


def _orient2(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _inside2(p, tri, tolerance=EPSILON):
    signs = [_orient2(tri[i], tri[(i + 1) % 3], p) /
             math.hypot(*(tri[(i + 1) % 3][k] - tri[i][k] for k in range(2))) for i in range(3)]
    return min(signs) >= -tolerance or max(signs) <= tolerance


def _segment2(a, b, c, d, tolerance=EPSILON):
    """Contact points of two planar segments, including collinear overlap."""
    def on(p, x, y):
        return (abs(_orient2(x, y, p)) <= tolerance * math.hypot(y[0] - x[0], y[1] - x[1]) and
                all(min(x[k], y[k]) - tolerance <= p[k] <= max(x[k], y[k]) + tolerance for k in range(2)))
    contacts = [p for p in (a, b) if on(p, c, d)] + [p for p in (c, d) if on(p, a, b)]
    u, v = [b[k] - a[k] for k in range(2)], [d[k] - c[k] for k in range(2)]
    denominator = u[0] * v[1] - u[1] * v[0]
    if abs(denominator) > 1e-14 * math.hypot(*u) * math.hypot(*v):
        delta = [c[k] - a[k] for k in range(2)]
        t = (delta[0] * v[1] - delta[1] * v[0]) / denominator
        s = (delta[0] * u[1] - delta[1] * u[0]) / denominator
        if -tolerance <= t <= 1 + tolerance and -tolerance <= s <= 1 + tolerance:
            contacts.append([a[k] + t * u[k] for k in range(2)])
    return contacts


def _triangles_intersect(left, right, shared):
    """Detect contact beyond the vertices/edge two triangles intentionally share."""
    def allowed(p, common):
        if any(norm(sub(p, q)) <= EPSILON for q in common):
            return True
        if len(common) == 2:
            edge = sub(common[1], common[0])
            t = dot(sub(p, common[0]), edge) / dot(edge, edge)
            return -EPSILON <= t <= 1 + EPSILON and norm(sub(p, [common[0][k] + t * edge[k] for k in range(3)])) <= EPSILON
        return False

    for source, target in ((left, right), (right, left)):
        normal = cross(sub(target[1], target[0]), sub(target[2], target[0]))
        length = norm(normal)
        normal = [n / length for n in normal]
        distances = [dot(sub(p, target[0]), normal) for p in source]
        if min(distances) > EPSILON or max(distances) < -EPSILON:
            return False
        axis = max(range(3), key=lambda k: abs(normal[k]))
        axes = [k for k in range(3) if k != axis]
        flat = lambda p: [p[k] for k in axes]
        target2 = [flat(p) for p in target]
        for i, a in enumerate(source):
            b = source[(i + 1) % 3]
            da, db = distances[i], distances[(i + 1) % 3]
            if abs(da) <= EPSILON and _inside2(flat(a), target2) and not allowed(a, shared):
                return True
            if abs(da) <= EPSILON and abs(db) <= EPSILON:
                for j in range(3):
                    for p2 in _segment2(flat(a), flat(b), target2[j], target2[(j + 1) % 3]):
                        p = list(target[0])
                        p[axes[0]], p[axes[1]] = p2
                        p[axis] = target[0][axis] - sum(normal[k] * (p[k] - target[0][k]) for k in axes) / normal[axis]
                        if not allowed(p, shared):
                            return True
            elif da * db < 0:
                t = da / (da - db)
                p = [a[k] + t * (b[k] - a[k]) for k in range(3)]
                if _inside2(flat(p), target2) and not allowed(p, shared):
                    return True
    return False


@dataclass
class _BoundsNode:
    box: tuple
    count: int
    faces: list | None = None
    left: _BoundsNode | None = None
    right: _BoundsNode | None = None


def _overlap(a, b):
    return (a[0][0] <= b[1][0] + EPSILON and b[0][0] <= a[1][0] + EPSILON and
            a[0][1] <= b[1][1] + EPSILON and b[0][1] <= a[1][1] + EPSILON and
            a[0][2] <= b[1][2] + EPSILON and b[0][2] <= a[1][2] + EPSILON)


def _intersection_pair(vertices, triangles, groups=None, stats=None):
    """BVH candidate traversal; adjacent faces still receive exact contact tests.

    The unchanged work budget counts both node and triangle pair inspections.
    Optional counters are for benchmark diagnostics, never acceptance metadata.
    """
    deadline = time.monotonic() + MAX_AUDIT_SECONDS
    boxes = [(tuple(min(vertices[v][k] for v in face) for k in range(3)),
              tuple(max(vertices[v][k] for v in face) for k in range(3))) for face in triangles]
    centers = [tuple(lo[k] + hi[k] for k in range(3)) for lo, hi in boxes]
    counters = stats if stats is not None else {}
    counters.update(node_tests=0, candidate_pairs=0, exact_tests=0)

    def budget(kind):
        counters[kind] += 1
        work = counters["node_tests"] + counters["candidate_pairs"]
        if work > MAX_PAIR_CHECKS:
            raise ValueError("Mesh intersection audit exceeds its bounded work budget; simplify or split the mesh.")
        if work % 256 == 0 and time.monotonic() > deadline:
            raise ValueError("Mesh intersection audit exceeds its 2-second time budget; simplify or split the mesh.")

    def build(indices):
        box = (tuple(min(boxes[i][0][k] for i in indices) for k in range(3)),
               tuple(max(boxes[i][1][k] for i in indices) for k in range(3)))
        if len(indices) <= 4:
            return _BoundsNode(box, len(indices), faces=indices)
        axis = max(range(3), key=lambda k: max(centers[i][k] for i in indices) - min(centers[i][k] for i in indices))
        ordered = sorted(indices, key=lambda i: centers[i][axis])
        middle = len(ordered) // 2
        return _BoundsNode(box, len(indices), left=build(ordered[:middle]), right=build(ordered[middle:]))

    if groups is None:
        root = build(list(range(len(triangles))))
        pending = [(root, root)]
    else:
        # Cross-object queries never need candidate pairs within either object.
        separated = {}
        for index, group in enumerate(groups):
            separated.setdefault(group, []).append(index)
        roots = [build(indices) for indices in separated.values()]
        pending = [(a, b) for i, a in enumerate(roots) for b in roots[i + 1:]]
    while pending:
        a, b = pending.pop()
        budget("node_tests")
        if not _overlap(a.box, b.box):
            continue
        if a.faces is not None and b.faces is not None:
            for i, index in enumerate(a.faces):
                for other in (b.faces[i + 1:] if a is b else b.faces):
                    budget("candidate_pairs")
                    if not _overlap(boxes[index], boxes[other]):
                        continue
                    common = set(triangles[index]) & set(triangles[other]) if groups is None else set()
                    counters["exact_tests"] += 1
                    if _triangles_intersect([vertices[v] for v in triangles[index]],
                                            [vertices[v] for v in triangles[other]], [vertices[v] for v in common]):
                        return other, index
        elif a is b:
            pending.extend(((a.left, a.left), (a.left, a.right), (a.right, a.right)))
        elif b.faces is not None or (a.faces is None and a.count >= b.count):
            pending.extend(((a.left, b), (a.right, b)))
        else:
            pending.extend(((a, b.left), (a, b.right)))
    if time.monotonic() > deadline:
        raise ValueError("Mesh intersection audit exceeds its 2-second time budget; simplify or split the mesh.")
    return None


def _contains(vertices, triangles, point):
    """Solid-angle winding test, valid for the already audited closed surfaces."""
    angle = 0.0
    for face in triangles:
        a, b, c = [sub(vertices[v], point) for v in face]
        la, lb, lc = norm(a), norm(b), norm(c)
        if min(la, lb, lc) <= EPSILON:
            return True
        denominator = la * lb * lc + dot(a, b) * lc + dot(b, c) * la + dot(c, a) * lb
        angle += 2 * math.atan2(dot(a, cross(b, c)), denominator)
    return abs(angle) > 2 * math.pi


def meshes_intersect(first: dict, first_position: list, second: dict, second_position: list) -> bool:
    """Conservative initial overlap/contact check for two normalized mesh assets.

    Tangency within 1e-9 m is contact and returns True. Closed containment also
    returns True, including an open cloth wholly inside a closed shell. Open
    surfaces have no inferred interior. Inputs must already pass normalize_mesh.
    """
    positions = (first_position, second_position)
    if any(not isinstance(p, list) or len(p) != 3 or not all(finite_number(v) for v in p) for p in positions):
        raise ValueError("Mesh positions must contain three finite coordinates.")
    parts = [[[v[k] + position[k] for k in range(3)] for v in asset["vertices"]]
             for asset, position in zip((first, second), positions)]
    for asset in (first, second):
        _capacity(len(asset["vertices"]), len(asset["triangles"]))
    # Cheap whole-object bounds reject is important for scenes with several assets.
    if any(max(v[k] for v in parts[0]) < min(v[k] for v in parts[1]) - EPSILON or
           max(v[k] for v in parts[1]) < min(v[k] for v in parts[0]) - EPSILON for k in range(3)):
        return False
    faces = first["triangles"] + [[v + len(parts[0]) for v in face] for face in second["triangles"]]
    groups = [0] * len(first["triangles"]) + [1] * len(second["triangles"])
    if _intersection_pair(parts[0] + parts[1], faces, groups) is not None:
        return True
    return ((first["metadata"]["closed"] and _contains(parts[0], first["triangles"], parts[1][0])) or
            (second["metadata"]["closed"] and _contains(parts[1], second["triangles"], parts[0][0])))


def audit_mesh(vertices: Any, triangles: Any, require_closed: bool = False) -> dict[str, Any]:
    """Validate topology and bounded pairwise triangle intersection; never mutate input."""
    if not isinstance(vertices, list) or not isinstance(triangles, list) or len(vertices) < 3 or not triangles:
        raise ValueError("Mesh needs vertex and triangle lists with at least 3 vertices and 1 triangle.")
    _capacity(len(vertices), len(triangles))
    points = [_vector(v, 3, "vertex") for v in vertices]
    if len({tuple(p) for p in points}) != len(points):
        raise ValueError("Duplicate mesh vertices must be welded before use.")
    edges, seen, used, vertex_faces = {}, set(), set(), [[] for _ in points]
    minimum_edge, area, signed_volume = math.inf, 0.0, 0.0
    for index, face in enumerate(triangles):
        if not isinstance(face, list) or len(face) != 3 or any(type(v) is not int or not 0 <= v < len(points) for v in face):
            raise ValueError("Every triangle must have three integer vertex indices in range (booleans are invalid).")
        signature = tuple(sorted(face))
        if len(set(face)) != 3 or signature in seen:
            raise ValueError("Repeated vertex indices or duplicate triangles are invalid.")
        seen.add(signature)
        used.update(face)
        a, b, c = [points[v] for v in face]
        twice_area = norm(cross(sub(b, a), sub(c, a)))
        lengths = [norm(sub(points[face[i]], points[face[(i + 1) % 3]])) for i in range(3)]
        if min(lengths) <= EPSILON or twice_area <= EPSILON * max(lengths):
            raise ValueError("Mesh contains a degenerate or sub-nanometre triangle.")
        minimum_edge = min(minimum_edge, *lengths)
        area += 0.5 * twice_area
        # A local reference avoids cancellation for small meshes far from zero.
        signed_volume += dot(sub(a, points[0]), cross(sub(b, points[0]), sub(c, points[0]))) / 6.0
        for i, v in enumerate(face):
            vertex_faces[v].append(index)
            w = face[(i + 1) % 3]
            key = tuple(sorted((v, w)))
            edges.setdefault(key, []).append((index, v < w))
    if len(used) != len(points):
        raise ValueError("Unused mesh vertices are invalid.")
    adjacency = [set() for _ in triangles]
    for entries in edges.values():
        if len(entries) > 2:
            raise ValueError("Mesh is non-manifold: more than two triangles share an edge.")
        if len(entries) == 2:
            (a, direction_a), (b, direction_b) = entries
            if direction_a == direction_b:
                raise ValueError("Mesh triangle winding is inconsistent.")
            adjacency[a].add(b)
            adjacency[b].add(a)

    def reached(seed, allowed):
        visited, stack = {seed}, [seed]
        while stack:
            for neighbor in adjacency[stack.pop()] & allowed - visited:
                visited.add(neighbor)
                stack.append(neighbor)
        return visited

    if len(reached(0, set(range(len(triangles))))) != len(triangles):
        raise ValueError("Mesh must be one connected surface; use separate assets for separate objects.")
    if any(len(reached(faces[0], set(faces))) != len(faces) for faces in vertex_faces):
        raise ValueError("Mesh has a non-manifold vertex fan.")
    boundary = sum(len(entries) == 1 for entries in edges.values())
    if require_closed and boundary:
        raise ValueError("This use requires a closed mesh; boundary edges were found.")
    if not boundary and abs(signed_volume) <= EPSILON ** 3:
        raise ValueError("A closed mesh must enclose nonzero volume.")
    # Bounded content cache: metadata claims never form the key, and every call
    # still checks finite values, index types, resource limits and topology.
    digest = hashlib.sha256(json.dumps([points, triangles], separators=(",", ":"), allow_nan=False).encode()).digest()
    if digest in _AUDIT_CACHE:
        _AUDIT_CACHE.move_to_end(digest)
        return deepcopy(_AUDIT_CACHE[digest])
    intersection = _intersection_pair(points, triangles)
    if intersection is not None:
        raise ValueError(f"Mesh triangles {intersection[0]} and {intersection[1]} self-intersect or touch outside their shared edge/vertex.")
    metadata = {"vertex_count": len(points), "triangle_count": len(triangles), "edge_count": len(edges),
                "boundary_edges": boundary, "closed": boundary == 0, "connected": True,
                "consistent_orientation": True, "self_intersection_checked": True,
                "signed_volume": signed_volume if not boundary else None, "surface_area": area,
                "min_edge": minimum_edge, "bounds": [[min(v[k] for v in points) for k in range(3)],
                                                       [max(v[k] for v in points) for k in range(3)]]}
    _AUDIT_CACHE[digest] = deepcopy(metadata)
    if len(_AUDIT_CACHE) > 16:
        _AUDIT_CACHE.popitem(last=False)
    return metadata


def _lathe(profile, segments):
    poles = int(profile[0][0] == 0) + int(profile[-1][0] == 0)
    _capacity((len(profile) - poles) * segments + 2, 2 * (len(profile) - poles) * segments)
    vertices, triangles, rings = [], [], []
    for radius, y in profile:
        if radius == 0:
            rings.append([len(vertices)])
            vertices.append([0.0, y, 0.0])
        else:
            rings.append(list(range(len(vertices), len(vertices) + segments)))
            vertices.extend([[radius * math.cos(2 * math.pi * i / segments), y,
                              radius * math.sin(2 * math.pi * i / segments)] for i in range(segments)])
    if len(rings[0]) != 1:
        rings.insert(0, [len(vertices)])
        vertices.append([0.0, profile[0][1], 0.0])
    if len(rings[-1]) != 1:
        rings.append([len(vertices)])
        vertices.append([0.0, profile[-1][1], 0.0])
    for lower, upper in zip(rings, rings[1:]):
        for i in range(segments):
            j = (i + 1) % segments
            if len(lower) == 1:
                triangles.append([lower[0], upper[i], upper[j]])
            elif len(upper) == 1:
                triangles.append([lower[i], upper[0], lower[j]])
            else:
                triangles.extend([[lower[i], upper[i], upper[j]], [lower[i], upper[j], lower[j]]])
    return vertices, triangles


def _box(size, divisions):
    _capacity(6 * divisions * divisions + 2, 12 * divisions * divisions)
    vertices, triangles, lookup = [], [], {}
    for axis in range(3):
        for sign in (-1, 1):
            grid = []
            for i in range(divisions + 1):
                row = []
                for j in range(divisions + 1):
                    coord = [0, 0, 0]
                    coord[axis] = sign * divisions
                    coord[(axis + 1) % 3] = 2 * i - divisions
                    coord[(axis + 2) % 3] = 2 * j - divisions
                    key = tuple(coord)
                    if key not in lookup:
                        lookup[key] = len(vertices)
                        vertices.append([coord[k] * size[k] / (2 * divisions) for k in range(3)])
                    row.append(lookup[key])
                grid.append(row)
            for i in range(divisions):
                for j in range(divisions):
                    a, b, c, d = grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]
                    triangles.extend([[a, b, c], [a, c, d]] if sign > 0 else [[a, c, b], [a, d, c]])
    return vertices, triangles


def _polygon(contour):
    if not isinstance(contour, list) or not 3 <= len(contour) <= 256:
        raise ValueError("Extrusion contour needs 3–256 points without a repeated closing point.")
    points = [_vector(p, 2, "contour point") for p in contour]
    if len({tuple(p) for p in points}) != len(points):
        raise ValueError("Contour points must be unique; omit the repeated closing point.")
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        if abs(_orient2(points[i - 1], a, b)) <= EPSILON * math.hypot(b[0] - a[0], b[1] - a[1]):
            raise ValueError("Remove collinear contour vertices before extrusion.")
        for j in range(i + 1, len(points)):
            if j == i + 1 or (i == 0 and j == len(points) - 1):
                continue
            if _segment2(a, b, points[j], points[(j + 1) % len(points)]):
                raise ValueError("Extrusion contour must be simple, without crossings or holes.")
    signed_area = sum(_orient2(points[0], p, points[(i + 1) % len(points)]) for i, p in enumerate(points))
    if abs(signed_area) <= EPSILON ** 2:
        raise ValueError("Extrusion contour must enclose nonzero area.")
    if signed_area < 0:
        points.reverse()
    remaining, faces = list(range(len(points))), []
    while len(remaining) > 3:
        for i, b in enumerate(remaining):
            a, c = remaining[i - 1], remaining[(i + 1) % len(remaining)]
            triangle = [points[a], points[b], points[c]]
            if _orient2(*triangle) > EPSILON ** 2 and not any(_inside2(points[v], triangle) for v in remaining if v not in (a, b, c)):
                faces.append([a, b, c])
                remaining.pop(i)
                break
        else:
            raise ValueError("Contour triangulation is ill-conditioned; simplify the contour.")
    return points, faces + [remaining]


def _provenance(provenance):
    provenance = provenance if provenance is not None else {"source": "description", "notes": "Procedural dimensions supplied by the agent."}
    if (not isinstance(provenance, dict) or set(provenance) - {"source", "notes"} or
            provenance.get("source") not in ("description", "image", "imagined") or
            not isinstance(provenance.get("notes", ""), str) or len(provenance.get("notes", "")) > 2000):
        raise ValueError("provenance requires source description/image/imagined and at most 2000 characters of notes.")
    if provenance["source"] in ("image", "imagined") and not provenance.get("notes", "").strip():
        raise ValueError("Image/imagined meshes require notes describing inferred depth and unobserved geometry.")
    return dict(provenance)


def normalize_mesh(asset: Any) -> dict[str, Any]:
    """Recompute derived metadata, retaining only declared provenance and recipe.

    Saved assets are never trusted merely because they carry an earlier audit flag.
    Negative closed volume is rejected instead of silently altering raw topology.
    """
    if not isinstance(asset, dict) or set(asset) - {"vertices", "triangles", "metadata"}:
        raise ValueError("Mesh asset accepts only vertices, triangles, and metadata.")
    metadata = audit_mesh(asset.get("vertices"), asset.get("triangles"))
    old = asset.get("metadata", {})
    if not isinstance(old, dict) or set(old) - set(metadata) - {"recipe", "provenance", "units"}:
        raise ValueError("Mesh metadata contains unknown fields.")
    recipe = old.get("recipe", "raw")
    if not isinstance(recipe, str) or recipe not in RECIPE_FIELDS or old.get("units", "metres") != "metres":
        raise ValueError("Mesh metadata requires a supported recipe and metre units.")
    if metadata["closed"] and metadata["signed_volume"] < 0:
        raise ValueError("Closed mesh winding must point outward; reverse every triangle explicitly.")
    metadata.update({"recipe": recipe, "provenance": _provenance(old.get("provenance")), "units": "metres"})
    return {"vertices": [[float(v) for v in p] for p in asset["vertices"]],
            "triangles": [list(face) for face in asset["triangles"]], "metadata": metadata}


def build_mesh(spec: Any) -> dict[str, Any]:
    """Build a recipe or validate an inline raw asset. See RECIPE_FIELDS."""
    if not isinstance(spec, dict) or not isinstance(spec.get("type"), str) or spec["type"] not in RECIPE_FIELDS:
        raise ValueError("Mesh type must be raw, ellipsoid, box, torus, lathe, extrusion, or cloth.")
    kind = spec["type"]
    if set(spec) - RECIPE_FIELDS[kind] - {"type", "provenance"}:
        raise ValueError(f"Unknown {kind} mesh fields; accepted recipe fields are {sorted(RECIPE_FIELDS[kind])}.")
    provenance = _provenance(spec.get("provenance"))
    if kind == "raw":
        vertices, triangles = spec.get("vertices"), spec.get("triangles")
    elif kind == "box":
        vertices, triangles = _box(_vector(spec.get("size", [1, 1, 1]), 3, "size", True),
                                   _integer(spec.get("subdivisions", 2), "subdivisions", 1, 16))
    elif kind in ("ellipsoid", "lathe"):
        segments = _integer(spec.get("segments", 16), "segments", 8, 64)
        if kind == "ellipsoid":
            radii = _vector(spec.get("radii", [0.5, 0.5, 0.5]), 3, "radii", True)
            rings = _integer(spec.get("rings", 8), "rings", 3, 64)
            profile = [[0, -1]] + [[math.sin(math.pi * i / rings), -math.cos(math.pi * i / rings)] for i in range(1, rings)] + [[0, 1]]
        else:
            if not isinstance(spec.get("profile"), list) or not 2 <= len(spec["profile"]) <= 64:
                raise ValueError("Lathe profile needs 2–64 [radius, y] points.")
            profile = [_vector(p, 2, "profile point") for p in spec["profile"]]
            if any(p[0] < 0 or (p[0] == 0 and i not in (0, len(profile) - 1)) for i, p in enumerate(profile)) or any(a[1] >= b[1] for a, b in zip(profile, profile[1:])):
                raise ValueError("Lathe profile y must increase strictly; radii are positive except optional axis endpoints.")
            if all(p[0] == 0 for p in profile):
                raise ValueError("Lathe profile must include a positive radius.")
        vertices, triangles = _lathe(profile, segments)
        if kind == "ellipsoid":
            vertices = [[p[k] * radii[k] for k in range(3)] for p in vertices]
    elif kind == "torus":
        major = _number(spec.get("major_radius", 0.7), "major_radius")
        minor = _number(spec.get("minor_radius", 0.2), "minor_radius")
        if minor >= major:
            raise ValueError("Torus minor_radius must be smaller than major_radius.")
        segments = _integer(spec.get("segments", 24), "segments", 8, 64)
        tube = _integer(spec.get("tube_segments", 12), "tube_segments", 6, 64)
        _capacity(segments * tube, 2 * segments * tube)
        vertices = [[(major + minor * math.cos(2 * math.pi * j / tube)) * math.cos(2 * math.pi * i / segments),
                     minor * math.sin(2 * math.pi * j / tube),
                     (major + minor * math.cos(2 * math.pi * j / tube)) * math.sin(2 * math.pi * i / segments)]
                    for i in range(segments) for j in range(tube)]
        triangles = []
        for i in range(segments):
            for j in range(tube):
                a, b, c, d = i * tube + j, ((i + 1) % segments) * tube + j, ((i + 1) % segments) * tube + (j + 1) % tube, i * tube + (j + 1) % tube
                triangles.extend([[a, c, b], [a, d, c]])
    elif kind == "extrusion":
        points, caps = _polygon(spec.get("contour"))
        depth = _number(spec.get("depth", 0.2), "depth")
        count = len(points)
        vertices = [[p[0], p[1], z] for z in (-depth / 2, depth / 2) for p in points]
        triangles = [list(reversed(face)) for face in caps] + [[v + count for v in face] for face in caps]
        for i in range(count):
            j = (i + 1) % count
            triangles.extend([[i, j, j + count], [i, j + count, i + count]])
    else:
        size = _vector(spec.get("size", [1, 1]), 2, "size", True)
        divisions = _integer(spec.get("subdivisions", 8), "subdivisions", 1, 63)
        _capacity((divisions + 1) ** 2, 2 * divisions ** 2)
        vertices = [[size[0] * (i / divisions - 0.5), 0, size[1] * (j / divisions - 0.5)]
                    for i in range(divisions + 1) for j in range(divisions + 1)]
        triangles = []
        for i in range(divisions):
            for j in range(divisions):
                a = i * (divisions + 1) + j
                b, c, d = a + divisions + 1, a + divisions + 2, a + 1
                triangles.extend([[a, c, b], [a, d, c]])
    return normalize_mesh({"vertices": vertices, "triangles": triangles,
                           "metadata": {"recipe": kind, "provenance": provenance}})
