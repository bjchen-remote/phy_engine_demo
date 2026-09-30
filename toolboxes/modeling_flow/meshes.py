"""Dependency-free triangle import and physical mesh eligibility checks.

Topology checks establish a bounded closed oriented triangle mesh. They do not
establish that an image reconstruction is physically correct or free from
geometric self-intersections; those limits are explicit in the receipt.
"""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path

from .contracts import FlowError, MAX_SIMULATION_FACES, MAX_SIMULATION_VERTICES


MAX_DISPLAY_VERTICES = 1_000_000
MAX_DISPLAY_FACES = 2_000_000
MAX_OBJ_BYTES = 256 * 1024 * 1024
MAX_OBJ_LINE_BYTES = 16 * 1024


def _vector(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(type(part) not in (int, float) or not -1e12 <= part <= 1e12
                   or not math.isfinite(part) for part in value)):
        raise FlowError("invalid_mesh", "Mesh vertices must contain three finite coordinates")
    return [float(part) for part in value]


def validate_mesh(mesh: dict, max_vertices=MAX_DISPLAY_VERTICES,
                  max_faces=MAX_DISPLAY_FACES) -> dict:
    if not isinstance(mesh, dict) or set(mesh) != {"vertices", "faces"}:
        raise FlowError("invalid_mesh", "Mesh must contain vertices and triangle faces")
    vertices, faces = mesh["vertices"], mesh["faces"]
    if (not isinstance(vertices, list) or not 4 <= len(vertices) <= max_vertices
            or not isinstance(faces, list) or not 1 <= len(faces) <= max_faces):
        raise FlowError("mesh_budget_exceeded", "Mesh is empty or exceeds the vertex/face import budget")
    normalized = [_vector(vertex) for vertex in vertices]
    normalized_faces = []
    for face in faces:
        if (not isinstance(face, (list, tuple)) or len(face) != 3
                or any(type(index) is not int or not 0 <= index < len(vertices) for index in face)):
            raise FlowError("invalid_mesh", "Each face must contain three in-range integer vertex indices")
        if len(set(face)) != 3:
            raise FlowError("invalid_mesh", "Face repeats a vertex index")
        normalized_faces.append(list(face))
    return {"vertices": normalized, "faces": normalized_faces}


def import_obj(path: str | Path, max_vertices=MAX_DISPLAY_VERTICES,
               max_faces=MAX_DISPLAY_FACES, max_bytes=MAX_OBJ_BYTES) -> dict:
    """Read triangles only; never open mtllib references or run mesh plugins.

    Standard positive and relative negative OBJ indices are accepted. Polygons,
    curves, external includes and homogeneous vertices are rejected because an
    implicit triangulation or coordinate conversion could change the geometry.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= max_bytes:
        raise FlowError("invalid_obj", "OBJ must be a bounded regular file")
    vertices, faces = [], []
    allowed_metadata = {"vt", "vn", "o", "g", "s", "usemtl", "mtllib"}
    try:
        with path.open("r", encoding="utf-8", errors="strict") as handle:
            for raw in handle:
                if len(raw.encode("utf-8")) > MAX_OBJ_LINE_BYTES:
                    raise FlowError("invalid_obj", "OBJ line exceeds the import budget")
                parts = raw.partition("#")[0].split()
                if not parts:
                    continue
                command = parts[0]
                if command == "v":
                    if len(parts) != 4 or len(vertices) >= max_vertices:
                        raise FlowError("invalid_obj", "Only bounded three-coordinate OBJ vertices are supported")
                    try:
                        vertices.append(_vector([float(part) for part in parts[1:]]))
                    except ValueError as error:
                        raise FlowError("invalid_obj", "Invalid OBJ vertex") from error
                elif command == "f":
                    if len(parts) != 4 or len(faces) >= max_faces:
                        raise FlowError("invalid_obj", "Only bounded triangle OBJ faces are supported")
                    triangle = []
                    for token in parts[1:]:
                        fields = token.split("/")
                        if len(fields) > 3 or not fields[0]:
                            raise FlowError("invalid_obj", "Malformed OBJ face index")
                        # Validate ignored uv/normal indices syntactically, but
                        # do not dereference them or import external materials.
                        try:
                            if any(field and int(field) == 0 for field in fields):
                                raise ValueError("zero index")
                            index = int(fields[0])
                        except ValueError as error:
                            raise FlowError("invalid_obj", "OBJ indices must be nonzero integers") from error
                        resolved = index - 1 if index > 0 else len(vertices) + index
                        if not 0 <= resolved < len(vertices):
                            raise FlowError("invalid_obj", "OBJ face refers to an unavailable vertex")
                        triangle.append(resolved)
                    faces.append(triangle)
                elif command not in allowed_metadata:
                    raise FlowError("invalid_obj", "Unsupported OBJ statement: " + command)
    except UnicodeError as error:
        raise FlowError("invalid_obj", "OBJ is not valid UTF-8 text") from error
    return validate_mesh({"vertices": vertices, "faces": faces}, max_vertices, max_faces)


def write_obj(path: Path, mesh: dict) -> None:
    mesh = validate_mesh(mesh)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("# modeling-flow: metres, generated shape; explicit scale and material\n")
        for vertex in mesh["vertices"]:
            handle.write("v " + " ".join(format(value, ".17g") for value in vertex) + "\n")
        for face in mesh["faces"]:
            handle.write("f " + " ".join(str(index + 1) for index in face) + "\n")


def _cross(left, right):
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def _bounds(vertices):
    low = [min(vertex[index] for vertex in vertices) for index in range(3)]
    high = [max(vertex[index] for vertex in vertices) for index in range(3)]
    extent = [high[index] - low[index] for index in range(3)]
    if any(not math.isfinite(value) for value in extent):
        raise FlowError("invalid_mesh", "Mesh coordinate range is not finite")
    return low, high, extent


def scale_mesh(mesh: dict, physical_extent_m: float, axis="max") -> tuple[dict, dict]:
    mesh = validate_mesh(mesh)
    if (type(physical_extent_m) not in (int, float) or not 0 < physical_extent_m <= 10000
            or not math.isfinite(physical_extent_m)):
        raise FlowError("invalid_request", "Physical extent must be finite and positive")
    if axis not in ("max", "x", "y", "z"):
        raise FlowError("invalid_request", "Unsupported physical scale axis")
    low, high, extent = _bounds(mesh["vertices"])
    reference = max(extent) if axis == "max" else extent[("x", "y", "z").index(axis)]
    if not reference > 0:
        raise FlowError("invalid_mesh", "The requested scale axis has zero extent")
    factor = physical_extent_m / reference
    center = [low[index] + extent[index] * 0.5 for index in range(3)]
    vertices = [[(vertex[index] - center[index]) * factor for index in range(3)]
                for vertex in mesh["vertices"]]
    result = validate_mesh({"vertices": vertices, "faces": mesh["faces"]})
    return result, {"units": "m", "scale_axis": axis, "physical_extent_m": physical_extent_m,
                    "source_extent": extent, "source_bbox_center": center, "scale_factor": factor,
                    "origin": "generated_mesh_bounding_box_center",
                    "orientation": "upstream_coordinates; orientation and gravity alignment not inferred"}


def audit_mesh(mesh: dict, simulation: bool = False) -> dict:
    """Audit closure, orientation and volume; do not claim hidden-shape accuracy."""
    mesh = validate_mesh(mesh)
    vertices, faces = mesh["vertices"], mesh["faces"]
    low, high, extent = _bounds(vertices)
    scale = max(extent)
    if not 0 < scale <= 1e6:
        raise FlowError("invalid_mesh", "Mesh has zero or unsupported coordinate extent")
    epsilon_area_squared = (scale ** 2 * 1e-12) ** 2
    edges, directions, seen = Counter(), Counter(), set()
    duplicate_faces = degenerate_faces = 0
    used = set()
    parent = list(range(len(vertices)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    volume = 0.0
    for face in faces:
        key = tuple(sorted(face))
        duplicate_faces += key in seen
        seen.add(key)
        a, b, c = (vertices[index] for index in face)
        ab, ac = ([b[index] - a[index] for index in range(3)],
                  [c[index] - a[index] for index in range(3)])
        cross = _cross(ab, ac)
        degenerate_faces += sum(value * value for value in cross) <= epsilon_area_squared
        # Shift coordinates to bbox center to avoid cancellation at a distant origin.
        origin = [low[index] + extent[index] * 0.5 for index in range(3)]
        a0, b0, c0 = ([vertex[index] - origin[index] for index in range(3)] for vertex in (a, b, c))
        volume += sum(a0[index] * _cross(b0, c0)[index] for index in range(3)) / 6
        used.update(face)
        for start, end in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = (min(start, end), max(start, end))
            edges[edge] += 1
            directions[edge] += 1 if start < end else -1
            parent[find(end)] = find(start)
    boundary = sum(count == 1 for count in edges.values())
    nonmanifold = sum(count > 2 for count in edges.values())
    inconsistent = sum(count == 2 and directions[edge] != 0 for edge, count in edges.items())
    components = len({find(index) for index in used})
    volume_ok = math.isfinite(volume) and abs(volume) > scale ** 3 * 1e-12
    topology_ok = (boundary == nonmanifold == inconsistent == duplicate_faces == degenerate_faces == 0
                   and components == 1 and len(used) == len(vertices) and volume_ok)
    within_budget = len(vertices) <= MAX_SIMULATION_VERTICES and len(faces) <= MAX_SIMULATION_FACES
    return {"vertices": len(vertices), "faces": len(faces), "bbox_min": low, "bbox_max": high,
            "extent": extent, "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "inconsistent_winding_edges": inconsistent, "duplicate_faces": duplicate_faces,
            "degenerate_faces": degenerate_faces, "unreferenced_vertices": len(vertices) - len(used),
            "connected_components": components, "signed_volume": volume, "volume": abs(volume),
            "closed_oriented_single_component": topology_ok,
            "simulation_budget_passed": within_budget,
            "simulation_eligible": topology_ok and within_budget if simulation else False,
            "self_intersections": "not_certified", "physical_accuracy": "not_verified_from_single_image"}


def orient_outward(mesh: dict) -> tuple[dict, bool]:
    """Reverse every face only for an already closed oriented single component."""
    report = audit_mesh(mesh)
    if report["closed_oriented_single_component"] and report["signed_volume"] < 0:
        return {"vertices": mesh["vertices"], "faces": [[a, c, b] for a, b, c in mesh["faces"]]}, True
    return mesh, False
