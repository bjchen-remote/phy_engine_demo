"""Synthetic operation and asset contracts: no real runtime, config or transport."""
from __future__ import annotations

import copy
import json
import json
from pathlib import Path
import sys
import unittest


TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))

from pipeline.operation_contracts import (
    OperationContractError,
    operation_descriptors,
    recovery_for,
    validate_asset_descriptor,
    validate_geometry_binding,
    validate_operation,
    validate_operation_result,
    validate_render_inputs,
)


class ContractAssertions(unittest.TestCase):
    def assertContractError(self, callable_, *args, **kwargs):
        with self.assertRaises(OperationContractError) as captured:
            callable_(*args, **kwargs)
        error = captured.exception
        self.assertIsInstance(error, ValueError)
        self.assertIsInstance(error.code, str)
        self.assertTrue(error.code)
        self.assertIsInstance(error.path, str)
        public = error.as_result()
        self.assertIs(public["ok"], False)
        self.assertEqual(public["code"], error.code)
        self.assertEqual(public["path"], error.path)
        return error


class OperationValidationTests(ContractAssertions):
    def physical(self):
        return {"image_id": "input-image-1", "physical_extent_m": 0.2,
                "material": {"name": "declared-test-material", "density_kg_m3": 1000.0}}

    def test_unknown_operation_and_unavailable_declared_operation_fail(self):
        self.assertContractError(validate_operation, "invented_remote_renderer", {})
        self.assertContractError(validate_operation, "modeling_preview_from_image",
                                 {"image_id": "input-image-1"},
                                 available_operations=["pipeline_capabilities"])

    def test_capability_lookup_has_no_arguments(self):
        validate_operation("pipeline_capabilities", {})
        self.assertContractError(validate_operation, "pipeline_capabilities", {"path": "/tmp/input"})

    def test_liquefaction_requires_one_task_reference_and_explicit_physical_extent(self):
        arguments = {'imported_asset_ref': 'a' * 64, 'physical_extent_m': .2, 'entity_id': 'body'}
        validate_operation('modeling_liquefy', arguments)
        for update in ({'model_ref': 'b' * 64}, {'reference_density_kg_m3': 800},
                       {'preset': 'honey'}, {'conversion': 'melting'}, {'conservation': 'mass'}):
            with self.subTest(update=update):
                self.assertContractError(validate_operation, 'modeling_liquefy', {**arguments, **update})
        missing_scale = {key: value for key, value in arguments.items() if key != 'physical_extent_m'}
        self.assertContractError(validate_operation, 'modeling_liquefy', missing_scale)
        self.assertContractError(validate_operation, 'modeling_liquefy', arguments,
                                 available_operations=['physics_prepare'])

    def test_whole_fluid_patch_is_distinct_from_mesh_patch_and_remove(self):
        operation = {'op': 'replace', 'path': '/entities/@body', 'fluid_ref': 'c' * 64}
        validate_operation('physics_patch', {'operations': [operation]})
        validate_operation('physics_patch', {'operations': [{'op': 'remove', 'path': '/entities/@body'}]})
        for update in ({'mesh_ref': 'd' * 64}, {'value_json': '{}'}, {'op': 'remove'},
                       {'path': '/entities/@body/mesh'}):
            with self.subTest(update=update):
                self.assertContractError(validate_operation, 'physics_patch',
                                         {'operations': [{**operation, **update}]})

    def test_arguments_must_be_an_object(self):
        for invalid in (None, [], "{}", True):
            with self.subTest(arguments=invalid):
                self.assertContractError(validate_operation, "pipeline_capabilities", invalid)

    def test_serialized_legacy_json_rejects_numeric_overflow(self):
        # JSON permits this number's syntax, but Python decodes it as infinity.
        # parse_constant alone does not catch overflow in a numeric token.
        self.assertContractError(validate_operation, "physics_validate",
                                 {"scene_json": '{"world":{"duration":1e999}}'})

    def test_preview_generation_needs_no_material_or_physical_extent(self):
        arguments = {"image_id": "input-image-1"}
        original = copy.deepcopy(arguments)
        validate_operation("modeling_preview_from_image", arguments)
        self.assertEqual(arguments, original)

    def test_preview_generation_rejects_material_and_path_arguments(self):
        for update in ({"material": {"name": "water", "density_kg_m3": 1000}},
                       {"image_path": "/tmp/input.png"}, {"output_dir": "/tmp/output"},
                       {"runtime": "/tmp/runtime"}, {"image_id": "/tmp/input.png"},
                       {"image_id": "https://example.invalid/image.png"}):
            with self.subTest(update=update):
                self.assertContractError(validate_operation, "modeling_preview_from_image",
                                         {"image_id": "input-image-1", **update})

    def test_physical_generation_requires_explicit_scale_and_material(self):
        arguments = self.physical()
        original = copy.deepcopy(arguments)
        validate_operation("modeling_from_image", arguments)
        self.assertEqual(arguments, original)
        for missing in ("physical_extent_m", "material", "image_id"):
            invalid = copy.deepcopy(arguments)
            invalid.pop(missing)
            with self.subTest(missing=missing):
                self.assertContractError(validate_operation, "modeling_from_image", invalid)

    def test_physical_numeric_assumptions_reject_nonfinite_and_boolean_values(self):
        for invalid in (True, False, float("nan"), float("inf"), -float("inf"), 0, -1):
            with self.subTest(field="physical_extent_m", value=invalid):
                arguments = self.physical()
                arguments["physical_extent_m"] = invalid
                self.assertContractError(validate_operation, "modeling_from_image", arguments)
            with self.subTest(field="density_kg_m3", value=invalid):
                arguments = self.physical()
                arguments["material"]["density_kg_m3"] = invalid
                self.assertContractError(validate_operation, "modeling_from_image", arguments)

    def test_sampling_integer_and_float_fields_reject_boolean_and_nonfinite_values(self):
        for field in ("seed", "num_inference_steps", "octree_resolution", "num_chunks", "guidance_scale"):
            for invalid in (True, float("nan"), float("inf")):
                with self.subTest(field=field, value=invalid):
                    self.assertContractError(validate_operation, "modeling_preview_from_image",
                                             {"image_id": "input-image-1", field: invalid})

    def test_preview_render_uses_model_ref_without_simulation_ref(self):
        arguments = {"model_ref": "a" * 64}
        validate_operation("modeling_preview_render", arguments)
        for update in ({"simulation_ref": "b" * 64}, {"model_path": "/tmp/model.json"},
                       {"model_ref": "../other-task/model"}, {"model_ref": "/tmp/model.json"}):
            with self.subTest(update=update):
                self.assertContractError(validate_operation, "modeling_preview_render", {**arguments, **update})

    def test_identifiers_hashes_and_material_names_reject_trailing_controls(self):
        self.assertContractError(validate_operation, "modeling_preview_from_image", {"image_id": "input-image-1\n"})
        self.assertContractError(validate_operation, "modeling_preview_render", {"model_ref": "a" * 64 + "\n"})
        arguments = self.physical()
        arguments["material"]["name"] = "material\n"
        self.assertContractError(validate_operation, "modeling_from_image", arguments)

    def test_preview_camera_fields_reject_boolean_and_nonfinite_values(self):
        for field in ("width", "height", "fps", "duration_seconds", "pitch_degrees", "start_yaw_degrees"):
            for invalid in (True, float("nan"), float("inf")):
                with self.subTest(field=field, value=invalid):
                    self.assertContractError(validate_operation, "modeling_preview_render",
                                             {"model_ref": "a" * 64, field: invalid})

    def test_cleanup_needs_a_model_reference_and_declared_mode(self):
        validate_operation("modeling_preview_cleanup", {"model_ref": "a" * 64})
        self.assertContractError(validate_operation, "modeling_preview_cleanup", {})
        self.assertContractError(validate_operation, "modeling_preview_cleanup",
                                 {"model_ref": "a" * 64, "display_cleanup": "unbounded"})

    def test_unsupported_quality_is_rejected_instead_of_downgraded(self):
        validate_operation("pipeline_configure", {"modeling_quality": "standard",
                           "simulation_quality": "strict", "rendering_quality": "preview"})
        for update in ({"modeling_quality": "high"}, {"rendering_quality": "deep"},
                       {"simulation_quality": True}):
            with self.subTest(update=update):
                self.assertContractError(validate_operation, "pipeline_configure", update)


TASK_ID = "synthetic-task-a"


def asset(*, ref="a" * 64, role="model", sha256="1" * 64,
          purpose="physical_simulation", units="m", frame="scene_world", topology=None):
    value = {"artifact_ref": ref, "schema": "model-asset/1", "sha256": sha256,
             "role": role, "bytes": 256, "task_scope": TASK_ID, "purpose": purpose,
             "units": units, "coordinate_frame": frame,
             "producer": {"id": "synthetic-producer", "version": "0.1.0", "digest": "f" * 64},
             "dependencies": []}
    if topology is not None:
        value["topology_sha256"] = topology
    return value


def dependency(value):
    return {key: value[key] for key in ("artifact_ref", "schema", "sha256")}


class OperationDiscoveryTests(ContractAssertions):
    def test_discovered_schemas_have_no_dangling_local_references(self):
        def walk(value):
            if isinstance(value, dict):
                self.assertNotIn("$ref", value)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(operation_descriptors())

    def test_catalogue_matches_existing_physics_and_composed_operations(self):
        # This is the public package manifest, never a host/account configuration.
        manifest = json.loads((TOOLBOXES / "physics/toolbox.json").read_text(encoding="utf-8"))
        composed = {"pipeline_capabilities", "pipeline_configure", "pipeline_status", "pipeline_render_prepare",
                    "modeling_from_image", "modeling_preview_from_image", "modeling_preview_cleanup",
                    "modeling_preview_render"}
        descriptors = operation_descriptors()
        self.assertEqual(set(descriptors), set(manifest["agent_api"]["operations"]) | composed | {'modeling_liquefy'})
        self.assertEqual(len(descriptors), 28)
        for name, descriptor in descriptors.items():
            with self.subTest(operation=name):
                self.assertEqual(descriptor["name"], name)

    def test_filter_exposes_only_known_available_operations(self):
        selected = ["pipeline_capabilities", "modeling_preview_render", "invented_remote_renderer"]
        descriptors = operation_descriptors(selected)
        self.assertIsInstance(descriptors, dict)
        self.assertEqual(set(descriptors), {"pipeline_capabilities", "modeling_preview_render"})
        self.assertEqual(operation_descriptors([]), {})

    def test_discovery_returns_independent_copies(self):
        descriptors = operation_descriptors(["pipeline_capabilities"])
        pristine = copy.deepcopy(descriptors)
        descriptors["pipeline_capabilities"]["synthetic_mutation"] = True
        self.assertEqual(operation_descriptors(["pipeline_capabilities"]), pristine)

    def test_purpose_does_not_turn_preview_into_physical_execution(self):
        validate_operation("pipeline_capabilities", {}, purpose="model_preview")
        validate_operation("pipeline_capabilities", {}, purpose="physical_simulation")
        self.assertContractError(validate_operation, "modeling_from_image",
                                 OperationValidationTests().physical(), purpose="model_preview")
        self.assertContractError(validate_operation, "modeling_preview_render",
                                 {"model_ref": "a" * 64}, purpose="physical_simulation")


class AssetDescriptorTests(ContractAssertions):
    def test_valid_descriptor_is_copied_without_mutating_input(self):
        value = asset()
        original = copy.deepcopy(value)
        validated = validate_asset_descriptor(value, task_id=TASK_ID)
        self.assertEqual(validated, original)
        self.assertIsNot(validated, value)
        validated["producer"]["id"] = "changed"
        self.assertEqual(value, original)

    def test_current_task_role_digest_units_and_topology_are_enforced(self):
        value = asset(role="simulation_geometry", topology="2" * 64)
        validated = validate_asset_descriptor(value, task_id=TASK_ID,
            expected_role="simulation_geometry", expected_sha256=value["sha256"],
            expected_units="m", expected_topology_sha256=value["topology_sha256"])
        self.assertEqual(validated, value)
        cases = [({"task_id": "synthetic-task-b"}, "asset_scope_mismatch"),
                 ({"expected_sha256": "3" * 64}, "artifact_hash_mismatch"),
                 ({"expected_units": "model_unit"}, "units_mismatch"),
                 ({"expected_topology_sha256": "4" * 64}, "topology_mismatch")]
        for overrides, code in cases:
            with self.subTest(overrides=overrides):
                error = self.assertContractError(validate_asset_descriptor, value,
                                                 **{"task_id": TASK_ID, **overrides})
                self.assertEqual(error.code, code)
        self.assertContractError(validate_asset_descriptor, value, task_id=TASK_ID, expected_role="video")

    def test_each_geometry_role_requires_a_topology_digest(self):
        for role in ("raw_geometry", "display_geometry", "simulation_geometry"):
            with self.subTest(role=role):
                self.assertContractError(validate_asset_descriptor, asset(role=role), task_id=TASK_ID)
                validate_asset_descriptor(asset(role=role, topology="2" * 64), task_id=TASK_ID)

    def test_preview_spatial_assets_need_explicit_units_and_coordinate_frame(self):
        for role in ("model", "raw_geometry", "display_geometry"):
            spatial = asset(role=role, purpose="model_preview", units="model_unit", frame="model_local",
                            topology="2" * 64 if role != "model" else None)
            for update in ({"units": "not_applicable"}, {"coordinate_frame": "not_applicable"}):
                with self.subTest(role=role, update=update):
                    self.assertContractError(validate_asset_descriptor, {**spatial, **update}, task_id=TASK_ID)

    def test_unknown_fields_local_paths_and_invalid_identity_fail(self):
        cases = [{"path": "/tmp/private-model.json"}, {"artifact_ref": "../another-task"},
                 {"artifact_ref": "/tmp/model"}, {"sha256": "not-a-digest"},
                 {"role": "external_command"}, {"units": "cm"},
                 {"coordinate_frame": "unknown_frame"}, {"purpose": "send_to_new_group"}]
        for update in cases:
            with self.subTest(update=update):
                self.assertContractError(validate_asset_descriptor, {**asset(), **update}, task_id=TASK_ID)

    def test_missing_required_descriptor_fields_fail(self):
        for field in asset():
            value = asset()
            value.pop(field)
            with self.subTest(field=field):
                self.assertContractError(validate_asset_descriptor, value, task_id=TASK_ID)

    def test_byte_count_rejects_noninteger_boolean_negative_and_nonfinite(self):
        for invalid in (True, 0, -1, 0.5, float("nan"), float("inf")):
            with self.subTest(bytes=invalid):
                self.assertContractError(validate_asset_descriptor, {**asset(), "bytes": invalid}, task_id=TASK_ID)

    def test_producer_and_dependencies_have_strict_descriptor_shapes(self):
        for producer in ({"id": "test", "version": "0.1.0"},
                         {"id": "test", "version": "0.1.0", "digest": "bad"},
                         {"id": "test", "version": "0.1.0", "digest": "f" * 64, "path": "/tmp/code"}):
            with self.subTest(producer=producer):
                self.assertContractError(validate_asset_descriptor, {**asset(), "producer": producer}, task_id=TASK_ID)
        for dependencies in (None, {}, [{"artifact_ref": "a" * 64, "schema": "model-asset/1"}],
                             [{**dependency(asset()), "path": "/tmp/model"}]):
            with self.subTest(dependencies=dependencies):
                self.assertContractError(validate_asset_descriptor,
                                         {**asset(), "dependencies": dependencies}, task_id=TASK_ID)


class RenderInputTests(ContractAssertions):
    def physical_pair(self):
        model = asset()
        simulation = asset(ref="b" * 64, role="simulation_result", sha256="2" * 64)
        simulation["dependencies"] = [dependency(model)]
        return model, simulation

    def test_physical_render_requires_a_verified_same_model_dependency(self):
        model, simulation = self.physical_pair()
        result = validate_render_inputs(model, simulation, task_id=TASK_ID, purpose="physical_simulation")
        self.assertEqual(result, {"purpose": "physical_simulation", "model": model, "simulation": simulation})
        self.assertIsNot(result["model"], model)
        self.assertIsNot(result["simulation"], simulation)
        error = self.assertContractError(validate_render_inputs, model, None,
                                         task_id=TASK_ID, purpose="physical_simulation")
        self.assertEqual(error.code, "missing_simulation")

    def test_missing_or_wrong_model_dependency_is_rejected(self):
        model, simulation = self.physical_pair()
        for dependencies in ([], [{**dependency(model), "artifact_ref": "c" * 64}],
                             [{**dependency(model), "sha256": "3" * 64}],
                             [{**dependency(model), "schema": "other-model/1"}]):
            invalid = {**simulation, "dependencies": dependencies}
            with self.subTest(dependencies=dependencies):
                error = self.assertContractError(validate_render_inputs, model, invalid,
                                                 task_id=TASK_ID, purpose="physical_simulation")
                self.assertEqual(error.code, "model_dependency_mismatch")

    def test_cross_task_or_units_do_not_silently_retarget_simulation(self):
        model, simulation = self.physical_pair()
        for update, code in (({"task_scope": "synthetic-task-b"}, "asset_scope_mismatch"),
                             ({"units": "model_unit"}, "units_mismatch")):
            with self.subTest(update=update):
                error = self.assertContractError(validate_render_inputs, model, {**simulation, **update},
                                                 task_id=TASK_ID, purpose="physical_simulation")
                self.assertEqual(error.code, code)

    def test_model_preview_accepts_no_simulation_and_keeps_model_units(self):
        model = asset(purpose="model_preview", units="model_unit", frame="model_local")
        result = validate_render_inputs(model, None, task_id=TASK_ID, purpose="model_preview")
        self.assertEqual(result, {"purpose": "model_preview", "model": model, "simulation": None})
        simulation = asset(ref="b" * 64, role="simulation_result", purpose="model_preview",
                           units="model_unit", frame="model_local")
        self.assertContractError(validate_render_inputs, model, simulation,
                                 task_id=TASK_ID, purpose="model_preview")

    def test_wrong_purpose_and_display_asset_cannot_substitute_for_simulation(self):
        model, simulation = self.physical_pair()
        preview = {**model, "purpose": "model_preview"}
        self.assertContractError(validate_render_inputs, preview, simulation,
                                 task_id=TASK_ID, purpose="physical_simulation")
        geometry = {**simulation, "role": "display_geometry", "topology_sha256": "4" * 64}
        self.assertContractError(validate_render_inputs, model, geometry,
                                 task_id=TASK_ID, purpose="physical_simulation")


class GeometryBindingTests(ContractAssertions):
    def pair(self, *, mode="raw_display_subset"):
        source = asset(role="raw_geometry", purpose="model_preview", units="model_unit",
                       frame="model_local", topology="2" * 64)
        target = asset(ref="b" * 64, role="display_geometry", sha256="3" * 64,
                       purpose="model_preview", units="model_unit", frame="model_local", topology="4" * 64)
        binding = {"schema_version": "geometry-binding/1", "mode": mode,
                   "source_ref": source["artifact_ref"], "target_ref": target["artifact_ref"],
                   "source_sha256": source["sha256"], "target_sha256": target["sha256"],
                   "source_topology_sha256": source["topology_sha256"],
                   "target_topology_sha256": target["topology_sha256"],
                   "units": "model_unit", "coordinate_frame": "model_local", "receipt_sha256": "5" * 64}
        return source, target, binding

    def test_subset_and_bounded_surface_bind_exact_assets_without_claiming_geometry_proof(self):
        for mode in ("raw_display_subset", "bounded_surface"):
            source, target, binding = self.pair(mode=mode)
            original = copy.deepcopy((source, target, binding))
            result = validate_geometry_binding(binding, source, target, task_id=TASK_ID)
            self.assertEqual(result, binding)
            self.assertIsNot(result, binding)
            self.assertEqual((source, target, binding), original)

    def test_identity_binding_requires_unchanged_geometry_bytes_and_topology(self):
        source, target, binding = self.pair(mode="identity")
        target.update(sha256=source["sha256"], topology_sha256=source["topology_sha256"])
        binding.update(target_sha256=target["sha256"], target_topology_sha256=target["topology_sha256"])
        validate_geometry_binding(binding, source, target, task_id=TASK_ID)
        changed = {**target, "sha256": "6" * 64}
        self.assertContractError(validate_geometry_binding, {**binding, "target_sha256": changed["sha256"]},
                                 source, changed, task_id=TASK_ID)

    def test_simulation_vertex_mapping_requires_matching_topology(self):
        source = asset(role="simulation_geometry", topology="2" * 64)
        target = asset(ref="b" * 64, role="display_geometry", sha256="3" * 64, topology="2" * 64)
        binding = {"schema_version": "geometry-binding/1", "mode": "simulation_vertices",
                   "source_ref": source["artifact_ref"], "target_ref": target["artifact_ref"],
                   "source_sha256": source["sha256"], "target_sha256": target["sha256"],
                   "source_topology_sha256": source["topology_sha256"],
                   "target_topology_sha256": target["topology_sha256"],
                   "units": "m", "coordinate_frame": "scene_world", "receipt_sha256": "5" * 64}
        validate_geometry_binding(binding, source, target, task_id=TASK_ID)
        changed = {**target, "topology_sha256": "6" * 64}
        error = self.assertContractError(validate_geometry_binding,
            {**binding, "target_topology_sha256": changed["topology_sha256"]}, source, changed, task_id=TASK_ID)
        self.assertEqual(error.code, "topology_mismatch")

    def test_binding_rejects_hash_topology_units_scope_and_reference_mismatch(self):
        source, target, binding = self.pair()
        for field, invalid, code in (("source_sha256", "6" * 64, "artifact_hash_mismatch"),
                                     ("target_sha256", "6" * 64, "artifact_hash_mismatch"),
                                     ("source_topology_sha256", "6" * 64, "topology_mismatch"),
                                     ("target_topology_sha256", "6" * 64, "topology_mismatch"),
                                     ("units", "m", "units_mismatch")):
            with self.subTest(field=field):
                error = self.assertContractError(validate_geometry_binding, {**binding, field: invalid},
                                                 source, target, task_id=TASK_ID)
                self.assertEqual(error.code, code)
        self.assertContractError(validate_geometry_binding, {**binding, "source_ref": "c" * 64},
                                 source, target, task_id=TASK_ID)
        error = self.assertContractError(validate_geometry_binding, binding, source,
                                         {**target, "task_scope": "synthetic-task-b"}, task_id=TASK_ID)
        self.assertEqual(error.code, "asset_scope_mismatch")

    def test_null_topology_does_not_bypass_mesh_identity(self):
        source, target, binding = self.pair()
        self.assertContractError(validate_geometry_binding, {**binding, "source_topology_sha256": None},
                                 source, target, task_id=TASK_ID)

    def test_particle_surface_binding_is_visual_and_has_no_fabricated_triangle_topology(self):
        source = asset(role="sampled_state")
        target = asset(ref="b" * 64, role="video", sha256="3" * 64,
                       units="not_applicable", frame="not_applicable")
        binding = {"schema_version": "geometry-binding/1", "mode": "particle_visual_surface",
                   "source_ref": source["artifact_ref"], "target_ref": target["artifact_ref"],
                   "source_sha256": source["sha256"], "target_sha256": target["sha256"],
                   "source_topology_sha256": None, "target_topology_sha256": None,
                   "units": "m", "coordinate_frame": "scene_world", "receipt_sha256": "5" * 64}
        self.assertEqual(validate_geometry_binding(binding, source, target, task_id=TASK_ID), binding)
        self.assertContractError(validate_geometry_binding, {**binding, "source_topology_sha256": "6" * 64},
                                 source, target, task_id=TASK_ID)

    def test_unadvertised_binding_mode_missing_receipt_and_path_fields_fail(self):
        source, target, binding = self.pair()
        for update in ({"mode": "barycentric"}, {"receipt_sha256": "not-a-hash"},
                       {"path": "/tmp/mapping.json"}, {"coordinate_frame": "scene_world"}):
            with self.subTest(update=update):
                self.assertContractError(validate_geometry_binding, {**binding, **update},
                                         source, target, task_id=TASK_ID)
        for field in binding:
            invalid = copy.deepcopy(binding)
            invalid.pop(field)
            with self.subTest(missing=field):
                self.assertContractError(validate_geometry_binding, invalid, source, target, task_id=TASK_ID)


class OperationResultTests(ContractAssertions):
    def preview_result(self, operation):
        if operation == "modeling_preview_from_image":
            return {"ok": True, "model_ref": "a" * 64, "ready_to_preview": True,
                    "ready_to_simulate": False, "simulation_performed": False}
        return {"ok": True, "result_kind": "model-preview", "model_ref": "a" * 64,
                "video_path": "artifacts/model-preview.mp4"}

    def test_valid_preview_results_remain_display_only_and_are_copied(self):
        for operation in ("modeling_preview_from_image", "modeling_preview_render"):
            result = self.preview_result(operation)
            with self.subTest(operation=operation):
                validated = validate_operation_result(operation, result, purpose="model_preview")
                self.assertEqual(validated, result)
                self.assertIsNot(validated, result)

    def test_preview_cannot_claim_physical_readiness_simulation_or_numerical_use(self):
        for operation in ("modeling_preview_from_image", "modeling_preview_render"):
            for field in ("ready_to_simulate", "simulation_performed", "numerical_usable"):
                with self.subTest(operation=operation, field=field):
                    result = {**self.preview_result(operation), field: True}
                    error = self.assertContractError(validate_operation_result, operation, result,
                                                     purpose="model_preview")
                    self.assertEqual(error.code, "invalid_result")

    def test_preview_nested_verification_cannot_claim_simulation_or_numerical_use(self):
        for operation in ("modeling_preview_from_image", "modeling_preview_render"):
            for field in ("simulation_performed", "numerical_usable", "numerical_passed"):
                with self.subTest(operation=operation, field=field):
                    result = {**self.preview_result(operation), "verification": {field: True}}
                    error = self.assertContractError(validate_operation_result, operation, result,
                                                     purpose="model_preview")
                    self.assertEqual(error.code, "invalid_result")


class RecoveryContractTests(ContractAssertions):
    def test_unknown_delivery_never_authorizes_resend_or_solver_reexecution(self):
        recovery = recovery_for("delivery_unknown")
        self.assertIs(recovery["automatic_retry"], False)
        self.assertIs(recovery["solver_rerun"], False)
        self.assertIs(recovery["resend"], False)
        self.assertIsNone(recovery["next_action"])
        self.assertTrue(recovery["preserve_artifacts"])

    def test_render_and_export_failure_with_checkpoint_recover_only_presentation(self):
        for stage in ("rendering", "data_export"):
            with self.subTest(stage=stage):
                recovery = recovery_for("pipeline_stage_failed", stage=stage, preserved_simulation=True)
                self.assertIs(recovery["automatic_retry"], False)
                self.assertIs(recovery["solver_rerun"], False)
                self.assertIs(recovery["resend"], False)
                self.assertTrue(recovery["requires_verified_checkpoint"])
                self.assertEqual(recovery["next_action"]["tool"], "pipeline_render_prepare")

    def test_missing_checkpoint_incomplete_or_unknown_failure_has_no_automatic_work(self):
        for code, kwargs in (("pipeline_stage_failed", {"stage": "rendering", "preserved_simulation": False}),
                             ("stage_incomplete", {}), ("image_modeling_incomplete", {}),
                             ("invented_failure", {})):
            with self.subTest(code=code, kwargs=kwargs):
                recovery = recovery_for(code, **kwargs)
                self.assertIs(recovery["automatic_retry"], False)
                self.assertIs(recovery["solver_rerun"], False)
                self.assertIs(recovery["resend"], False)
                self.assertIsNone(recovery["next_action"])
                self.assertTrue(recovery["preserve_artifacts"])


class LegacyAdmissionCompatibilityTests(ContractAssertions):
    def test_saved_draft_and_owned_output_arguments_remain_compatible(self):
        for operation in ("physics_validate", "physics_estimate", "physics_prepare"):
            validate_operation(operation, {})
        validate_operation("physics_simulate", {})
        validate_operation("physics_simulate", {"output_dir": "artifacts"})
        validate_operation("physics_simulate", {"output_dir": "work/physics/artifacts"})
        self.assertContractError(validate_operation, "physics_simulate", {"output_dir": "/tmp/another-task"})
        self.assertContractError(validate_operation, "physics_simulate", {"scene_json": "{}", "spec_json": "{}"})
        validate_operation("physics_patch", {"operations": [{"op": "replace", "path": "/entities/@part/mesh",
                                                             "mesh_ref": "a" * 64}]})
        self.assertContractError(validate_operation, "physics_patch", {
            "operations": [{"op": "remove", "path": "/entities/@part/mesh", "mesh_ref": "a" * 64}]})
        self.assertContractError(validate_operation, "physics_patch", {
            "operations": [{"op": "replace", "path": "/world", "mesh_ref": "a" * 64}]})

    def test_operation_schemas_are_formal_draft_2020_12_when_dependency_is_present(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("optional jsonschema dependency unavailable; stdlib validation is tested separately")
        document = json.loads((TOOLBOXES / "pipeline/operation_contracts.json").read_text())
        for schema in document["$defs"].values():
            Draft202012Validator.check_schema(schema)
        for descriptor in operation_descriptors().values():
            for field in ("input_schema", "output_schema"):
                Draft202012Validator.check_schema(descriptor[field])
        schema = operation_descriptors()["modeling_preview_render"]["input_schema"]
        validator = Draft202012Validator(schema)
        validator.validate({"model_ref": "a" * 64})
        self.assertTrue(list(validator.iter_errors({"model_ref": "a" * 64, "simulation_ref": "b" * 64})))


if __name__ == "__main__":
    unittest.main()
