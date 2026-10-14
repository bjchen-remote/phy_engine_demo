"""Explicit, bounded simulation-only reduction; detailed display geometry survives.

Native dependencies are loaded inside operations so importing the host or the
triangle validator never loads a modeling runtime. Component size establishes a
numerical cleanup policy, not recognition of the depicted object's parts.
"""
from __future__ import annotations

import importlib

from .contracts import FlowError
from .meshes import validate_mesh


NUMERICAL_COMPONENT_POLICY = {
    "dominant_min_area_ratio": 0.99,
    "component_max_extent_ratio": 0.001,
    "component_max_area_ratio": 0.000001,
    "component_max_absolute_volume_ratio": 0.000000001,
    "total_max_area_ratio": 0.00001,
    "total_max_absolute_volume_ratio": 0.00000001,
    "require_inside_dominant_bounds": True,
}


def numerical_component_cleanup(mesh: dict) -> tuple[dict, dict]:
    """Remove only bounded micro components, retaining every larger surface.

    Extent, area AND absolute enclosed volume must pass independently. A thin
    large shell or cavity fails the extent/area conditions even when its signed
    volume is small. Components outside the main surface's bounds survive. The
    original display mesh is neither overwritten nor replaced by this result.
    """
    mesh = validate_mesh(mesh)
    try:
        np = importlib.import_module("numpy")
        sparse = importlib.import_module("scipy.sparse")
        graph = importlib.import_module("scipy.sparse.csgraph")
    except (ImportError, OSError) as error:
        raise FlowError("decimator_missing", "Install the pinned numpy/scipy mesh reduction dependencies") from error
    vertices = np.asarray(mesh["vertices"], dtype=np.float64)
    faces = np.asarray(mesh["faces"], dtype=np.int32)
    edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    adjacency = sparse.coo_matrix((np.ones(len(edges), dtype=np.uint8),
                                  (edges[:, 0], edges[:, 1])),
                                 shape=(len(vertices), len(vertices))).tocsr()
    count, labels = graph.connected_components(adjacency, directed=False)
    face_labels = labels[faces[:, 0]]
    cross = np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                     vertices[faces[:, 2]] - vertices[faces[:, 0]])
    area = np.bincount(face_labels, weights=np.linalg.norm(cross, axis=1) * 0.5, minlength=count)
    center = (vertices.min(axis=0) + vertices.max(axis=0)) * 0.5
    shifted = vertices - center
    volumes = np.einsum("ij,ij->i", shifted[faces[:, 0]],
                        np.cross(shifted[faces[:, 1]], shifted[faces[:, 2]])) / 6
    volume = np.bincount(face_labels, weights=volumes, minlength=count)
    lows = np.full((count, 3), np.inf)
    highs = np.full((count, 3), -np.inf)
    np.minimum.at(lows, labels, vertices)
    np.maximum.at(highs, labels, vertices)
    vertex_counts = np.bincount(labels, minlength=count)
    face_counts = np.bincount(face_labels, minlength=count)
    main = int(np.argmax(area))
    total_area, total_volume = float(area.sum()), float(np.abs(volume).sum())
    metadata = {"applied": False, "policy": "bounded_numerical_components/1",
                "thresholds": dict(NUMERICAL_COMPONENT_POLICY), "source_components": int(count),
                "remaining_components": int(count), "removed_components": [],
                "removed_area_ratio": 0.0, "removed_absolute_volume_ratio": 0.0,
                "display_geometry_preserved": True,
                "semantic_classification": "not_verified; thresholds only"}
    if count == 1:
        metadata["reason"] = "single_component"
        return mesh, metadata
    if (not total_area > 0 or not total_volume > 0
            or not np.isfinite(total_area) or not np.isfinite(total_volume)):
        metadata["reason"] = "undefined_component_measure"
        return mesh, metadata
    if area[main] / total_area < NUMERICAL_COMPONENT_POLICY["dominant_min_area_ratio"]:
        metadata["reason"] = "no_dominant_single_surface"
        return mesh, metadata
    main_extent = float((highs[main] - lows[main]).max())
    removed = []
    for component in range(count):
        if component == main or not face_counts[component]:
            continue
        area_ratio = float(area[component] / total_area)
        volume_ratio = float(abs(volume[component]) / total_volume)
        extent_ratio = float((highs[component] - lows[component]).max() / main_extent)
        inside = bool(np.all(lows[component] >= lows[main]) and np.all(highs[component] <= highs[main]))
        if (inside and extent_ratio <= NUMERICAL_COMPONENT_POLICY["component_max_extent_ratio"]
                and area_ratio <= NUMERICAL_COMPONENT_POLICY["component_max_area_ratio"]
                and volume_ratio <= NUMERICAL_COMPONENT_POLICY["component_max_absolute_volume_ratio"]):
            removed.append({"component": int(component), "vertices": int(vertex_counts[component]),
                            "faces": int(face_counts[component]), "bbox_min": lows[component].tolist(),
                            "bbox_max": highs[component].tolist(), "extent_ratio": extent_ratio,
                            "area_m2": float(area[component]), "area_ratio": area_ratio,
                            "signed_volume_m3": float(volume[component]), "absolute_volume_ratio": volume_ratio})
    area_removed = sum(part["area_ratio"] for part in removed)
    volume_removed = sum(part["absolute_volume_ratio"] for part in removed)
    if (area_removed > NUMERICAL_COMPONENT_POLICY["total_max_area_ratio"]
            or volume_removed > NUMERICAL_COMPONENT_POLICY["total_max_absolute_volume_ratio"]):
        metadata["reason"] = "aggregate_cleanup_budget_exceeded"
        return mesh, metadata
    if not removed:
        metadata["reason"] = "no_component_within_all_thresholds"
        return mesh, metadata
    keep = ~np.isin(face_labels, [part["component"] for part in removed])
    remaining_faces = faces[keep]
    used = np.unique(remaining_faces)
    indices = np.full(len(vertices), -1, dtype=np.int32)
    indices[used] = np.arange(len(used), dtype=np.int32)
    result = validate_mesh({"vertices": vertices[used].tolist(), "faces": indices[remaining_faces].tolist()})
    metadata.update(applied=True, remaining_components=int(count - len(removed)),
                    removed_components=removed, removed_area_ratio=area_removed,
                    removed_absolute_volume_ratio=volume_removed,
                    reason="micro_components_removed_with_disclosed_bounds")
    return result, metadata


def topology_decimate(mesh: dict, target_faces: int) -> dict:
    """QEM with topology/normal preservation and original-vertex placements.

    The library's flags are constraints, not proof of geometric validity. The
    caller must independently audit every candidate; the pipeline also runs its
    original engine intersection gate before admitting any simulation asset.
    """
    try:
        np = importlib.import_module("numpy")
        pymeshlab = importlib.import_module("pymeshlab")
    except (ImportError, OSError) as error:
        raise FlowError("decimator_missing", "Install pinned pymeshlab for topology-preserving simulation reduction") from error
    try:
        model = pymeshlab.Mesh(vertex_matrix=np.asarray(mesh["vertices"], dtype=np.float64),
                              face_matrix=np.asarray(mesh["faces"], dtype=np.int32))
        group = pymeshlab.MeshSet()
        group.add_mesh(model)
        group.apply_filter("meshing_decimation_quadric_edge_collapse", targetfacenum=target_faces,
                           targetperc=0.0, qualitythr=0.3, preserveboundary=True,
                           preservenormal=True, preservetopology=True, optimalplacement=False,
                           autoclean=False, selected=False)
        reduced = group.current_mesh()
        return validate_mesh({"vertices": reduced.vertex_matrix().tolist(),
                              "faces": reduced.face_matrix().tolist()})
    except (pymeshlab.PyMeshLabException, ValueError, TypeError) as error:
        raise FlowError("decimation_failed", "Topology-preserving native mesh reduction failed: " + str(error)) from error
