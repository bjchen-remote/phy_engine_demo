#!/usr/bin/env python3
"""Run a toolbox in a real macOS sandbox and save a verified MP4, without a bridge."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from registry import manifest, publish, read_json, sha256, verify, write_json


def profile(package: Path, job: Path) -> str:
    def literal(path):
        return str(Path(path).resolve()).replace("\\", "\\\\").replace('"', '\\"')
    roots = ["/System", "/usr", "/Applications", "/opt/homebrew", "/Library/Developer", "/private/etc",
             "/private/var/select", "/var/select", "/dev", sys.prefix, sys.base_prefix, package, job]
    parents = ["/", "/Library", "/Library/Developer", "/private", "/private/var", "/var"]
    reads = " ".join(f'(subpath "{literal(path)}")' for path in roots)
    reads += " " + " ".join(f'(literal "{literal(path)}")' for path in parents)
    return " ".join(["(version 1)", "(deny default)", "(allow process*)",
        "(allow signal (target self))", "(allow sysctl-read)", "(allow mach-lookup)",
        "(allow ipc-posix*)", '(allow file-read-metadata (subpath "/"))',
        f"(allow file-read* {reads})", f'(allow file-write* (subpath "{literal(job)}"))',
        "(deny network*)"])


def execute(package: Path, job: Path, phase: str, timeout: int) -> None:
    command = ["/usr/bin/sandbox-exec", "-p", profile(package, job), sys.executable,
               str(package / manifest(package)["entrypoint"]), "--phase", phase,
               "--task", str(job / "task.json")]
    environment = {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(job / "work/tmp"),
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "LANG": "en_US.UTF-8"}
    process = subprocess.Popen(command, cwd=job / "work", env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    (job / f"{phase}.log").write_text(output[-20000:])
    if process.returncode:
        raise RuntimeError(f"{phase} failed: {output[-2000:]}")


def validate_result(job: Path, max_bytes: int) -> tuple[Path, dict]:
    result = read_json(job / "artifacts/result-manifest.json")
    if result.get("status") != "succeeded" or result.get("verification", {}).get("passed") is not True:
        raise ValueError("toolbox verification failed")
    outputs = result.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 1 or outputs[0].get("media_type") != "video/mp4":
        raise ValueError("expected exactly one MP4")
    item = outputs[0]
    relative = Path(item["path"])
    raw = job / "artifacts" / relative
    if relative.is_absolute() or any(path.is_symlink() for path in (raw, *raw.parents)):
        raise ValueError("invalid output path")
    video = raw.resolve()
    video.relative_to((job / "artifacts").resolve())
    if video.suffix != ".mp4" or not 0 < video.stat().st_size <= max_bytes:
        raise ValueError("invalid video size or extension")
    with video.open("rb") as handle:
        header = handle.read(12)
    if header[4:8] != b"ftyp" or sha256(video) != item["sha256"]:
        raise ValueError("video integrity check failed")
    return video, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent / "physics")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--text", default="快速模拟一滴水落到地面")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        raise RuntimeError("the current prebuilt toolbox and sandbox require macOS")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="toolbox-smoke-") as temporary:
        root = Path(temporary).resolve()
        job = root / "job"
        (job / "work/tmp").mkdir(parents=True)
        (job / "artifacts").mkdir()
        pointer = publish(args.source, root / "registry", True)
        package = verify(root / "registry", pointer)
        write_json(job / "task.json", {"schema_version": 1, "task_id": "offline-smoke",
            "request": {"text": args.text, "trust": "untrusted_external_input"},
            "limits": {"wall_time_seconds": 30, "max_output_bytes": 16 * 1024 * 1024, "network": False},
            "output_dir": "artifacts", "work_dir": "work"})
        execute(package, job, "probe", 5)
        capability = read_json(job / "capability.json")
        if capability.get("supported") is not True:
            raise ValueError("toolbox does not support this request or budget")
        execute(package, job, "run", 30)
        video, result = validate_result(job, 16 * 1024 * 1024)
        args.output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video, args.output / "simulation.mp4")
        write_json(args.output / "result-manifest.json", result)
        report = {"schema_version": 1, "ok": True, "toolbox": pointer,
                  "elapsed_seconds": round(time.monotonic() - started, 3),
                  "video_bytes": video.stat().st_size}
        write_json(args.output / "acceptance.json", report)
        print(json.dumps(report))


if __name__ == "__main__":
    main()
