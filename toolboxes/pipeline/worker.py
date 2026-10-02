"""Fixed-argv, single-stage process entrypoint; copied into each module package."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def atomic(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    args = parser.parse_args()
    engine = args.engine.resolve()
    os.environ["PHYSICS_PIPELINE_PHYSICS_ROOT"] = str(engine)
    os.environ["PHYSICS_DEMO_NATIVE_LIBRARY"] = str(engine / "prebuilt/libphysics_native.dylib")
    os.environ["PHYSICS_DEMO_PREBUILT_VIDEO_DIR"] = str(engine / "prebuilt")
    sys.path.insert(0, str(engine))
    sys.path.insert(0, str(engine / "physics_release"))
    request = json.loads(args.request.read_text())
    metadata = json.loads((Path(__file__).parent / "module.json").read_text())
    if request.get("schema_version") != "stage-call/1" or request.get("stage") != metadata["role"]:
        raise ValueError("wrong stage request")
    options = request["arguments"]
    if not isinstance(options, dict):
        raise ValueError("stage arguments must be an object")
    try:
        if metadata["role"] == "modeling":
            from pipeline_stages.modeling import build_model
            result = build_model(**options)
        elif metadata["role"] == "simulation":
            from pipeline_stages.simulation import run_simulation
            result = run_simulation(**options)
        elif metadata["role"] == "rendering":
            if request.get("action") == "model_preview":
                from pipeline_presentation.model_preview import render_model_preview
                result = render_model_preview(**options, engine_root=engine)
            elif request.get("action") == "export":
                from pipeline_presentation.artifacts import export_data
                result = export_data(**options)
            elif request.get("action") == "render":
                from pipeline_presentation.rendering import render_simulation
                result = render_simulation(**options)
            else:
                raise ValueError("unknown rendering action")
        else:
            raise ValueError("unknown module role")
        atomic(args.response, result)
    except Exception as error:
        atomic(args.response, {"ok": False, "stage": metadata["role"],
            "failure": {"code": "stage_failed", "message": str(error)[:1000], "retryable": False}})
        raise


if __name__ == "__main__":
    main()
