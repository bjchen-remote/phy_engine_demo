"""Explicit operator installation for a pinned offline Mac MLX runtime.

This script performs installation-time network access only. QQ never invokes
it. All executable argument vectors and remote URLs are fixed by this source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib import request as urllib_request
from urllib.parse import urlparse


UPSTREAM_URL = "https://github.com/ZimengXiong/Hunyuan3D-Swift.git"
UPSTREAM_COMMIT = "292331f4d26ddb80b9dcea6bcb5629ff82f12b82"
CHECKPOINT_REVISION = "f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6"
TENCENT_SOURCE_COMMIT = "f8db63096c8282cb27354314d896feba5ba6ff8a"
MODEL_SUBFOLDER = "hunyuan3d-dit-v2-mini"
STATE_SCHEMA = "modeling-flow-install/1"
CONFIG_SHA256 = "cabcba7f6115752c8fe5b370e12bf714936f70377a8a80f151872f76c2d64609"
MODEL_SHA256 = "3cc66f3bea33e4062b7dbc875ffe1d70c4888914aec3e91b60f94e9bd01b522b"
TENCENT_LICENSE_SHA256 = "94259df223918a5733677965c1bfe1774a2dba25042d9c3b47a3418ea6c1f324"
MLX_LICENSE_SHA256 = "e0485dc21868a42682cf1255af42d1bf038cf5e80608b2006b5c69d65d426d7f"
THIRD_PARTY_NOTICE_SHA256 = "abd4d61f1c0bf3dcfc6a399457f26254be74518dc53de8b69e2606e3a3a1d241"
FOREGROUND_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"
FOREGROUND_SHA256 = "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491"
FOREGROUND_BYTES = 175997641
U2NET_LICENSE_URL = "https://raw.githubusercontent.com/xuebinqin/U-2-Net/ac7e1c817ecab7c7dff5ce6b1abba61cd213ff29/LICENSE"
U2NET_LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
REMBG_LICENSE_URL = "https://raw.githubusercontent.com/danielgatis/rembg/202e42649a8492a7c49f808de36608a7d1cbbfe3/LICENSE.txt"
REMBG_LICENSE_SHA256 = "90a3215072968fd304669c5389f04f1274a587abdd0507d99dead0f5511f8999"
SOURCE = Path(__file__).resolve().parent / "modeling_flow"
REQUIREMENTS = SOURCE / "requirements-mlx.lock.txt"
FOREGROUND_REQUIREMENTS = SOURCE / "requirements-foreground.lock.txt"
PYTHON_METADATA = ("import json,platform,sys;print(json.dumps({'version':list(sys.version_info[:3]),"
                   "'system':platform.system(),'machine':platform.machine(),"
                   "'prefix':sys.prefix,'base_prefix':sys.base_prefix,'executable':sys.executable}))")


class InstallError(ValueError):
    pass


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n"


def _atomic_json(path: Path, value: dict):
    if path.is_symlink():
        raise InstallError("Installer state cannot be a symbolic link")
    descriptor, temporary = tempfile.mkstemp(prefix=".install-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _dedicated_path(value: str | Path, label: str) -> Path:
    raw = Path(value)
    if not raw.is_absolute() or any(part.is_symlink() for part in (raw, *raw.parents)):
        raise InstallError(label + " must be an absolute path without symbolic-link components")
    path = raw.resolve()
    if path in (Path("/"), Path("/Users"), Path("/private"), Path("/private/tmp"), Path.home().resolve()):
        raise InstallError(label + " must be a dedicated directory")
    return path


def _python_metadata(executable: Path) -> dict:
    try:
        completed = subprocess.run([str(executable), "-I", "-c", PYTHON_METADATA],
                                   capture_output=True, check=True, text=True, timeout=15)
        value = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise InstallError("Cannot verify the explicit Python interpreter") from error
    if (not isinstance(value, dict) or value.get("version", [0])[:2] < [3, 12]
            or value.get("system") != "Darwin" or value.get("machine") not in ("arm64", "aarch64")):
        raise InstallError("Installation requires an Apple Silicon Mac and explicit Python >=3.12")
    return value


class _HTTPSRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise InstallError("Checkpoint redirects must stay on HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_pinned(url: str, destination: Path, sha256: str, expected_bytes: int,
                    opener=None) -> None:
    """Stream one exact file. Partial installer files can resume with HTTP Range."""
    if urlparse(url).scheme != "https":
        raise InstallError("Installation downloads require HTTPS")
    if destination.is_symlink():
        raise InstallError("Pinned installation target cannot be a symbolic link")
    if destination.exists():
        if destination.is_file() and destination.stat().st_size == expected_bytes and file_digest(destination) == sha256:
            return
        raise InstallError("Existing installation file differs from its fixed pin; it was preserved")
    partial = destination.with_name(destination.name + ".part")
    if partial.is_symlink() or (partial.exists() and not partial.is_file()):
        raise InstallError("Partial download must be an installer-owned regular file")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > expected_bytes:
        raise InstallError("Partial download exceeds its fixed size; it was preserved")
    if offset == expected_bytes:
        if file_digest(partial) != sha256:
            raise InstallError("Partial download digest differs from its pin; it was preserved")
        os.link(partial, destination)
        partial.unlink()
        return
    opener = opener or urllib_request.build_opener(_HTTPSRedirectHandler())
    headers = {"User-Agent": "qq-local-modeling-operator-installer/1", "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = "bytes=" + str(offset) + "-"
    try:
        with opener.open(urllib_request.Request(url, headers=headers), timeout=60) as response:
            status = response.status
            if offset and (status != 206 or not response.headers.get("Content-Range", "").startswith("bytes " + str(offset) + "-")):
                raise InstallError("Server did not honor partial-file resume; partial data was preserved")
            if not offset and status != 200:
                raise InstallError("Pinned download did not return a complete file response")
            with partial.open("ab" if offset else "xb") as handle:
                received = offset
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    received += len(chunk)
                    if received > expected_bytes:
                        raise InstallError("Download exceeded its fixed file size")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        if received != expected_bytes or file_digest(partial) != sha256:
            raise InstallError("Downloaded file failed its exact size/SHA-256 pin; partial data was preserved")
        os.link(partial, destination)
        partial.unlink()
    except InstallError:
        raise
    except (OSError, ValueError) as error:
        raise InstallError("Pinned download failed; partial installation data was preserved") from error


def _run(arguments: list[str], log, timeout=1800):
    try:
        subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=log,
                       stderr=subprocess.STDOUT, check=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise InstallError("Installation command failed; see install.log (files were preserved)") from error


def _copy_verified(source: Path, destination: Path, expected: str):
    if source.is_symlink() or not source.is_file() or file_digest(source) != expected:
        raise InstallError("Pinned source file differs from its expected hash")
    if destination.exists() or destination.is_symlink():
        if not destination.is_symlink() and destination.is_file() and file_digest(destination) == expected:
            return
        raise InstallError("Existing installation file differs from its pin; it was preserved")
    shutil.copyfile(source, destination)


def _runtime_config(root: Path, foreground_model="u2net") -> dict:
    value = {"schema_version": "modeling-flow-config/1", "backend": "hunyuan3d_mlx",
            "device": "metal", "dtype": "float16",
            "upstream_root": str(root / "upstream/python/shape"), "upstream_commit": UPSTREAM_COMMIT,
            "weights_root": str(root / "weights"), "checkpoint_id": "tencent/Hunyuan3D-2mini",
            "checkpoint_revision": CHECKPOINT_REVISION, "model_subfolder": MODEL_SUBFOLDER,
            "variant": "fp16", "weight_files": [
                {"path": MODEL_SUBFOLDER + "/config.yaml", "sha256": CONFIG_SHA256},
                {"path": MODEL_SUBFOLDER + "/model.fp16.safetensors", "sha256": MODEL_SHA256}]}
    value["background_removal"] = foreground_model
    if foreground_model == "u2net":
        value["foreground_model"] = {"path": "segmentation/u2net.onnx", "sha256": FOREGROUND_SHA256}
    return value


def _publish_identical_json(path: Path, value: dict):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != _json_bytes(value):
            raise InstallError("Existing installer artifact differs from its manifest; it was preserved")
        return
    with path.open("xb") as handle:
        handle.write(_json_bytes(value))


def install(output: str | Path, python: str | Path, provider_name: str,
            resume: bool = False, check_only: bool = False, foreground_model: str = "u2net") -> dict:
    root = _dedicated_path(output, "output")
    raw_python = Path(python)
    if not raw_python.is_absolute() or not raw_python.is_file() or not os.access(raw_python, os.X_OK):
        raise InstallError("--python must select an absolute executable local interpreter")
    executable = raw_python.resolve()
    if (not isinstance(provider_name, str) or not 1 <= len(provider_name) <= 200
            or any(ord(char) < 32 for char in provider_name)):
        raise InstallError("--provider-name must identify the actual local service operator")
    _python_metadata(executable)
    if foreground_model not in ("u2net", "none"):
        raise InstallError("foreground_model must be the fixed local u2net graph or none")
    adapter_inventory = {path.name: file_digest(path) for path in SOURCE.iterdir()
                         if path.is_file() and path.suffix in (".py", ".md", ".txt")}
    identity = {"schema_version": STATE_SCHEMA, "root": str(root), "python": str(executable),
                "provider_name": provider_name, "upstream_commit": UPSTREAM_COMMIT,
                "checkpoint_revision": CHECKPOINT_REVISION,
                "requirements_sha256": file_digest(REQUIREMENTS), "adapter_inventory": adapter_inventory}
    identity["foreground_model"] = foreground_model
    if foreground_model == "u2net":
        identity["foreground_requirements_sha256"] = file_digest(FOREGROUND_REQUIREMENTS)
    state_path = root / "installer-state.json"
    if root.exists():
        if not (resume or check_only) or not root.is_dir() or state_path.is_symlink() or not state_path.is_file():
            raise InstallError("--output must be new; use --resume only for this installer's retained directory")
        if state_path.stat().st_size > 65536:
            raise InstallError("Invalid retained installation state")
        try:
            state = json.loads(state_path.read_text())
        except (ValueError, UnicodeError) as error:
            raise InstallError("Invalid retained installation state") from error
        if not isinstance(state, dict) or state.get("identity") != identity:
            raise InstallError("Retained installation identity differs; use a new output directory")
    else:
        if check_only:
            raise InstallError("--check-only requires a completed existing installation")
        root.mkdir(parents=True)
        state = {"identity": identity, "stage": "created", "complete": False}
        _atomic_json(state_path, state)

    def stage(name):
        state.update(stage=name, complete=False)
        state.pop("error", None)
        _atomic_json(state_path, state)

    try:
        log_path = root / "install.log"
        health_path = root / "health-result.json"
        if log_path.is_symlink() or health_path.is_symlink():
            raise InstallError("Installer log/result must be regular files")
        runtime_python = root / ".venv/bin/python"
        if not check_only:
            with log_path.open("a", encoding="utf-8") as log:
                stage("upstream")
                upstream = _dedicated_path(root / "upstream", "upstream")
                if not upstream.exists():
                    _run(["git", "clone", "--filter=blob:none", "--no-checkout", "--", UPSTREAM_URL, str(upstream)], log)
                if not (upstream / ".git").is_dir():
                    raise InstallError("Retained upstream clone is incomplete; it was preserved for explicit recovery")
                _run(["git", "-C", str(upstream), "checkout", "--detach", UPSTREAM_COMMIT], log)
                stage("venv")
                environment = _dedicated_path(root / ".venv", "virtual environment")
                if not runtime_python.exists():
                    _run([str(executable), "-I", "-m", "venv", str(environment)], log)
                runtime_metadata = _python_metadata(runtime_python)
                if Path(runtime_metadata["prefix"]).resolve() != environment:
                    raise InstallError("Retained interpreter is not this dedicated virtual environment")
                stage("dependencies")
                lock = root / "requirements-mlx.lock.txt"
                _copy_verified(REQUIREMENTS, lock, identity["requirements_sha256"])
                _run([str(runtime_python), "-I", "-m", "pip", "--isolated", "install", "--no-cache-dir",
                      "--only-binary=:all:", "--no-deps", "--index-url", "https://pypi.org/simple",
                      "--requirement", str(lock)], log)
                if foreground_model == "u2net":
                    foreground_lock = root / "requirements-foreground.lock.txt"
                    _copy_verified(FOREGROUND_REQUIREMENTS, foreground_lock, identity["foreground_requirements_sha256"])
                    _run([str(runtime_python), "-I", "-m", "pip", "--isolated", "install", "--no-cache-dir",
                          "--only-binary=:all:", "--no-deps", "--index-url", "https://pypi.org/simple",
                          "--requirement", str(foreground_lock)], log)
                _run([str(runtime_python), "-I", "-m", "pip", "check"], log)
            stage("weights")
            checkpoint = _dedicated_path(root / "weights" / MODEL_SUBFOLDER, "checkpoint")
            checkpoint.mkdir(parents=True, exist_ok=True)
            base = "https://huggingface.co/tencent/Hunyuan3D-2mini/resolve/" + CHECKPOINT_REVISION + "/" + MODEL_SUBFOLDER + "/"
            download_pinned(base + "config.yaml", checkpoint / "config.yaml", CONFIG_SHA256, 1628)
            download_pinned(base + "model.fp16.safetensors", checkpoint / "model.fp16.safetensors",
                            MODEL_SHA256, 3819958234)
            if foreground_model == "u2net":
                stage("foreground")
                segmentation = _dedicated_path(root / "weights/segmentation", "foreground model")
                segmentation.mkdir(exist_ok=True)
                download_pinned(FOREGROUND_URL, segmentation / "u2net.onnx", FOREGROUND_SHA256, FOREGROUND_BYTES)
            stage("licenses")
            licenses = _dedicated_path(root / "licenses", "licenses")
            licenses.mkdir(exist_ok=True)
            download_pinned("https://raw.githubusercontent.com/Tencent-Hunyuan/Hunyuan3D-2/" + TENCENT_SOURCE_COMMIT + "/LICENSE",
                            licenses / "TENCENT-HUNYUAN-LICENSE.txt", TENCENT_LICENSE_SHA256, 17829)
            _copy_verified(root / "upstream/LICENSE", licenses / "MLX-PORT-LICENSE.txt", MLX_LICENSE_SHA256)
            _copy_verified(root / "upstream/THIRD_PARTY_LICENSES.md", licenses / "THIRD_PARTY_LICENSES.md", THIRD_PARTY_NOTICE_SHA256)
            if foreground_model == "u2net":
                download_pinned(U2NET_LICENSE_URL, licenses / "U2NET-APACHE-2.0.txt", U2NET_LICENSE_SHA256, 11357)
                download_pinned(REMBG_LICENSE_URL, licenses / "REMBG-MIT.txt", REMBG_LICENSE_SHA256, 1069)
            notice = ("Local image-to-3D service is provided by " + provider_name + ".\n"
                      "This service is not affiliated with Tencent and is not sponsored or endorsed by Tencent.\n"
                      "Outputs are AI generated; physical scale, material and hidden surfaces are not measured.\n"
                      "Model: tencent/Hunyuan3D-2mini, revision " + CHECKPOINT_REVISION + ".\n"
                      "Model license: licenses/TENCENT-HUNYUAN-LICENSE.txt\n"
                      "MLX port license: licenses/MLX-PORT-LICENSE.txt\n")
            notice += "Foreground processing: " + foreground_model + "; estimated masks are not verified object boundaries.\n"
            if foreground_model == "u2net":
                notice += ("Foreground model license: licenses/U2NET-APACHE-2.0.txt\n"
                           "ONNX conversion source license: licenses/REMBG-MIT.txt\n")
            notice_path = root / "NOTICE.txt"
            if notice_path.exists() or notice_path.is_symlink():
                if notice_path.is_symlink() or notice_path.read_text() != notice:
                    raise InstallError("Existing provider notice differs; it was preserved")
            else:
                notice_path.write_text(notice, encoding="utf-8")
            stage("adapter")
            adapter = _dedicated_path(root / "adapter/modeling_flow", "adapter")
            adapter.mkdir(parents=True, exist_ok=True)
            for name, checksum in adapter_inventory.items():
                _copy_verified(SOURCE / name, adapter / name, checksum)
            stage("configuration")
            _publish_identical_json(root / "config.json", _runtime_config(root, foreground_model))
            read_roots = []
            for prefix in (runtime_metadata["prefix"], runtime_metadata["base_prefix"]):
                path = _dedicated_path(Path(prefix).resolve(), "Python read root")
                if not path.is_relative_to(root) and str(path) not in read_roots:
                    read_roots.append(str(path))
            fragment = {"modeling_runtime": {"root": str(root), "python_executable": str(runtime_python),
                         "config": "config.json", "read_roots": read_roots, "provider_name": provider_name}}
            _publish_identical_json(root / "host-config.fragment.json", fragment)
        stage("health")
        health_code = ("import contextlib,json,sys;sys.path.insert(0,sys.argv[1]);"
                       "from modeling_flow.runner import health;"
                       "r=health(sys.argv[2]);print(json.dumps(r));sys.exit(0 if r['ok'] else 2)")
        with health_path.open("w", encoding="utf-8") as stdout, log_path.open("a", encoding="utf-8") as log:
            try:
                completed = subprocess.run([str(runtime_python), "-I", "-c", health_code,
                                           str(root / "adapter"), str(root / "config.json")],
                                          stdin=subprocess.DEVNULL, stdout=stdout, stderr=log,
                                          timeout=300, check=False)
            except (OSError, subprocess.SubprocessError) as error:
                raise InstallError("Installed runtime health failed; installation data was preserved") from error
        if completed.returncode:
            raise InstallError("Installed runtime health failed; see health-result.json and install.log")
        state.update(stage="ready", complete=True, inference_verified=False)
        state.pop("error", None)
        _atomic_json(state_path, state)
        return {"ok": True, "runtime_root": str(root), "inference_verified": False,
                "config_path": str(root / "config.json"),
                "host_config_fragment": str(root / "host-config.fragment.json"),
                "health_result": str(health_path)}
    except (InstallError, OSError, ValueError) as error:
        state.update(complete=False, error=str(error))
        _atomic_json(state_path, state)
        raise InstallError(str(error) + "; resume explicitly with the same arguments and --resume") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Install a fixed-pin Mac-local MLX image modeling runtime")
    parser.add_argument("--output", required=True, help="Absolute dedicated new runtime directory")
    parser.add_argument("--python", required=True, help="Explicit local Apple Silicon Python >=3.12")
    parser.add_argument("--provider-name", required=True, help="Actual local service operator, used in notices")
    parser.add_argument("--resume", action="store_true", help="Resume this installer's retained partial directory")
    parser.add_argument("--check-only", action="store_true", help="Verify existing runtime offline; do not install/download")
    parser.add_argument("--foreground-model", choices=("u2net", "none"), default="u2net",
                        help="Install pinned CPU U2-Net by default, or keep only supplied-alpha images")
    args = parser.parse_args()
    try:
        result = install(args.output, args.python, args.provider_name, args.resume, args.check_only, args.foreground_model)
    except InstallError as error:
        result = {"ok": False, "error": str(error)}
    sys.stdout.buffer.write(_json_bytes(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
