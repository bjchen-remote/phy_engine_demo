"""Trusted local Hunyuan3D runner. No network, prompt shell or CUDA extras."""
from __future__ import annotations

import gc
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .contracts import (FlowError, MAX_IMAGE_PIXELS, MAX_SIMULATION_FACES,
                        MAX_SIMULATION_VERTICES, PREVIEW_REQUEST_SCHEMA, RECEIPT_SCHEMA, RESULT_SCHEMA,
                        artifact, canonical_bytes, digest, file_digest, load_config,
                        load_request, output_directory, verify_foreground_model, verify_weights, write_json)
from .meshes import audit_mesh, import_obj, orient_outward, scale_mesh, validate_mesh, write_obj
from .reduction import numerical_component_cleanup, topology_decimate


ASSUMPTIONS = [
    "Single-image generation estimates the unobserved surfaces; hidden geometry is unverified.",
    "Uniform scale in metres is supplied by the user, not recovered from image pixels.",
    "The declared density assumes a homogeneous filled solid, not hollow walls or measured material.",
    "Mesh topology is checked; self-intersections and CAD tolerances are not certified.",
    "The generated coordinate orientation is preserved; gravity alignment must be chosen in the scene.",
    "A shape-only GLB contains no generated texture or physically measured surface properties.",
]


def _offline_environment():
    # Set before importing torch or Hugging Face modules. In particular, do not
    # turn an unsupported MPS operation into a hidden CPU inference path.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "0"
    os.environ["HY3DGEN_DEBUG"] = "0"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def _verify_upstream(config: dict) -> dict:
    root = Path(config["upstream_root"])
    package_name = "hy3dmlx" if config["backend"] == "hunyuan3d_mlx" else "hy3dgen"
    package = root / package_name
    if not package.is_dir() or not package.resolve().is_relative_to(root):
        raise FlowError("runtime_not_installed", "Pinned upstream " + package_name + " package is missing")
    try:
        actual = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                capture_output=True, text=True, timeout=10).stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                                 "--untracked-files=all", "--", package_name], check=True,
                                capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise FlowError("upstream_pin_unverifiable", "Cannot verify the operator-installed upstream Git pin") from error
    if actual != config["upstream_commit"] or status:
        raise FlowError("upstream_pin_mismatch", "Upstream commit differs from its pin or its runtime package has local changes")
    repository = ("https://github.com/ZimengXiong/Hunyuan3D-Swift" if config["backend"] == "hunyuan3d_mlx"
                  else "https://github.com/Tencent-Hunyuan/Hunyuan3D-2")
    return {"repository": repository, "commit": actual,
            "package_root": str(package), "working_tree_verified": True}


def _checkpoint_header(config: dict) -> dict:
    """Verify that the safetensors file includes the offline image conditioner.

    from_single_file silently skips conditioner loading if its keys are absent.
    This adapter explicitly rejects that path instead of using random weights.
    """
    path = Path(config["weights_root"]) / config["model_subfolder"] / "model.fp16.safetensors"
    try:
        with path.open("rb") as handle:
            size = int.from_bytes(handle.read(8), "little")
            if not 2 <= size <= 16 * 1024 * 1024 or size + 8 >= path.stat().st_size:
                raise ValueError("invalid header size")
            header = json.loads(handle.read(size).decode("utf-8"))
        if not isinstance(header, dict):
            raise ValueError("invalid header object")
        counts = {prefix: sum(key.startswith(prefix + ".") for key in header)
                  for prefix in ("model", "vae", "conditioner")}
        if not all(counts.values()):
            raise FlowError("incomplete_checkpoint", "Checkpoint must include model, VAE and image conditioner weights")
        return {"tensor_groups": counts, "format": "safetensors"}
    except (OSError, ValueError, UnicodeError) as error:
        if isinstance(error, FlowError):
            raise
        raise FlowError("invalid_checkpoint", "Cannot read the pinned safetensors checkpoint header") from error


def _versions() -> dict:
    packages = ("mlx", "mlx-metal", "onnxruntime", "flatbuffers", "protobuf", "torch",
                "torchvision", "diffusers", "transformers", "safetensors", "numpy",
                "opencv-python", "PyYAML", "Pillow", "trimesh", "scikit-image", "scipy",
                "pymeshlab", "fast-simplification", "imageio", "tifffile", "networkx",
                "lazy-loader", "packaging", "sympy", "mpmath")
    result = {}
    for name in packages:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def _load_runtime(config: dict):
    _offline_environment()
    upstream = _verify_upstream(config)
    root = Path(config["upstream_root"])
    if config["backend"] == "hunyuan3d_mlx":
        loaded = sys.modules.get("hy3dmlx")
        if loaded is not None and Path(loaded.__file__).resolve().parent != root / "hy3dmlx":
            raise FlowError("runtime_pin_mismatch", "Process already imported another hy3dmlx installation")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            mx = importlib.import_module("mlx.core")
            module = importlib.import_module("hy3dmlx.pipeline")
            trimesh = importlib.import_module("trimesh")
            importlib.import_module("PIL.Image")
        except (ImportError, OSError, RuntimeError, ValueError, AttributeError) as error:
            raise FlowError("runtime_dependency_missing", "MLX shape dependency is missing or incompatible: " + str(error)) from error
        if not Path(module.__file__).resolve().is_relative_to(root / "hy3dmlx"):
            raise FlowError("runtime_pin_mismatch", "Loaded MLX pipeline is outside the pinned source")
        if not mx.metal.is_available():
            raise FlowError("metal_unavailable", "Apple Metal is required; MLX CPU fallback is not enabled")
        mx.set_default_device(mx.gpu)
        return mx, module.Hunyuan3DShapePipeline, trimesh, upstream
    loaded = sys.modules.get("hy3dgen")
    if loaded is not None and Path(loaded.__file__).resolve().parent != root / "hy3dgen":
        raise FlowError("runtime_pin_mismatch", "Process already imported another hy3dgen installation")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        torch = importlib.import_module("torch")
        module = importlib.import_module("hy3dgen.shapegen")
        trimesh = importlib.import_module("trimesh")
        importlib.import_module("PIL.Image")
    except (ImportError, OSError, RuntimeError, ValueError, AttributeError) as error:
        raise FlowError("runtime_dependency_missing", "Shape runtime dependency is missing or incompatible: " + str(error)) from error
    if not Path(module.__file__).resolve().is_relative_to(root / "hy3dgen"):
        raise FlowError("runtime_pin_mismatch", "Loaded shape pipeline is outside the pinned upstream source")
    if config["device"] == "mps":
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is None or not mps.is_built() or not mps.is_available():
            raise FlowError("mps_unavailable", "MPS is required by configuration but is unavailable; CPU is an explicit operator choice")
    if config["device"] == "mps" and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") != "0":
        raise FlowError("runtime_policy_mismatch", "MPS CPU fallback must be disabled")
    return torch, module.Hunyuan3DDiTFlowMatchingPipeline, trimesh, upstream


def health(config_source: str | Path | dict) -> dict:
    try:
        config = load_config(config_source)
        inventory = verify_weights(config)
        foreground_model = verify_foreground_model(config)
        if foreground_model is not None:
            try:
                ort = importlib.import_module("onnxruntime")
                if "CPUExecutionProvider" not in ort.get_available_providers():
                    raise ImportError("ONNX CPU execution provider is unavailable")
            except (ImportError, OSError, RuntimeError) as error:
                raise FlowError("foreground_dependency_missing", "Install compatible offline onnxruntime for U2-Net") from error
        header = _checkpoint_header(config)
        decimator = _verify_decimator_runtime()
        _, _, _, upstream = _load_runtime(config)
        return {"ok": True, "schema_version": RESULT_SCHEMA, "operation": "health",
                "runtime_ready": True, "inference_verified": False,
                "backend": config["backend"], "device": config["device"], "dtype": config["dtype"],
                "config_sha256": digest(config), "upstream": upstream,
                "foreground_model": foreground_model,
                "background_removal": config["background_removal"],
                "decimator": decimator,
                "checkpoint": {"id": config["checkpoint_id"], "revision": config["checkpoint_revision"],
                               "inventory": inventory, **header}, "dependencies": _versions(),
                "capabilities": ["image_to_shape", "shape_glb", "bounded_triangle_obj", "offline_only"],
                "simulation_limits": {"max_vertices": MAX_SIMULATION_VERTICES,
                                      "max_faces": MAX_SIMULATION_FACES},
                "limitations": ASSUMPTIONS + ["Health checks do not load the model or certify a successful inference."]}
    except FlowError as error:
        return _failure(error)
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        return _failure(FlowError("local_runtime_error", "Cannot verify local modeling runtime: " + str(error)))


def _failure(error: FlowError, **details) -> dict:
    return {"ok": False, "schema_version": RESULT_SCHEMA, "code": error.code,
            "error": str(error), "ready_to_simulate": False, **details}


def _verify_decimator_runtime() -> dict:
    """Check local reduction imports before expensive image inference."""
    try:
        importlib.import_module("numpy")
        importlib.import_module("scipy.sparse")
        importlib.import_module("scipy.sparse.csgraph")
        native = importlib.import_module("pymeshlab")
        if not callable(getattr(native, "Mesh", None)) or not callable(getattr(native, "MeshSet", None)):
            raise ImportError("PyMeshLab mesh API is unavailable")
    except (ImportError, OSError, RuntimeError, ValueError, AttributeError) as error:
        raise FlowError("decimator_missing", "Install compatible pinned pymeshlab/numpy/scipy for local mesh reduction") from error
    return {"ready": True, "method": "topology_preserving_quadric_edge_collapse",
            "package": "pymeshlab", "geometry_validation_verified": False}


def _load_image(request: dict, config: dict | None = None):
    try:
        from PIL import Image, ImageOps
        with Image.open(request["image_path"]) as original:
            if original.format not in ("PNG", "JPEG", "WEBP"):
                raise FlowError("invalid_image", "Decoded image format is unsupported")
            if original.width * original.height > MAX_IMAGE_PIXELS or min(original.size) < 2:
                raise FlowError("invalid_image", "Decoded image dimensions exceed the modeling budget")
            if getattr(original, "n_frames", 1) != 1:
                raise FlowError("invalid_image", "Animated images must be converted to a single still image")
            image = ImageOps.exif_transpose(original).convert("RGBA")
            image.load()
    except FlowError:
        raise
    except (OSError, ValueError) as error:
        raise FlowError("invalid_image", "Cannot decode the supplied image") from error
    alpha_range = image.getchannel("A").getextrema()
    if alpha_range[1] == 0:
        raise FlowError("invalid_image", "Image foreground is fully transparent")
    metadata = {"width": image.width, "height": image.height,
                "foreground_mask": "provided_alpha" if alpha_range[0] < 255 else "full_image",
                "background_removal": "not_performed", "exif_orientation_applied": True}
    if config is not None and config["background_removal"] == "u2net" and alpha_range[0] == 255:
        from .segmentation import remove_foreground
        pin = config["foreground_model"]
        image, segmentation = remove_foreground(image, Path(config["weights_root"]) / pin["path"], pin["sha256"])
        metadata.update(segmentation)
    return image, metadata


def _mesh_arrays(mesh) -> dict:
    try:
        vertices, faces = mesh.vertices.tolist(), mesh.faces.tolist()
    except AttributeError as error:
        raise FlowError("invalid_generated_mesh", "Upstream did not return a triangle mesh") from error
    return validate_mesh({"vertices": vertices, "faces": faces})


def _decimate(mesh: dict, trimesh, request: dict) -> tuple[dict, dict]:
    original = audit_mesh(mesh)
    if original["simulation_budget_passed"] and original.get("connected_components", 1) == 1:
        return mesh, {"applied": False, "method": None}
    if not request["allow_decimation"]:
        if original["simulation_budget_passed"]:
            raise FlowError("simulation_mesh_rejected", "Disconnected mesh requires geometry cleanup and decimation is disabled")
        raise FlowError("simulation_mesh_budget_exceeded", "Generated mesh exceeds simulation budget and decimation is disabled")
    cleanup = {"applied": False, "reason": "single_component", "display_geometry_preserved": True}
    if (original.get("connected_components", 1) > 1
            and all(original.get(key, 0) == 0 for key in ("boundary_edges", "nonmanifold_edges",
                "inconsistent_winding_edges", "duplicate_faces", "unreferenced_vertices"))):
        mesh, cleanup = numerical_component_cleanup(mesh)
    prepared = audit_mesh(mesh) if cleanup["applied"] else original
    attempts = []
    metadata = {"applied": False, "reduction_required": not prepared["simulation_budget_passed"],
                "method": "topology_preserving_quadric_edge_collapse",
                "source_vertices": original["vertices"], "source_faces": original["faces"],
                "geometry_error_bound": "not_certified", "numerical_cleanup": cleanup,
                "constraints": {"preserve_topology": True, "preserve_normal": True,
                                "preserve_boundary": True, "optimal_placement": False,
                                "automatic_cleaning": False},
                "attempts": attempts}
    if prepared.get("connected_components", 1) > 1:
        raise FlowError("simulation_mesh_rejected",
                        "Significant disconnected surfaces remain; they cannot be silently deleted or connected",
                        {**metadata, "source_audit": original, "prepared_audit": prepared})
    if prepared["simulation_budget_passed"]:
        return mesh, {**metadata, "applied": False, "method": None}
    try:
        importlib.import_module("pymeshlab")
    except (ImportError, OSError) as error:
        raise FlowError("decimator_missing", "Install pinned pymeshlab in the local runtime to reduce this mesh", metadata) from error
    # Every attempt starts from the same simulation source. Six thousand faces
    # leaves headroom for the engine's bounded intersection audit. Original
    # vertex placements avoid QEM's unconstrained spikes in flat regions.
    targets = (6000, min(MAX_SIMULATION_FACES, 2 * (MAX_SIMULATION_VERTICES - 2)), 4000)
    for target in targets:
        try:
            metadata["applied"] = True
            candidate = topology_decimate(mesh, target)
            candidate, transform = scale_mesh(candidate, request["physical_extent_m"], request["scale_axis"])
            candidate, reversed_winding = orient_outward(candidate)
            report = audit_mesh(candidate, simulation=True)
            topology_preserved = (report.get("euler_characteristic") == prepared.get("euler_characteristic")
                                  and report["connected_components"] == prepared.get("connected_components", 1))
            attempts.append({"target_faces": target, "audit": report,
                             "scale_transform": transform, "winding_reversed": reversed_winding,
                             "source_euler_characteristic": prepared.get("euler_characteristic"),
                             "topology_preserved": topology_preserved})
            if report["simulation_eligible"] and topology_preserved:
                return candidate, {**metadata, "target_faces": target, "scale_transform": transform}
        except (FlowError, ValueError, TypeError, RuntimeError) as error:
            attempts.append({"target_faces": target, "code": getattr(error, "code", "decimation_failed"),
                             "error": str(error)})
    raise FlowError("simulation_mesh_rejected", "No bounded decimation candidate passed the mesh gate", metadata)


def _model_mesh(torch, pipeline_class, config: dict, request: dict, image):
    try:
        if config["backend"] == "hunyuan3d_mlx":
            pipeline = pipeline_class.from_pretrained(
                str(Path(config["weights_root"]) / config["model_subfolder"]),
                dtype=getattr(torch, config["dtype"]), quantize=None, verbose=False)
            # The MLX port's load_image accepts PIL objects. Passing the decoded
            # still applies our bounded decoder and EXIF normalization once.
            return pipeline.generate(image, num_inference_steps=request["num_inference_steps"],
                                     guidance_scale=request["guidance_scale"], seed=request["seed"],
                                     octree_resolution=request["octree_resolution"],
                                     num_chunks=request["num_chunks"], octree_decode=False,
                                     compile_dit=False, verbose=False)
        pipeline = pipeline_class.from_pretrained(
            config["weights_root"], subfolder=config["model_subfolder"], device=config["device"],
            dtype=getattr(torch, config["dtype"]), use_safetensors=True, variant=config["variant"])
        generator = torch.Generator(device="cpu").manual_seed(request["seed"])
        meshes = pipeline(image=image, num_inference_steps=request["num_inference_steps"],
                          guidance_scale=request["guidance_scale"], generator=generator,
                          octree_resolution=request["octree_resolution"], num_chunks=request["num_chunks"],
                          mc_algo="mc", output_type="trimesh", enable_pbar=False)
        if not isinstance(meshes, (list, tuple)) or len(meshes) != 1 or meshes[0] is None:
            raise FlowError("invalid_generated_mesh", "Expected exactly one generated shape")
        return meshes[0]
    except FlowError:
        raise
    except (RuntimeError, ValueError, OSError, ImportError, KeyError) as error:
        raise FlowError("image_inference_failed", "Local shape inference failed without a fallback: " + str(error)) from error
    finally:
        # An invocation is a dedicated subprocess in QQ. This also releases its
        # model references promptly when the API is used directly for development.
        if "pipeline" in locals():
            del pipeline
        gc.collect()
        if config["backend"] == "hunyuan3d_mlx" and hasattr(torch, "clear_cache"):
            torch.clear_cache()
        if config["device"] == "mps" and hasattr(torch, "mps"):
            torch.mps.empty_cache()


def generate(config_source: str | Path | dict, request_source: str | Path | dict,
             output: str | Path) -> dict:
    started = time.monotonic()
    try:
        config, request = load_config(config_source), load_request(request_source)
        preview = request["schema_version"] == PREVIEW_REQUEST_SCHEMA
        units = "model_unit" if preview and "physical_extent_m" not in request else "m"
        assumptions = (["AI-generated shape; hidden surfaces are inferred from one image.",
                        "No physical simulation, density or mass is computed for this model preview.",
                        "Shape-only assets have no generated texture or measured surface properties.",
                        "Dimensions are explicitly supplied." if units == "m" else
                        "Extent is normalized to one model unit; no physical size is inferred."]
                       if preview else ASSUMPTIONS)
        inventory = verify_weights(config)
        verify_foreground_model(config)
        header = _checkpoint_header(config)
        if request["allow_decimation"]:
            _verify_decimator_runtime()
        torch, pipeline_class, trimesh, upstream = _load_runtime(config)
        image, preprocessing = _load_image(request, config)
        image_sha256 = file_digest(Path(request["image_path"]))
        model = _model_mesh(torch, pipeline_class, config, request, image)
        generated = _mesh_arrays(model)
        mesh, scale = scale_mesh(generated, request.get("physical_extent_m", 1.0), request["scale_axis"])
        if units == "model_unit":
            scale = {key: value for key, value in scale.items() if key != "physical_extent_m"}
            scale.update(units=units, normalized_extent=1.0, physical_extent_m=None)
        mesh, flipped = orient_outward(mesh)
        raw_mesh = mesh
        raw_audit = audit_mesh(raw_mesh)
        cleanup = None
        if preview:
            from .display_cleanup import clean_display_mesh
            mesh, cleanup = clean_display_mesh(raw_mesh, request["display_cleanup"])
            assumptions.append("Display cleanup removes only bounded detached micro-components; the normalized original mesh is retained.")
        display_audit = audit_mesh(mesh)
        # Only now create output. Dependency, image and inference failures leave
        # no misleading successful artifacts behind.
        root = output_directory(output)
        display_obj, display_glb = root / "display.obj", root / "display.glb"
        write_obj(display_obj, mesh, units)
        display_model = trimesh.Trimesh(vertices=mesh["vertices"], faces=mesh["faces"], process=False)
        display_model.export(str(display_glb), file_type="glb")
        display_json = root / "display_mesh.json"
        write_json(display_json, {"schema_version": "modeling-flow-display/1", "units": units, **mesh,
                                 "scale": scale, "audit": display_audit,
                                 "image_sha256": image_sha256,
                                 "geometry_origin": "generated_single_image_approximation",
                                 "physical_accuracy": "unverified", "assumptions": assumptions})
        raw_artifacts = []
        if preview:
            raw_obj, raw_glb, raw_json = root / "raw.obj", root / "raw.glb", root / "raw_mesh.json"
            write_obj(raw_obj, raw_mesh, units)
            trimesh.Trimesh(vertices=raw_mesh["vertices"], faces=raw_mesh["faces"], process=False).export(str(raw_glb), file_type="glb")
            write_json(raw_json, {"schema_version": "modeling-flow-display/1", "units": units, **raw_mesh,
                                 "scale": scale, "audit": raw_audit, "image_sha256": image_sha256,
                                 "geometry_origin": "generated_single_image_approximation",
                                 "geometry_scope": "normalized_pre_cleanup", "physical_accuracy": "unverified",
                                 "assumptions": assumptions})
            write_json(root / "cleanup-receipt.json", cleanup)
            raw_artifacts = [artifact(raw_glb, root, "raw_glb", "model/gltf-binary"),
                             artifact(raw_obj, root, "raw_obj", "model/obj"),
                             artifact(raw_json, root, "raw_mesh_data", "application/json"),
                             artifact(root / "cleanup-receipt.json", root, "cleanup_receipt", "application/json")]
        # Round-trip through the bounded importer before a mesh reaches physics.
        verified = import_obj(display_obj)
        rejection = None
        decimation = {"applied": False, "method": None}
        simulation_audit = audit_mesh(verified, simulation=True)
        try:
            if preview:
                raise FlowError("display_only_requested", "Physical mesh preparation was not requested")
            simulation_mesh, decimation = _decimate(verified, trimesh, request)
            simulation_mesh, simulation_flipped = orient_outward(simulation_mesh)
            simulation_audit = audit_mesh(simulation_mesh, simulation=True)
            if not simulation_audit["simulation_eligible"]:
                raise FlowError("simulation_mesh_rejected", "Mesh does not pass closure, orientation, volume and simulation budget checks")
        except FlowError as error:
            rejection = error
            if error.details:
                decimation = error.details
            simulation_mesh = None
            simulation_flipped = False
        artifacts = [artifact(display_glb, root, "display_mesh", "model/gltf-binary"),
                     artifact(display_obj, root, "display_mesh_source", "model/obj"),
                     artifact(display_json, root, "display_mesh_data", "application/json")] + raw_artifacts
        result = {"schema_version": RESULT_SCHEMA, "ready_to_simulate": rejection is None,
                  "display_only": preview, "ready_to_preview": preview,
                  "display_mesh_path": str(display_glb), "display_obj_path": str(display_obj),
                  "display_mesh_json_path": str(display_json),
                  "receipt_path": str(root / "receipt.json"), "audit": simulation_audit,
                  "assumptions": assumptions, "artifacts": artifacts}
        if simulation_mesh is not None:
            physics_obj, physics_json = root / "simulation.obj", root / "simulation_mesh.json"
            write_obj(physics_obj, simulation_mesh)
            physics = {"schema_version": "modeling-flow-mesh/1", "units": "m", **simulation_mesh,
                       "material": request["material"],
                       "scale": {**scale, "simulation_decimation_rescale": decimation.get("scale_transform")},
                       "audit": simulation_audit, "image_sha256": image_sha256,
                       "geometry_origin": "generated_single_image_approximation",
                       "physical_accuracy": "unverified", "assumptions": ASSUMPTIONS}
            write_json(physics_json, physics)
            artifacts.extend([artifact(physics_obj, root, "simulation_mesh", "model/obj"),
                              artifact(physics_json, root, "simulation_mesh_data", "application/json")])
            result.update(simulation_mesh_path=str(physics_obj), simulation_mesh_json_path=str(physics_json),
                          simulation_mesh_sha256=digest(simulation_mesh),
                          mass_estimate_kg=simulation_audit["volume"] * request["material"]["density_kg_m3"])
        receipt = {"schema_version": RECEIPT_SCHEMA, "backend": config["backend"],
                   "upstream": upstream,
                   "checkpoint": {"id": config["checkpoint_id"], "revision": config["checkpoint_revision"],
                                  "inventory": inventory, **header},
                   "configuration_sha256": digest(config),
                   "configuration_file_sha256": (file_digest(Path(config_source))
                        if isinstance(config_source, (str, Path)) else None),
                   "request_sha256": digest(request),
                   "image": {"sha256": image_sha256, "bytes": Path(request["image_path"]).stat().st_size,
                             **preprocessing}, "sampling": {key: request[key] for key in
                   ("seed", "num_inference_steps", "guidance_scale", "octree_resolution", "num_chunks")},
                   "execution": {"device": config["device"], "dtype": config["dtype"],
                                 "checkpoint_storage_variant": config["variant"],
                                 "network": "offline_only", "mps_cpu_fallback": False,
                                 "mlx_quantization": None, "compiled_dit": False,
                                 "texture_generation": False, "flashvdm": False,
                                 "marching_cubes": "skimage_mc", "dependencies": _versions()},
                   "purpose": "model_preview" if preview else "physics_geometry",
                   "units": units, "scale": scale, "material": request.get("material"),
                   "mass_estimate_kg": (simulation_audit["volume"] * request["material"]["density_kg_m3"]
                                        if rejection is None else None),
                   "display_audit": display_audit, "simulation_audit": simulation_audit,
                   "decimation": decimation, "display_winding_reversed": flipped,
                   "simulation_winding_reversed": simulation_flipped,
                   "ready_to_simulate": rejection is None, "display_only": preview,
                   "assumptions": assumptions,
                   "artifacts": artifacts, "elapsed_seconds": time.monotonic() - started}
        if rejection is not None and not preview:
            receipt["simulation_rejection"] = {"code": rejection.code, "error": str(rejection)}
        if preview:
            receipt.update(simulation_preparation="not_requested", simulation_performed=False,
                           numerical_usable=False, mass_estimate_kg=None,
                           raw_audit=raw_audit, raw_geometry_preserved=True,
                           display_geometry_modified=cleanup["applied"],
                           display_cleanup={key: cleanup[key] for key in ("mode", "applied", "policy_id", "summary")})
        write_json(root / "receipt.json", receipt)
        result["artifacts"] = artifacts + [artifact(root / "receipt.json", root, "modeling_receipt", "application/json")]
        if preview:
            return {"ok": True, **result}
        if rejection is not None:
            return _failure(rejection, **{key: value for key, value in result.items()
                                         if key not in ("schema_version", "ready_to_simulate")})
        return {"ok": True, **result}
    except FlowError as error:
        return _failure(error)
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        return _failure(FlowError("local_runtime_error", "Local modeling runtime failed: " + str(error)))
