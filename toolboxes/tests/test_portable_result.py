"""Synthetic saved-evidence privacy contract; no runtime or messaging access."""
import copy
import io
import json
import os
from pathlib import Path
import posixpath
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "toolboxes"))
ENGINE = Path(os.environ.get("PHYSICS_PIPELINE_PHYSICS_ROOT", str(ROOT / "toolboxes/physics")))
sys.path[:0] = [str(ENGINE / "physics_release"), str(ENGINE)]

from pipeline_presentation import PresentationError, export_data
from pipeline_presentation.artifacts import canonical, digest
from pipeline_presentation.portable_result import (PORTABLE_SCHEMA,
                                                  PROJECTION_ALGORITHM,
                                                  verify_portable_data)
from physics_demo.runner import load_scene, simulate

PRIVATE = "/Users/synthetic/path"
DIAGNOSTIC = "https://example.invalid/diagnostic compiler failed at /Users/synthetic/path/solver.c:18; logs file:///Users/synthetic/path/log.txt; cache /scratch/synthetic/cache; input file:/Users/synthetic/path/input.obj; cache FILE:///Users/synthetic/path/cache.obj"


def glb(document, *, escaped_slashes=False):
    data = json.dumps(document, ensure_ascii=True, allow_nan=False).encode("utf-8")
    if escaped_slashes:
        data = data.replace(b"/", b"\\u002f")
    data += b" " * ((-len(data)) % 4)
    return struct.pack("<4sII", b"glTF", 2, 20 + len(data)) + struct.pack("<I4s", len(data), b"JSON") + data


class PortableResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name)
        cls.seed = cls.root / "seed"
        scene = load_scene(ROOT / "examples/three_body.json")
        scene["world"].update(duration=.03, dt=.005, output_fps=20)
        scene["budget"]["validation"] = "visual"
        scene["queries"] = [{"id": "x", "type": "series", "metric": {
            "type": "centroid", "entity": scene["entities"][0]["id"], "axis": "x"}}]
        checked = simulate(scene, cls.seed, 30, make_video=False)
        if not checked["ok"]:
            raise AssertionError(checked)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def source(self, root, stage=True):
        source = root / "source"
        source.mkdir()
        for path in self.seed.iterdir():
            if path.is_file():
                (source / path.name).write_bytes(path.read_bytes())
        result = json.loads((source / "result.json").read_bytes())
        for key, value in result["artifacts"].items():
            if isinstance(value, str):
                result["artifacts"][key] = str(source / Path(value).name)
        result["metadata_diagnostic"] = DIAGNOSTIC
        result["trajectory"]["diagnostics"]["synthetic_note"] = DIAGNOSTIC
        result["private_metadata"] = {"path": PRIVATE, "windows": "C:\\Users\\synthetic\\solver.c",
                                      "unc": "\\\\synthetic-server\\private\\solver.c",
                                      "file_single": "file:" + PRIVATE + "/private.obj",
                                      "file_upper": "FILE://" + PRIVATE + "/private.obj"}
        result["diagnostic_key"] = {PRIVATE + "/key": "private path key"}
        # The legacy engine compares repr(query) in its saved assumptions, so
        # source result key order is part of its own private read contract.
        (source / "result.json").write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
        model = json.loads((source / "scene.normalized.json").read_bytes())
        (source / "model.json").write_bytes(canonical({"schema_version": "pipeline-model/1",
            "domain": "physics", "model": model, "compiler_diagnostic": DIAGNOSTIC}))
        summary = json.loads((source / "summary.json").read_bytes())
        summary["artifacts"] = copy.deepcopy(result["artifacts"])
        summary["diagnostics"]["synthetic_note"] = DIAGNOSTIC
        summary["linked_plan"] = {"path": str(source / "plan.json"),
            "bytes": (source / "plan.json").stat().st_size,
            "sha256": digest((source / "plan.json").read_bytes())}
        (source / "summary.json").write_bytes(canonical(summary))
        if stage:
            roles = {"result.json": "canonical_result", "model.json": "canonical_model",
                "scene.normalized.json": "solver_model", "plan.json": "solver_plan",
                "summary.json": "verification", "completion.json": "completion",
                "measurements.json": "solver_observations"}
            artifacts = [{"path": name, "role": role, "media_type": "application/json",
                "bytes": (source / name).stat().st_size, "sha256": digest((source / name).read_bytes())}
                for name, role in roles.items()]
            by_name = {ref["path"]: ref for ref in artifacts}
            manifest = {"schema_version": "pipeline-simulation/1", "domain": "physics", "ok": True,
                "result": by_name["result.json"], "model": by_name["model.json"],
                "quality_gate": {"numerical_passed": True, "passed": True},
                "data_usable": True, "data": {"numerical_usable": True}, "artifacts": artifacts,
                "compiler_diagnostic": DIAGNOSTIC}
            (source / "simulation-manifest.json").write_bytes(canonical(manifest))
        return source

    def hashes(self, source):
        return {path.name: digest(path.read_bytes()) for path in source.iterdir() if path.is_file()}

    def mutate(self, archive_path, update):
        with zipfile.ZipFile(archive_path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        update(members)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in sorted(members.items()):
                archive.writestr(name, payload)
        Path(archive_path).write_bytes(output.getvalue())

    def test_portable_projected_paths_values_and_source_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.source(root)
            before = self.hashes(source)
            with patch("physics_demo.runner.simulate", side_effect=AssertionError("no rerun")):
                exported = export_data(source / "result.json", source / "model.json", root / "delivery", portable=True)
                verified = verify_portable_data(source / "result.json", source / "model.json", exported["path"])
            self.assertTrue(verified["verified"])
            self.assertEqual(exported["schema"], "pipeline-data/2")
            self.assertEqual(before, self.hashes(source))
            with zipfile.ZipFile(exported["path"]) as archive:
                for name in archive.namelist():
                    payload = archive.read(name)
                    self.assertNotIn(PRIVATE.encode(), payload, name)
                    self.assertNotIn(str(source).encode(), payload, name)
                    self.assertNotIn(b"/scratch/synthetic", payload, name)
                    self.assertNotIn(b"C:\\\\Users\\\\synthetic", payload, name)
                raw = json.loads((source / "result.json").read_bytes())
                public = json.loads(archive.read("result.json"))
                self.assertNotEqual(digest(archive.read("result.json")), before["result.json"])
                self.assertEqual(public["scene"], raw["scene"])
                self.assertEqual(public["scene"]["queries"], raw["scene"]["queries"])
                self.assertEqual(public["trajectory"]["frames"], raw["trajectory"]["frames"])
                self.assertEqual(public["trajectory"]["observations"], raw["trajectory"]["observations"])
                numeric = lambda value: {key: item for key, item in value.items() if type(item) in (int, float)}
                self.assertEqual(numeric(public["trajectory"]["diagnostics"]), numeric(raw["trajectory"]["diagnostics"]))
                self.assertEqual(json.loads(archive.read("measurements.json")), json.loads((source / "measurements.json").read_bytes()))
                self.assertEqual(public["portable_projection"]["schema"], PORTABLE_SCHEMA)
                self.assertFalse(public["portable_projection"]["byte_exact_source"])
                self.assertEqual(public["private_metadata"]["file_single"], "[local-path-omitted]")
                self.assertEqual(public["private_metadata"]["file_upper"], "[local-path-omitted]")
                self.assertEqual(public["artifacts"]["measurements"], "measurements.json")
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["source_result_sha256"], before["result.json"])
                self.assertEqual(manifest["portable_projection"]["algorithm"], PROJECTION_ALGORITHM)
                projections = {item["path"]: item for item in manifest["portable_projection"]["members"]}
                for member in manifest["members"]:
                    payload = archive.read(member["path"])
                    self.assertEqual((len(payload), digest(payload)), (member["bytes"], member["sha256"]))
                    self.assertEqual(projections[member["path"]]["projected_sha256"], digest(payload))
                for path in source.iterdir():
                    name = "source-stage/" + path.name
                    self.assertEqual(projections[name]["source_sha256"], before[path.name])
                stage = json.loads(archive.read("source-stage/simulation-manifest.json"))
                for ref in stage["artifacts"]:
                    payload = archive.read("source-stage/" + ref["path"])
                    self.assertEqual((len(payload), digest(payload)), (ref["bytes"], ref["sha256"]))
                for key in ("model", "result"):
                    self.assertIn(stage[key], stage["artifacts"])
                public_summary = json.loads(archive.read("source-stage/summary.json"))
                self.assertEqual(public_summary["linked_plan"]["sha256"], digest(archive.read("source-stage/plan.json")))
                self.assertEqual(public_summary["linked_plan"]["bytes"], len(archive.read("source-stage/plan.json")))
                self.assertEqual(public_summary["artifacts"]["measurements"], "measurements.json")
                self.assertNotEqual(archive.read("source-stage/summary.json"), (source / "summary.json").read_bytes())
                quality = json.loads(archive.read("quality.json"))
                self.assertEqual(quality["quality_gate"]["simulation_stage"]["manifest_sha256"], digest(archive.read("source-stage/simulation-manifest.json")))

    def test_repeatable_without_stage_and_legacy_remains_byte_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.source(root, stage=False)
            one = export_data(source / "result.json", source / "model.json", root / "one", portable=True)
            two = export_data(source / "result.json", source / "model.json", root / "two", portable=True)
            self.assertEqual(one["sha256"], two["sha256"])
            self.assertTrue(verify_portable_data(source / "result.json", source / "model.json", one["path"])["verified"])
            legacy = export_data(source / "result.json", source / "model.json", root / "legacy")
            explicit = export_data(source / "result.json", source / "model.json", root / "explicit", portable=False)
            self.assertEqual(legacy["sha256"], explicit["sha256"])
            self.assertEqual(legacy["schema"], "pipeline-data/1")
            with zipfile.ZipFile(legacy["path"]) as archive:
                self.assertEqual(archive.read("result.json"), (source / "result.json").read_bytes())
                self.assertEqual(archive.read("model.json"), (source / "model.json").read_bytes())
                self.assertIn(PRIVATE.encode(), archive.read("result.json"))
            with self.assertRaises(PresentationError):
                verify_portable_data(source / "result.json", source / "model.json", legacy["path"])

    def test_modeling_asset_chain_task_relative_paths_and_size_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = self.source(root)
            modeling = root / "work/modeling-images/synthetic-ref"
            imports = root / "work/model-imports/synthetic-import"
            licenses = root / "modules/modeling/modeling_flow/third_party"
            for directory in (modeling / "raw", imports, licenses, root / "inputs"):
                directory.mkdir(parents=True)

            def save(path, document):
                path.write_bytes(canonical(document))
                return path

            def ref(path, spelling=None):
                return {"path": spelling or str(path), "sha256": digest(path.read_bytes()),
                        "size_bytes": path.stat().st_size}

            geometry = b"# synthetic inert geometry\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
            raw = modeling / "raw/raw.obj"
            raw.write_bytes(geometry)
            display, simulation = modeling / "display.obj", modeling / "simulation.obj"
            display.write_bytes(geometry)
            simulation.write_bytes(geometry.replace(b"1 0 0", b"0.5 0 0"))
            raw_glb = modeling / "raw/raw.glb"
            raw_glb.write_bytes(glb({"asset": {"version": "2.0"}, "extras": {"source": "synthetic inert fixture"}}))
            license_path = licenses / "Synthetic-LICENSE.txt"
            license_path.write_text("Synthetic fixture license. Retain these exact notice bytes.\n")
            source_receipt = save(root / "inputs/reference-source.json", {
                "schema_version": "modeling-source/1", "source_path": PRIVATE + "/reference.png",
                "diagnostics": DIAGNOSTIC})
            conversion = save(imports / "conversion-receipt.json", {
                "schema_version": "model-conversion/1", "input": ref(raw_glb), "output": ref(raw),
                "source_receipt": ref(source_receipt, "inputs/reference-source.json"),
                "compiler": DIAGNOSTIC})
            import_source = save(imports / "import-source.json", {
                "schema_version": "model-import/1", "source": ref(raw, "work/modeling-images/synthetic-ref/raw/raw.obj"),
                "conversion_receipt": ref(conversion, "conversion-receipt.json"),
                "upstream_path": PRIVATE + "/importer"})
            receipt = save(modeling / "receipt.json", {
                "schema_version": "image-modeling-receipt/1", "source_receipt": ref(source_receipt),
                "import_source": ref(import_source, "work/model-imports/synthetic-import/import-source.json"),
                "raw_asset": ref(raw_glb, "work/modeling-images/synthetic-ref/raw/raw.glb"),
                "artifacts": [ref(display), ref(simulation, "simulation.obj")],
                "license": ref(license_path), "diagnostics": DIAGNOSTIC})
            display_mesh = save(modeling / "display_mesh.json", {
                "vertices": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                "faces": [[0, 1, 2]], "source": ref(display, "display.obj")})
            paths = {"raw/raw.obj": raw, "raw/raw.glb": raw_glb, "display.obj": display,
                "simulation.obj": simulation, "display_mesh.json": display_mesh,
                "source-receipt.json": source_receipt, "conversion-receipt.json": conversion,
                "import-source.json": import_source, "receipt.json": receipt}
            inventory = [{"path": str(path), "name": "modeling/synthetic-ref/" + name,
                          "sha256": digest(path.read_bytes())} for name, path in paths.items()]
            inventory.append({"path": str(license_path), "name": "modeling/licenses/Synthetic-LICENSE.txt",
                              "sha256": digest(license_path.read_bytes())})
            model = json.loads((source / "model.json").read_bytes())
            model["modeling_assets"] = {"receipt": ref(receipt), "import_source": ref(import_source),
                "conversion_receipt": ref(conversion, "work/model-imports/synthetic-import/conversion-receipt.json")}
            (source / "model.json").write_bytes(canonical(model))
            stage = json.loads((source / "simulation-manifest.json").read_bytes())
            for descriptor in [stage["model"], *stage["artifacts"]]:
                if descriptor["path"] == "model.json":
                    descriptor.update(bytes=(source / "model.json").stat().st_size,
                                      sha256=digest((source / "model.json").read_bytes()))
            (source / "simulation-manifest.json").write_bytes(canonical(stage))
            source_before = self.hashes(source)
            assets_before = {item["path"]: Path(item["path"]).read_bytes() for item in inventory}
            exported = export_data(source / "result.json", source / "model.json", root / "delivery",
                                   modeling_assets=inventory, portable=True)
            self.assertTrue(verify_portable_data(source / "result.json", source / "model.json", exported["path"],
                                                modeling_assets=inventory)["verified"])
            with zipfile.ZipFile(exported["path"]) as archive:
                names = set(archive.namelist())

                def check_descriptors(value, current):
                    if isinstance(value, dict):
                        if "path" in value and "sha256" in value:
                            target = posixpath.normpath(posixpath.join(posixpath.dirname(current), value["path"]))
                            self.assertIn(target, names, (current, value["path"]))
                            self.assertEqual(value["sha256"], digest(archive.read(target)))
                            for key in ("bytes", "size", "size_bytes"):
                                if key in value:
                                    self.assertEqual(value[key], len(archive.read(target)))
                        for item in value.values():
                            check_descriptors(item, current)
                    elif isinstance(value, list):
                        for item in value:
                            check_descriptors(item, current)

                for name in sorted(names - {"manifest.json"}):
                    payload = archive.read(name)
                    self.assertNotIn(PRIVATE.encode(), payload, name)
                    self.assertNotIn(b"work/modeling-images/", payload, name)
                    self.assertNotIn(b"work/model-imports/", payload, name)
                    if name.endswith(".json"):
                        check_descriptors(json.loads(payload), name)
                for name in ("raw/raw.obj", "raw/raw.glb", "display.obj", "simulation.obj"):
                    self.assertEqual(archive.read("modeling/synthetic-ref/" + name), paths[name].read_bytes())
                self.assertEqual(archive.read("modeling/licenses/Synthetic-LICENSE.txt"), license_path.read_bytes())
                public_model = json.loads(archive.read("model.json"))
                self.assertEqual(public_model["model"], model["model"])
                self.assertEqual(public_model["modeling_assets"]["receipt"]["path"], "modeling/synthetic-ref/receipt.json")
                public_receipt = json.loads(archive.read("modeling/synthetic-ref/receipt.json"))
                self.assertEqual(public_receipt["source_receipt"]["path"], "source-receipt.json")
                self.assertNotEqual(public_receipt["source_receipt"]["size_bytes"], source_receipt.stat().st_size)
                projections = {item["path"]: item for item in json.loads(archive.read("manifest.json"))["portable_projection"]["members"]}
                for item in inventory:
                    self.assertEqual(projections[item["name"]]["source_sha256"], item["sha256"])
            self.assertEqual(source_before, self.hashes(source))
            self.assertEqual(assets_before, {path: Path(path).read_bytes() for path in assets_before})

    def test_tampering_rejected_even_with_self_consistent_replaced_hashes(self):
        def result_tamper(members):
            document = json.loads(members["result.json"])
            document["trajectory"]["frames"][0]["t"] += .25
            members["result.json"] = canonical(document)
            manifest = json.loads(members["manifest.json"])
            for ref in manifest["members"]:
                ref["bytes"], ref["sha256"] = len(members[ref["path"]]), digest(members[ref["path"]])
            for ref in manifest["portable_projection"]["members"]:
                ref["projected_bytes"], ref["projected_sha256"] = len(members[ref["path"]]), digest(members[ref["path"]])
            members["manifest.json"] = canonical(manifest)

        def source_hash_tamper(members):
            manifest = json.loads(members["manifest.json"])
            manifest["source_result_sha256"] = "0" * 64
            members["manifest.json"] = canonical(manifest)

        def stage_tamper(members):
            document = json.loads(members["source-stage/summary.json"])
            document["diagnostics"]["synthetic_note"] = "fraudulent diagnostic"
            members["source-stage/summary.json"] = canonical(document)

        for update in (result_tamper, source_hash_tamper, stage_tamper,
                       lambda members: members.update({"extra-private-result.json": b"{}"}),
                       lambda members: members.pop("source-stage/completion.json")):
            with self.subTest(tamper=update.__name__), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = self.source(root)
                exported = export_data(source / "result.json", source / "model.json", root / "delivery", portable=True)
                self.mutate(exported["path"], update)
                with self.assertRaises(PresentationError):
                    verify_portable_data(source / "result.json", source / "model.json", exported["path"])

    def test_inconsistent_source_descriptor_is_rejected_without_publishing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.source(root)
            summary = json.loads((source / "summary.json").read_bytes())
            summary["linked_plan"]["sha256"] = "0" * 64
            (source / "summary.json").write_bytes(canonical(summary))
            stage = json.loads((source / "simulation-manifest.json").read_bytes())
            for ref in stage["artifacts"]:
                if ref["path"] == "summary.json":
                    ref.update(bytes=(source / "summary.json").stat().st_size,
                               sha256=digest((source / "summary.json").read_bytes()))
            (source / "simulation-manifest.json").write_bytes(canonical(stage))
            with self.assertRaisesRegex(PresentationError, "descriptor disagrees"):
                export_data(source / "result.json", source / "model.json", root / "delivery", portable=True)
            self.assertFalse((root / "delivery/data.zip").exists())

    def test_hidden_zip_metadata_and_appended_original_are_rejected(self):
        for location in ("comment", "trailer", "duplicate"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = self.source(root)
                exported = export_data(source / "result.json", source / "model.json", root / "delivery", portable=True)
                if location == "comment":
                    with zipfile.ZipFile(exported["path"], "a") as archive:
                        archive.comment = PRIVATE.encode()
                elif location == "trailer":
                    with Path(exported["path"]).open("ab") as stream:
                        stream.write((source / "result.json").read_bytes())
                else:
                    with zipfile.ZipFile(exported["path"], "a") as archive:
                        with self.assertWarns(UserWarning):
                            archive.writestr("result.json", archive.read("result.json"))
                with self.assertRaises(PresentationError):
                    verify_portable_data(source / "result.json", source / "model.json", exported["path"])

    def test_portable_compressed_fallback_verifies_and_retains_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.source(root, stage=False)
            model = json.loads((source / "model.json").read_bytes())
            model["saved_metadata"] = "bounded synthetic repeat metadata " * 3000
            (source / "model.json").write_bytes(canonical(model))
            before = self.hashes(source)
            budget = 16 * 1024
            exported = export_data(source / "result.json", source / "model.json", root / "delivery",
                                   max_bytes=budget, portable=True)
            self.assertLessEqual(exported["bytes"], budget)
            self.assertTrue(verify_portable_data(source / "result.json", source / "model.json", exported["path"],
                                                max_bytes=budget)["verified"])
            with zipfile.ZipFile(exported["path"]) as archive:
                self.assertTrue(all(item.compress_type == zipfile.ZIP_DEFLATED for item in archive.infolist()))
                self.assertEqual(json.loads(archive.read("model.json"))["saved_metadata"], model["saved_metadata"])
            self.assertEqual(before, self.hashes(source))

    def test_private_path_in_immutable_scientific_value_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            scene = json.loads((self.seed / "scene.normalized.json").read_bytes())
            scene["name"] = PRIVATE
            self.assertTrue(simulate(scene, source, 30, make_video=False)["ok"])
            before = self.hashes(source)
            with self.assertRaisesRegex(PresentationError, "immutable scientific"):
                export_data(source / "result.json", source / "scene.normalized.json", root / "delivery", portable=True)
            self.assertFalse((root / "delivery/data.zip").exists())
            self.assertEqual(before, self.hashes(source))

    def test_escaped_glb_json_paths_are_rejected_without_rewriting_binary(self):
        for path in (PRIVATE + "/private.obj", "file:" + PRIVATE + "/private.obj",
                     "FILE://" + PRIVATE + "/private.obj"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                source = self.source(root, stage=False)
                asset = root / "private.glb"
                payload = glb({"asset": {"version": "2.0"}, "extras": {"source_path": path}}, escaped_slashes=True)
                asset.write_bytes(payload)
                self.assertNotIn(PRIVATE.encode(), payload)
                inventory = [{"path": str(asset), "name": "modeling/private.glb", "sha256": digest(payload)}]
                before = self.hashes(source)
                with self.assertRaisesRegex(PresentationError, "GLB JSON metadata"):
                    export_data(source / "result.json", source / "model.json", root / "delivery",
                                modeling_assets=inventory, portable=True)
                self.assertFalse((root / "delivery/data.zip").exists())
                self.assertEqual(asset.read_bytes(), payload)
                self.assertEqual(before, self.hashes(source))

    def test_unsafe_archive_member_names_cannot_be_exported(self):
        for name in ("modeling/unsafe:receipt.json", "modeling//receipt.json", "modeling/./receipt.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                source = self.source(root, stage=False)
                asset = root / "receipt.json"
                asset.write_bytes(b"{}")
                inventory = [{"path": str(asset), "name": name, "sha256": digest(asset.read_bytes())}]
                with self.assertRaises(PresentationError):
                    export_data(source / "result.json", source / "model.json", root / "delivery",
                                modeling_assets=inventory, portable=True)
                self.assertFalse((root / "delivery/data.zip").exists())


if __name__ == "__main__":
    unittest.main()
