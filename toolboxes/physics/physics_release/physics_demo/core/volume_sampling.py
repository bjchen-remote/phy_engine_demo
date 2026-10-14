"""Deterministic, bounded closed-mesh volume sampling for instant conversion.

The source is an explicitly metre-scaled homogeneous volume, not a melting or
phase-change model.  A projected-triangle BVH supplies oriented ray intervals;
no lattice point walks every source triangle.  Metadata never grants admission.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import re

from physics_demo.core.meshes import audit_mesh

SAMPLER_VERSION = "closed-mesh-ray-lattice/1"
MAX_CANDIDATE_CELLS = 250_000
MAX_TRIANGLE_QUERIES = 2_000_000
MAX_NODE_VISITS = 2_000_000
MAX_VOLUME_RELATIVE_ERROR = 0.05
MAX_SOURCE_COVERAGE_DISTANCE_SPACING_RATIO = 2.0
MAX_COVERAGE_CELL_VISITS = 5_000_000
MAX_COVERAGE_POINT_CHECKS = 5_000_000
COVERAGE_CELL_RADIUS = 3
MAX_CACHE_ENTRIES = 8
MAX_CACHE_POINTS = 100_000
_SAMPLE_CACHE = OrderedDict()


class VolumeSamplingError(ValueError):
    """An explicit source, sampling-work, or resolution rejection."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(code + ": " + message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def qualify_mesh(mesh, units="m"):
    """Reaudit geometry as one closed outward surface; discard metadata claims."""
    if units != "m":
        raise ValueError("mesh_volume requires explicitly declared metre units")
    if not isinstance(mesh, dict) or set(mesh) - {"vertices", "triangles", "metadata"}:
        raise ValueError("mesh_volume mesh accepts vertices, triangles and metadata only")
    vertices, triangles = mesh.get("vertices"), mesh.get("triangles")
    metadata = audit_mesh(vertices, triangles, require_closed=True)
    if metadata["signed_volume"] <= 0:
        raise ValueError("mesh_volume requires positive outward signed volume; reverse all faces explicitly")
    return {"vertices": [[float(v) for v in point] for point in vertices],
            "triangles": [list(face) for face in triangles],
            "metadata": {**metadata, "units": "metres", "recipe": "raw",
                         "provenance": {"source": "imagined",
                                        "notes": "Explicitly scaled closed-mesh initial water volume."}}}


def _cross2(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _sub2(a, b):
    return a[0] - b[0], a[1] - b[1]


def _top_left(a, b):
    # Adjacent projected triangles make opposite choices on a shared edge.
    dy, dx = b[1] - a[1], b[0] - a[0]
    return dy > 0 or (dy == 0 and dx < 0)


def _ray_hit(triangle, u, v):
    """Half-open projected raster rule, returning (axis coordinate, crossing)."""
    projected, axial, area, crossing = triangle
    if area < 0:
        projected = (projected[0], projected[2], projected[1])
        axial = (axial[0], axial[2], axial[1])
        area = -area
    point = (u, v)
    edges = []
    for i in range(3):
        a, b = projected[i], projected[(i + 1) % 3]
        # Evaluate each undirected edge in a canonical direction so two
        # adjacent faces get exactly opposite floating-point edge values.
        # Computing the reversed edge independently can double-count a ray
        # lying numerically on their diagonal.
        first, second = (a, b) if a < b else (b, a)
        edge = _cross2(_sub2(second, first), _sub2(point, first))
        if a != first:
            edge = -edge
        if edge < 0 or (edge == 0 and not _top_left(a, b)):
            return None
        edges.append(edge)
    coordinate = (edges[1] * axial[0] + edges[2] * axial[1] + edges[0] * axial[2]) / area
    return coordinate, crossing


def _build_bvh(triangles):
    boxes = [(min(p[0] for p in t[0]), min(p[1] for p in t[0]),
              max(p[0] for p in t[0]), max(p[1] for p in t[0])) for t in triangles]

    def build(indices):
        box = (min(boxes[i][0] for i in indices), min(boxes[i][1] for i in indices),
               max(boxes[i][2] for i in indices), max(boxes[i][3] for i in indices))
        if len(indices) <= 8:
            return box, tuple(indices), None, None
        axis = 0 if box[2] - box[0] >= box[3] - box[1] else 1
        indices.sort(key=lambda i: (boxes[i][axis] + boxes[i][axis + 2], i))
        middle = len(indices) // 2
        return box, None, build(indices[:middle]), build(indices[middle:])

    return build(list(range(len(triangles))))


def _intervals(root, triangles, u, v, work):
    pending, events = [root], []
    while pending:
        box, indices, left, right = pending.pop()
        work[0] += 1
        if work[0] > MAX_NODE_VISITS:
            raise VolumeSamplingError("mesh_volume_query_budget", "BVH node visit budget exceeded; coarsen or simplify")
        if u < box[0] or u > box[2] or v < box[1] or v > box[3]:
            continue
        if indices is None:
            pending.extend((right, left))
            continue
        for index in indices:
            work[1] += 1
            if work[1] > MAX_TRIANGLE_QUERIES:
                raise VolumeSamplingError("mesh_volume_query_budget", "ray triangle query budget exceeded; coarsen or simplify")
            hit = _ray_hit(triangles[index], u, v)
            if hit is not None:
                events.append(hit)
    events.sort()
    intervals, depth, start = [], 0, None
    # Equal-position crossings cancel tangencies. No geometric tolerance fills
    # a thin interval or silently welds distinct source surfaces.
    index = 0
    while index < len(events):
        coordinate, delta = events[index]
        index += 1
        while index < len(events) and events[index][0] == coordinate:
            delta += events[index][1]
            index += 1
        old = depth
        depth += delta
        if depth < 0 or depth > 1:
            raise VolumeSamplingError("mesh_volume_ray_ambiguity", "oriented scanline has ambiguous crossings")
        if old == 0 and depth == 1:
            start = coordinate
        elif old == 1 and depth == 0:
            if coordinate > start:
                intervals.append((start, coordinate))
    if depth:
        raise VolumeSamplingError("mesh_volume_ray_ambiguity", "oriented scanline did not close")
    return intervals


def _source_surface_coverage(mesh, center, points, spacing):
    """Bounded finite source samples must have nearby actual lattice particles.

    Two spacings accommodate oblique tetrahedron corners on a midpoint lattice.
    This engineering admission tolerance is not a wall-thickness, aperture or
    continuous-surface distance certificate. A fixed seven-cell neighborhood
    contains every particle within the tolerance, including rounding margins.
    """
    grid = {}
    for point in points:
        cell = tuple(math.floor(value / spacing) for value in point)
        grid.setdefault(cell, []).append(point)
    vertices, faces = mesh["vertices"], mesh["triangles"]
    cell_visits = point_checks = samples = 0
    maximum = 0.0
    limit2 = (MAX_SOURCE_COVERAGE_DISTANCE_SPACING_RATIO * spacing) ** 2
    offsets = range(-COVERAGE_CELL_RADIUS, COVERAGE_CELL_RADIUS + 1)

    def source_points():
        for vertex in vertices:
            yield [vertex[k] + center[k] for k in range(3)]
        for face in faces:
            yield [math.fsum(vertices[i][k] for i in face) / 3.0 + center[k] for k in range(3)]

    for point in source_points():
        samples += 1
        cell = tuple(math.floor(value / spacing) for value in point)
        nearest = math.inf
        for dx in offsets:
            for dy in offsets:
                for dz in offsets:
                    cell_visits += 1
                    if cell_visits > MAX_COVERAGE_CELL_VISITS:
                        raise VolumeSamplingError("mesh_volume_coverage_budget", "source coverage cell-visit budget exceeded; simplify the source")
                    for particle in grid.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()):
                        point_checks += 1
                        if point_checks > MAX_COVERAGE_POINT_CHECKS:
                            raise VolumeSamplingError("mesh_volume_coverage_budget", "source coverage point-distance budget exceeded; simplify the source")
                        nearest = min(nearest, math.fsum((point[k] - particle[k]) ** 2 for k in range(3)))
        if nearest > limit2 * (1.0 + 1e-10):
            raise VolumeSamplingError("mesh_volume_feature_coverage",
                "a source vertex or triangle centroid has no particle within two spacings; use finer spacing to retain thin or extended source features")
        maximum = max(maximum, nearest)
    return {"status": "passed", "method": "all_source_vertices_and_triangle_centroids; fixed_neighborhood_particle_spatial_hash",
        "samples": samples, "source_vertices": len(vertices), "triangle_centroids": len(faces),
        "maximum_nearest_particle_distance_m": math.sqrt(maximum),
        "maximum_nearest_particle_distance_spacing_ratio": math.sqrt(maximum) / spacing,
        "limit_spacing_ratio": MAX_SOURCE_COVERAGE_DISTANCE_SPACING_RATIO,
        "cell_radius": COVERAGE_CELL_RADIUS, "cell_visits": cell_visits, "point_distance_checks": point_checks,
        "certified_continuous_surface_coverage": False,
        "scope": "finite_source_samples_only; thin_walls_and_apertures_not_certified"}


def sample_mesh_volume(shape, spacing):
    """Return fresh world-space points and a deterministic acceptance receipt.

    The same function is the authoritative count, planner and initialization
    source. Any changed spacing requires a new call and repeats the volume gate.
    No points are truncated to satisfy a particle budget.
    """
    required = {"type", "mesh", "center", "units", "conversion", "conservation", "reference_density_kg_m3"}
    optional = {"source_ref", "receipt_sha256"}
    if not isinstance(shape, dict) or not required <= set(shape) or set(shape) - required - optional or shape.get("type") != "mesh_volume":
        raise ValueError("mesh_volume requires type, mesh, center, units, conversion, conservation and reference_density_kg_m3")
    if ("source_ref" in shape) != ("receipt_sha256" in shape) or any(
            not isinstance(shape[name], str) or re.fullmatch(r"[a-f0-9]{64}", shape[name]) is None
            for name in optional if name in shape):
        raise ValueError("mesh_volume source_ref and receipt_sha256 must be paired SHA-256 strings")
    if shape["units"] != "m" or shape["conversion"] != "instant" or shape["conservation"] != "volume":
        raise ValueError("mesh_volume requires units=m, conversion=instant and conservation=volume")
    if not _finite_number(shape["reference_density_kg_m3"]) or shape["reference_density_kg_m3"] != 1000:
        raise ValueError("mesh_volume first version requires reference_density_kg_m3=1000")
    center = shape["center"]
    if not isinstance(center, list) or len(center) != 3 or any(not _finite_number(v) or abs(v) > 10_000 for v in center):
        raise ValueError("mesh_volume center must contain three finite world coordinates within +/-10000 m")
    center = [float(v) for v in center]
    if not _finite_number(spacing) or not 1e-4 <= spacing <= 0.5:
        raise ValueError("mesh_volume spacing must be finite in [0.0001, 0.5] m")
    spacing = float(spacing)
    mesh = qualify_mesh(shape["mesh"], shape["units"])
    geometry = {key: mesh[key] for key in ("vertices", "triangles")}
    source_hash = _digest(geometry)
    key = (source_hash, tuple(center), spacing, SAMPLER_VERSION)
    if key in _SAMPLE_CACHE:
        cached = _SAMPLE_CACHE[key]
        receipt = cached["receipt"]
        if receipt["candidate_cells"] > MAX_CANDIDATE_CELLS:
            raise VolumeSamplingError("mesh_volume_candidate_budget", "cached candidate count exceeds current budget")
        if receipt["triangle_queries"] > MAX_TRIANGLE_QUERIES or receipt["bvh_node_visits"] > MAX_NODE_VISITS:
            raise VolumeSamplingError("mesh_volume_query_budget", "cached query work exceeds current budget")
        coverage = receipt["source_surface_coverage"]
        if coverage["cell_visits"] > MAX_COVERAGE_CELL_VISITS or coverage["point_distance_checks"] > MAX_COVERAGE_POINT_CHECKS:
            raise VolumeSamplingError("mesh_volume_coverage_budget", "cached source coverage work exceeds current budget")
        if coverage["maximum_nearest_particle_distance_spacing_ratio"] > MAX_SOURCE_COVERAGE_DISTANCE_SPACING_RATIO * (1.0 + 1e-10):
            raise VolumeSamplingError("mesh_volume_feature_coverage", "cached source coverage exceeds the current distance tolerance")
        _SAMPLE_CACHE.move_to_end(key)
        return deepcopy(cached)
    lo, hi = mesh["metadata"]["bounds"]
    counts = [max(1, math.ceil((b - a) / spacing)) for a, b in zip(lo, hi)]
    candidates = math.prod(counts)
    if candidates > MAX_CANDIDATE_CELLS:
        raise VolumeSamplingError("mesh_volume_candidate_budget", f"{candidates} candidate cells exceed {MAX_CANDIDATE_CELLS}; coarsen spacing")
    origin = [a + 0.5 * spacing for a in lo]
    axis = max(range(3), key=lambda k: (counts[k], -k))
    u_axis, v_axis = (axis + 1) % 3, (axis + 2) % 3
    triangles = []
    for face in mesh["triangles"]:
        vertices = [mesh["vertices"][i] for i in face]
        projected = tuple((p[u_axis] - lo[u_axis], p[v_axis] - lo[v_axis]) for p in vertices)
        axial = tuple(p[axis] - lo[axis] for p in vertices)
        area = _cross2(_sub2(projected[1], projected[0]), _sub2(projected[2], projected[0]))
        if area:
            triangles.append((projected, axial, area, -1 if area > 0 else 1))
    if not triangles:
        raise VolumeSamplingError("mesh_volume_resolution", "no nonparallel volume boundary triangles")
    root = _build_bvh(triangles)
    work, points, occupied = [0, 0], [], set()
    for ui in range(counts[u_axis]):
        u = (ui + 0.5) * spacing
        for vi in range(counts[v_axis]):
            v = (vi + 0.5) * spacing
            for low, high in _intervals(root, triangles, u, v, work):
                # Boundary policy: closed at entry, open at exit.
                first = max(0, math.ceil(low / spacing - 0.5))
                last = min(counts[axis], math.ceil(high / spacing - 0.5))
                for ai in range(first, last):
                    point = [0.0, 0.0, 0.0]
                    for k, index in ((axis, ai), (u_axis, ui), (v_axis, vi)):
                        point[k] = origin[k] + index * spacing + center[k]
                    if not all(math.isfinite(value) for value in point):
                        raise VolumeSamplingError("mesh_volume_nonfinite_points", "sampled world coordinates are not finite")
                    points.append(point)
                    indices = [0, 0, 0]
                    indices[axis], indices[u_axis], indices[v_axis] = ai, ui, vi
                    occupied.add(tuple(indices))
    points.sort()
    if not points:
        raise VolumeSamplingError("mesh_volume_resolution", "no interior particles at this spacing; use finer spacing")
    if len({tuple(p) for p in points}) != len(points):
        raise VolumeSamplingError("mesh_volume_duplicate_points", "sampling produced duplicate particle coordinates")
    remaining = set(occupied)
    lattice_components = 0
    while remaining:
        lattice_components += 1
        pending = [remaining.pop()]
        while pending:
            current = pending.pop()
            for k in range(3):
                for direction in (-1, 1):
                    neighbor = list(current)
                    neighbor[k] += direction
                    neighbor = tuple(neighbor)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
    if lattice_components != 1:
        raise VolumeSamplingError("mesh_volume_feature_coverage",
                                  f"one connected source became {lattice_components} disconnected six-neighbor lattice components; use finer spacing")
    minimum_spacing = min((origin[k] + spacing + center[k]) - (origin[k] + center[k]) for k in range(3))
    if minimum_spacing < spacing * (1 - 1e-8):
        raise VolumeSamplingError("mesh_volume_spacing_precision", "world-coordinate precision cannot preserve lattice spacing")
    volume = mesh["metadata"]["signed_volume"]
    represented = len(points) * spacing ** 3
    error = abs(represented - volume) / volume
    if error > MAX_VOLUME_RELATIVE_ERROR:
        raise VolumeSamplingError("mesh_volume_volume_error", f"relative represented volume error {error:.6g} exceeds 0.05; use finer spacing")
    coverage = _source_surface_coverage(mesh, center, points, spacing)
    receipt = {"sampler_version": SAMPLER_VERSION, "source_mesh_sha256": source_hash,
               "lattice_origin": [v + c for v, c in zip(origin, center)], "spacing": spacing,
               "N": len(points), "particle_sha256": _digest(points), "mesh_volume": volume,
               "source_volume_m3": volume, "represented_volume": represented,
               "represented_volume_m3": represented, "relative_volume_error": error,
               "volume_relative_error": error, "reference_density_kg_m3": 1000.0,
               "source_mass_kg": 1000.0 * volume, "represented_mass_kg": 1000.0 * represented,
               "component_coverage": {"source_components": 1, "represented_source_components": 1,
                                      "sampled_lattice_components": lattice_components,
                                      "scope": "six_neighbor_lattice_connectivity; separate_from_surface_coverage"},
               "source_surface_coverage": coverage,
               "minimum_lattice_spacing_m": minimum_spacing,
               "candidate_cells": candidates, "scanlines": counts[u_axis] * counts[v_axis],
               "scan_axis": axis, "bvh_node_visits": work[0], "triangle_queries": work[1],
               "boundary_policy": "half-open ray intervals; midpoint lattice", "conversion": "instant",
               "conservation": "volume", "units": "m", "volume_gate_passed": True}
    result = {"points": points, "receipt": receipt}
    if len(points) <= MAX_CACHE_POINTS:
        _SAMPLE_CACHE[key] = deepcopy(result)
        while len(_SAMPLE_CACHE) > MAX_CACHE_ENTRIES or sum(len(v["points"]) for v in _SAMPLE_CACHE.values()) > MAX_CACHE_POINTS:
            _SAMPLE_CACHE.popitem(last=False)
    return result
