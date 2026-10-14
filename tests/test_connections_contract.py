"""Public connected-mass contracts preserve materials and reject unsafe requests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from physics_demo.api import call_tool
from physics_demo.runner import inspect, prepare, query, simulate, validate
from physics_demo.io.video import _retime_result_document, encode_watchable_mp4, probe_mp4
from physics_demo.io.connections import result_checks as connection_result_checks


ROOT = Path(__file__).resolve().parents[1]


def scene(kind="spring"):
    link = {"id": "link", "type": kind, "entities": ["anchor", "bob"], "rest_length": 1.0}
    if kind == "spring":
        link.update(stiffness=16.0, damping=2.0)
    return {
        "version": 1, "name": "connection-contract",
        "world": {"duration": 0.12, "dt": 0.01, "output_fps": 20, "gravity": [0, -9.81, 0],
                  "bounds": {"min": [-3, 0, -3], "max": [3, 5, 3]}},
        "budget": {"wall_time_s": 30, "backend": "auto", "quality": "preview"},
        "entities": [
            {"id": "anchor", "type": "point_mass", "mass": 1, "fixed": True,
             "position": [0, 1, 0], "velocity": [0, 0, 0]},
            {"id": "bob", "type": "point_mass", "mass": 2, "fixed": False,
             "position": [1.2 if kind == "spring" else 1, 1, 0], "velocity": [0, 0, 0]},
        ],
        "colliders": [], "interactions": {"mutual_gravity": False},
        "connections": [link], "connection_settings": {"substeps": 2, "iterations": 16},
    }


def metric(kind, target="link"):
    return {"id": kind, "type": "series", "metric": {"type": kind, "connection": target}}


class ConnectionContractTests(unittest.TestCase):
    def test_registered_example_enum_matches_discoverable_catalog(self):
        from physics_demo.catalog import EXAMPLE_CATALOG
        tools = json.loads((ROOT / "agent/tools.json").read_text())
        registration = next(tool for tool in tools if tool["name"] == "physics_example")
        advertised = registration["input_schema"]["properties"]["name"]["enum"]
        self.assertEqual(set(advertised), set(EXAMPLE_CATALOG))
        for name in advertised:
            self.assertTrue(call_tool("physics_example", {"name": name})["ok"])

    def test_patch_to_prepare_needs_no_manual_reserialization(self):
        result = call_tool("physics_patch", {"scene_json": json.dumps(scene()), "operations": [
            {"op": "replace", "path": "/connections/@link/stiffness", "value_json": "24"}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(json.loads(result["scene_json"]), result["scene"])
        ready = call_tool("physics_prepare", {"scene_json": result["scene_json"]})
        self.assertTrue(ready["ready_to_simulate"], ready)
        self.assertEqual(ready["scene"]["connections"][0]["stiffness"], 24)

    def test_peak_energy_gate_blocks_a_clean_looking_endpoint(self):
        value = scene()
        value["connections"][0]["damping"] = 0
        value["queries"] = [metric("spring_energy")]
        with tempfile.TemporaryDirectory() as directory:
            summary = simulate(value, directory, make_video=False)
            self.assertTrue(summary["ok"], summary)
            path = Path(directory) / "result.json"
            result = json.loads(path.read_text())
            result["trajectory"]["diagnostics"]["connection_peak_relative_energy_drift"] = .03
            path.write_text(json.dumps(result))
            checked = inspect(directory)
            self.assertFalse(checked["ok"])
            self.assertIn("connection_peak_energy_drift_at_most_0_02", checked["quality_gate"]["failed_checks"])
            self.assertFalse(query(directory)["ok"])

    def test_breakable_scene_round_trips_and_saved_state_cannot_heal(self):
        value = scene()
        value["connections"][0]["break_tensile_strain"] = .1
        value["queries"] = [metric("spring_force"), metric("spring_energy")]
        for backend in ("auto", "native"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                value["budget"]["backend"] = backend
                prepared = prepare(value)
                self.assertTrue(prepared["ready_to_simulate"], prepared)
                self.assertEqual(prepared["scene"]["connections"][0]["break_tensile_strain"], .1)
                summary = simulate(value, directory, make_video=(backend == "native"))
                self.assertTrue(summary["ok"], summary)
                self.assertTrue(inspect(directory)["ok"])
                self.assertTrue(query(directory)["ok"])
                path = Path(directory) / "result.json"
                original = json.loads(path.read_text())
                self.assertEqual(original["trajectory"]["diagnostics"]["connection_break_times_s"], [0.])
                self.assertEqual(original["trajectory"]["frames"][0]["connection_active"], [False])
                changed = copy.deepcopy(original)
                changed["trajectory"]["frames"][0]["connection_active"] = [True]
                path.write_text(json.dumps(changed))
                self.assertFalse(inspect(directory)["ok"])
                changed = copy.deepcopy(original)
                changed["trajectory"]["diagnostics"]["connection_break_times_s"] = [None]
                changed["trajectory"]["diagnostics"]["broken_connection_count"] = 0
                path.write_text(json.dumps(changed))
                self.assertFalse(inspect(directory)["ok"])

    def test_tensile_failure_bounds_and_mixed_route_rejection(self):
        for invalid in (None, True, [], {}, -0.01, 10.01, 10 ** 500):
            with self.subTest(value=repr(invalid)[:20]):
                value = scene()
                value["connections"][0]["break_tensile_strain"] = invalid
                self.assert_rejected(value, "connection_parameter", machine=True)
        for kind in ("rod", "rope"):
            value = scene(kind)
            value["connections"][0]["break_tensile_strain"] = .1
            self.assert_rejected(value, "unsupported_connection_fracture", machine=True)
        mixed = json.loads((ROOT / "examples" / "spring_rigid_pendulum.json").read_text())
        mixed["connections"][0]["break_tensile_strain"] = .1
        self.assert_rejected(mixed, "unsupported_connection_fracture", machine=True)
        with tempfile.TemporaryDirectory() as directory:
            refused = simulate(mixed, directory, make_video=False)
            self.assertFalse(refused["ok"])
            self.assertEqual(refused["stage"], "validate")
            self.assertIn("unsupported_connection_fracture", {item["code"] for item in refused["errors"]})

    def test_public_schema_separates_standalone_failure_from_mixed_links(self):
        schema = json.loads((ROOT / "agent" / "scene-v1.schema.json").read_text())
        root_routes = {item["$ref"] for item in schema["properties"]["connections"]["items"]["anyOf"]}
        self.assertEqual(root_routes, {"#/$defs/connection", "#/$defs/coupled_connection"})
        standalone = schema["$defs"]["connection"]["oneOf"][0]["properties"]
        mixed = schema["$defs"]["coupled_connection"]["oneOf"][0]["properties"]
        self.assertEqual(standalone["break_tensile_strain"]["minimum"], 0)
        self.assertEqual(standalone["break_tensile_strain"]["maximum"], 10)
        self.assertNotIn("break_tensile_strain", mixed)

    def test_dynamic_failure_survives_actual_video_delivery(self):
        value = scene()
        value["world"]["duration"] = .2
        value["entities"][1]["position"] = [1., 1., 0.]
        value["entities"][1]["velocity"] = [1., 0., 0.]
        value["connections"][0]["break_tensile_strain"] = .05
        with tempfile.TemporaryDirectory() as directory:
            summary = simulate(value, directory, make_video=True)
            self.assertTrue(summary["ok"], summary)
            self.assertTrue(inspect(directory)["ok"])
            result = json.loads((Path(directory) / "result.json").read_text())
            frames = result["trajectory"]["frames"]
            self.assertEqual(frames[0]["connection_active"], [True])
            self.assertEqual(frames[-1]["connection_active"], [False])
            self.assertGreater(result["trajectory"]["diagnostics"]["connection_break_times_s"][0], 0)

    def test_slow_motion_state_uses_physical_break_time(self):
        value = scene()
        value["world"].update(duration=.2, output_fps=10)
        value["entities"][1]["position"] = [1., 1., 0.]
        value["entities"][1]["velocity"] = [1., 0., 0.]
        value["connections"][0]["break_tensile_strain"] = .05
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(simulate(value, directory, make_video=False)["ok"])
            original = json.loads((Path(directory) / "result.json").read_text())
            event_time = original["trajectory"]["diagnostics"]["connection_break_times_s"][0]
            self.assertGreater(event_time, .05)
            self.assertLess(event_time, .1)
            retimed, _ = _retime_result_document(original, fps=60, segments=[
                {"physical_start_s": 0., "physical_end_s": .2, "playback_duration_s": 1.}])
            frames = retimed["trajectory"]["frames"]
            self.assertTrue(any(.05 <= frame["t"] < event_time and frame["connection_active"] == [True]
                                for frame in frames))
            self.assertTrue(all(frame["connection_active"] == [frame["t"] + 1e-12 < event_time]
                                for frame in frames))
            self.assertEqual(original["trajectory"]["frames"][0]["connection_active"], [True])
            output = Path(directory) / "slow-motion.mp4"
            metadata = encode_watchable_mp4(Path(directory) / "result.json", output, fps=60,
                segments=[{"physical_start_s": 0., "physical_end_s": .2, "playback_duration_s": 1.}],
                max_bytes=16 * 1024 * 1024)
            self.assertEqual(metadata["presentation"]["sample_count"], 60)
            self.assertEqual(probe_mp4(output)["sample_count"], 60)

    def test_late_break_prebreak_energy_gate_and_failure_witness(self):
        value = scene()
        value["budget"]["validation"] = "visual"
        value["world"]["duration"] = .2
        value["entities"][1]["position"] = [1., 1., 0.]
        value["entities"][1]["velocity"] = [1., 0., 0.]
        value["connections"][0].update(damping=0., break_tensile_strain=.05)
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(simulate(value, directory, make_video=False)["ok"])
            self.assertTrue(inspect(directory)["ok"])
            path = Path(directory) / "result.json"
            original = json.loads(path.read_text())
            diagnostics = original["trajectory"]["diagnostics"]
            self.assertTrue(diagnostics["connection_prebreak_energy_conservation_applicable"])
            self.assertLess(diagnostics["connection_prebreak_peak_relative_energy_drift"], .02)
            self.assertGreater(diagnostics["connection_break_lengths_m"][0], 1.05)
            changed = copy.deepcopy(original)
            changed["trajectory"]["diagnostics"]["connection_prebreak_peak_relative_energy_drift"] = .1
            path.write_text(json.dumps(changed))
            rejected = inspect(directory)
            self.assertFalse(rejected["ok"])
            self.assertIn("connection_prebreak_peak_energy_drift_at_most_0_02",
                          rejected["quality_gate"]["failed_checks"])
            changed = copy.deepcopy(original)
            changed["trajectory"]["diagnostics"]["connection_break_lengths_m"] = [1.0]
            path.write_text(json.dumps(changed))
            self.assertFalse(inspect(directory)["ok"])

    def test_static_trajectory_cannot_fake_a_later_break_below_threshold(self):
        value = scene()
        value["entities"][1]["position"] = [1., 1., 0.]
        value["connections"][0].update(damping=0., break_tensile_strain=.1)
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(simulate(value, directory, make_video=False)["ok"])
            path = Path(directory) / "result.json"
            forged = json.loads(path.read_text())
            diagnostics = forged["trajectory"]["diagnostics"]
            self.assertEqual(diagnostics["connection_break_times_s"], [None])
            diagnostics.update(connection_break_times_s=[.05], connection_break_lengths_m=[1.0],
                               broken_connection_count=1,
                               connection_energy_conservation_applicable=False,
                               connection_prebreak_energy_conservation_applicable=True)
            for frame in forged["trajectory"]["frames"]:
                frame["connection_active"] = [frame["t"] + 1e-12 < .05]
            with self.assertRaisesRegex(ValueError, "witness"):
                connection_result_checks(forged["scene"], forged["plan"], forged["trajectory"])
            path.write_text(json.dumps(forged))
            self.assertFalse(inspect(directory)["ok"])

    def assert_rejected(self, value, code=None, machine=False):
        original = copy.deepcopy(value)
        for tool in ("physics_validate", "physics_prepare"):
            response = call_tool(tool, {"scene_json": json.dumps(value)})
            self.assertFalse(response["ok"], response)
            self.assertTrue(response["errors"], response)
            if code:
                self.assertIn(code, {error["code"] for error in response["errors"]})
            if tool == "physics_prepare":
                self.assertFalse(response.get("ready_to_simulate", False))
        self.assertEqual(value, original, "validation must not mutate the caller's input")
        if machine and Draft202012Validator is not None:
            schema = json.loads((ROOT / "agent" / "scene-v1.schema.json").read_text())
            self.assertFalse(Draft202012Validator(schema).is_valid(value))

    def test_prepare_preserves_all_supported_links_and_exact_runnable_scene(self):
        for kind in ("spring", "rod", "rope"):
            with self.subTest(kind=kind):
                original = scene(kind)
                if kind == "rope":
                    original["entities"][1]["position"][0] = 0.5
                original["queries"] = [metric("connection_length"), metric("connection_extension")]
                if kind == "spring":
                    original["queries"] += [metric("spring_force"), metric("spring_energy")]
                response = call_tool("physics_prepare", {"scene_json": json.dumps(original)})
                self.assertTrue(response["ok"], response)
                self.assertTrue(response["ready_to_simulate"])
                self.assertEqual(response["plan"]["backend"], "connections")
                self.assertEqual(response["scene"]["connections"], original["connections"])
                self.assertEqual(response["scene"]["entities"], original["entities"])
                self.assertEqual(json.loads(response["scene_json"]), response["scene"])
                checked = call_tool("physics_validate", {"scene_json": response["scene_json"]})
                self.assertTrue(checked["ok"], checked)
                self.assertEqual(checked["assumptions"], response["assumptions"])
                self.assertEqual(response["plan"]["measurement_plan"]["sample_interval_s"], 0.01)

    @unittest.skipUnless(Draft202012Validator is not None, "optional jsonschema dependency")
    def test_published_schema_accepts_raw_and_normalized_supported_connections(self):
        schema = json.loads((ROOT / "agent" / "scene-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for kind in ("spring", "rod", "rope"):
            value = scene(kind)
            value["queries"] = [metric("connection_length"), metric("connection_extension")]
            if kind == "spring":
                value["queries"] += [metric("spring_force"), metric("spring_energy")]
                del value["connections"][0]["damping"]
            for candidate in (value, prepare(value)["scene"]):
                with self.subTest(kind=kind, normalized="force_fields" in candidate):
                    self.assertEqual(list(validator.iter_errors(candidate)), [])

    def test_maximum_nodes_and_links_are_explicit_and_not_decimated(self):
        value = scene()
        value["connections"][0]["break_tensile_strain"] = .1
        value["entities"] += [{**copy.deepcopy(value["entities"][1]), "id": f"node-{index}"}
                              for index in range(62)]
        value["connections"] = [{**copy.deepcopy(value["connections"][0]), "id": f"link-{index}"} for index in range(256)]
        accepted = prepare(value)
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(len(accepted["scene"]["entities"]), 64)
        self.assertEqual(len(accepted["scene"]["connections"]), 256)
        value["entities"].append({**copy.deepcopy(value["entities"][-1]), "id": "node-overflow"})
        self.assert_rejected(value, "body_limit", machine=True)
        value["entities"].pop()
        value["connections"].append({**copy.deepcopy(value["connections"][0]), "id": "link-overflow"})
        self.assert_rejected(value, "connection_limit", machine=True)

    def test_malformed_parameters_and_solver_settings_are_not_coerced(self):
        for key, values in {
            "rest_length": (None, True, [], {}, 0, 1001, 10 ** 500),
            "stiffness": (None, True, [], {}, 0, 1e8, 10 ** 500),
            "damping": (None, True, [], {}, -1, 1e6, 10 ** 500),
        }.items():
            for invalid in values:
                with self.subTest(parameter=key, value=repr(invalid)[:20]):
                    value = scene()
                    value["connections"][0][key] = invalid
                    self.assert_rejected(value, "connection_parameter", machine=True)
        for key in ("substeps", "iterations"):
            for invalid in (True, 0, 65, 1.5, None, []):
                value = scene()
                value["connection_settings"][key] = invalid
                with self.subTest(setting=key, value=invalid):
                    self.assert_rejected(value, "connection_settings", machine=True)
        for mass in (1e-10, 1e12 + 1):
            value = scene()
            value["entities"][1]["mass"] = mass
            self.assert_rejected(value, machine=True)

    def test_connection_ids_endpoints_and_fixed_velocity_are_checked(self):
        for targets in (None, [], ["anchor"], ["anchor", "bob", "bob"],
                        ["anchor", "anchor"], ["anchor", "missing"], ["anchor", {}]):
            value = scene()
            value["connections"][0]["entities"] = targets
            self.assert_rejected(value, "connection_target")
        value = scene()
        value["connections"] *= 2
        self.assert_rejected(value, "duplicate_connection_id")
        for invalid in (None, [], "", " " * 2, "x" * 129):
            value = scene()
            value["connections"][0]["id"] = invalid
            self.assert_rejected(value, "connection_id", machine=True)
        value = scene()
        value["entities"][0]["velocity"] = [0.01, 0, 0]
        self.assert_rejected(value, "fixed_velocity", machine=True)
        value = scene()
        value["entities"][1]["fixed"] = True
        self.assert_rejected(value, "fixed_connection")

    def test_initial_rods_and_ropes_are_not_silently_projected(self):
        for kind, positions in (("rod", (0.5, 1.1)), ("rope", (1.1,))):
            for position in positions:
                value = scene(kind)
                value["entities"][1]["position"][0] = position
                self.assert_rejected(value, "initial_connection_length")
        for kind in ("spring", "rod"):
            value = scene(kind)
            value["entities"][1]["position"] = list(value["entities"][0]["position"])
            self.assert_rejected(value, "coincident_connection")

    def test_rod_radial_velocity_is_rejected_while_rope_take_up_is_allowed(self):
        for speed in (-0.2, 0.2):
            value = scene("rod")
            value["entities"][1]["velocity"] = [speed, 0, 0]
            with self.subTest(radial_speed=speed):
                self.assert_rejected(value, "initial_connection_velocity")
                self.assertEqual(prepare(value)["scene"]["entities"][1]["velocity"], [speed, 0, 0])
        for kind, velocity in (("rod", [0, 0.2, 0]), ("rope", [0.2, 0, 0])):
            value = scene(kind)
            value["entities"][1]["velocity"] = velocity
            accepted = prepare(value)
            self.assertTrue(accepted["ok"], accepted)
            self.assertEqual(accepted["scene"]["entities"][1]["velocity"], velocity)

    def test_unsupported_coupling_backend_and_unknown_fields_are_rejected(self):
        mutations = (
            ("connection_backend", lambda s: s["budget"].update(backend="python")),
            ("unsupported_connection_coupling", lambda s: s["interactions"].update(mutual_gravity=True)),
            ("unsupported_connection_coupling", lambda s: s["colliders"].append(
                {"type": "plane", "normal": [0, 1, 0], "offset": 0})),
            ("unknown_field", lambda s: s["connections"][0].update(force=10)),
            ("unknown_field", lambda s: s["connection_settings"].update(soften_material=True)),
        )
        for code, mutate in mutations:
            value = scene()
            mutate(value)
            self.assert_rejected(value, code, machine=True)
        value = scene()
        value.pop("connections")
        self.assert_rejected(value, "connections_required", machine=True)
        for malformed in (None, {}, [], [None], [True], ["spring"]):
            value = scene()
            value["connections"] = malformed
            self.assert_rejected(value, machine=True)

    def test_query_and_force_targets_cannot_confuse_nodes_and_links(self):
        for kind in ("connection_length", "connection_extension", "spring_force", "spring_energy"):
            for target in ("bob", "missing", None, {}):
                value = scene()
                value["queries"] = [metric(kind, target)]
                self.assert_rejected(value, "query_connection")
        for kind in ("rod", "rope"):
            for quantity in ("spring_force", "spring_energy"):
                value = scene(kind)
                value["queries"] = [metric(quantity)]
                self.assert_rejected(value, "query_metric_target")
        field = {"id": "gravity", "type": "uniform", "acceleration": [0, -9.81, 0], "targets": ["bob"]}
        value = scene()
        value["force_fields"] = [field]
        self.assertTrue(prepare(value)["ok"])
        for targets in (["link"], ["missing"], ["bob", "bob"], [[]]):
            value = scene()
            value["force_fields"] = [{**field, "targets": targets}]
            self.assert_rejected(value)
        value = scene()
        value["queries"] = [{"id": "orbit", "type": "nbody_stability", "max_radius": 5, "min_separation": 0.1}]
        self.assert_rejected(value, "stability_requires_closed_nbody")

    def test_resolution_and_budget_failures_preserve_requested_materials(self):
        value = scene()
        value["connections"][0].update(stiffness=3600, damping=30)
        accepted = prepare(value)
        self.assertTrue(accepted["ok"], accepted)
        self.assertGreater(accepted["plan"]["connection_substeps"], value["connection_settings"]["substeps"])
        self.assertEqual(accepted["scene"]["connections"], value["connections"])
        impossible = []
        for change in (lambda s: s["connections"][0].update(stiffness=1e7),
                       lambda s: s["connections"][0].update(damping=1e5),
                       lambda s: s["entities"][1].update(mass=1e-9),
                       lambda s: s["world"].update(duration=30, dt=0.0001)):
            value = scene()
            change(value)
            impossible.append(value)
        heavy = scene("rod")
        heavy["world"].update(duration=30, dt=0.001)
        heavy["connection_settings"].update(substeps=64, iterations=64)
        heavy["connections"] = [{**copy.deepcopy(heavy["connections"][0]), "id": f"rod-{i}"} for i in range(256)]
        impossible.append(heavy)
        for value in impossible:
            self.assertTrue(validate(value)["ok"])
            rejected = prepare(value)
            self.assertFalse(rejected["ok"], rejected)
            self.assertFalse(rejected["ready_to_simulate"])
            self.assertIn("connection_budget_exceeded", {e["code"] for e in rejected["errors"]})
            self.assertEqual(rejected["scene"]["connections"], value["connections"])
            self.assertEqual(rejected["scene"]["entities"], value["entities"])
            with tempfile.TemporaryDirectory() as directory:
                result = simulate(value, Path(directory) / "rejected", make_video=False)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["stage"], "estimate")
        value = scene()
        value["budget"]["wall_time_s"] = 1
        self.assertEqual(prepare(value)["errors"][0]["code"], "budget_infeasible")

    def test_saved_queries_keep_units_scope_and_reject_connection_tampering(self):
        value = scene()
        value["queries"] = [metric(kind) for kind in
                            ("connection_length", "connection_extension", "spring_force", "spring_energy")]
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(value, directory, make_video=False)
            self.assertTrue(result["ok"], result)
            self.assertFalse(result["diagnostics"]["nbody_invariants_applicable"])
            self.assertTrue(inspect(directory)["ok"])
            units = ("m", "m", "N", "J")
            for declaration, unit in zip(value["queries"], units):
                response = call_tool("physics_query", {"result_path": directory, "query_id": declaration["id"]})
                self.assertTrue(response["ok"], response)
                self.assertEqual(response["answers"][0]["unit"], unit)
                self.assertEqual(response["answers"][0]["model_scope"], "ideal_massless_connection_model")
                self.assertEqual(len(response["series"]["times_s"]), 13)
            path = Path(directory) / "result.json"
            original = json.loads(path.read_text())
            changes = (
                lambda t: t["gravity_body_ids"].reverse(),
                lambda t: t["frames"][0]["g"][1].__setitem__(0, 9),
                lambda t: t["frames"][-1]["g"][0].__setitem__(0, 9),
                lambda t: t["diagnostics"].update(max_speed_m_s=-1),
            )
            for change in changes:
                document = copy.deepcopy(original)
                change(document["trajectory"])
                path.write_text(json.dumps(document))
                self.assertFalse(inspect(directory)["ok"])
                self.assertFalse(query(directory)["ok"])


if __name__ == "__main__":
    unittest.main()
