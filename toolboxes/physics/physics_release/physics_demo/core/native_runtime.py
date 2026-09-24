"""Shared content-addressed C11 build/load cache for the standalone solvers.

Each bridge owns its ctypes structs, ABI binding and live-library dictionary.
This module owns compilation, atomic cache publication and one rebuild after
a corrupt or ABI-incompatible disk entry. The particle ABI 5 path is separate.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
from typing import Callable

from physics_demo.core.native_backend import NativeBackendUnavailable, NativeSimulationError, _compiler
from physics_demo.limits import MAX_WALL_TIME_S


def load_library(
    source: Path, *, prefix: str, name: str, deadline: float,
    libraries: dict[str, C.CDLL], bind: Callable[[C.CDLL], None],
    extra_sources: tuple[Path, ...] = (), dependencies: tuple[Path, ...] = (),
    pthread: bool = False,
) -> tuple[C.CDLL, float, str]:
    """Compile one or more C units, preserving default callers' cache keys.

    Extra translation units include their sibling headers in the digest;
    dependencies lists any additional included headers, such as rigid math.
    """
    def timeout() -> float | None:
        remaining = deadline - time.monotonic()
        if remaining < .01:
            raise NativeBackendUnavailable(f"{name} backend deadline exhausted before compilation.")
        # Bounded API calls use their remaining task budget. The host-owned
        # unlimited path has a much larger sentinel deadline and no compiler
        # subprocess timeout, including the cold version query and rebuild.
        return None if remaining > MAX_WALL_TIME_S else remaining

    timeout()
    compiler = _compiler()
    darwin = platform.system() == "Darwin"
    flags = ["-std=c11", "-O3", "-DNDEBUG", "-fPIC", "-fvisibility=hidden", "-ffp-contract=off"]
    flags += ["-dynamiclib"] if darwin else ["-shared", "-lm"]
    if pthread:
        flags.append("-pthread")
    try:
        version = subprocess.run([compiler, "--version"], capture_output=True,
                                 timeout=timeout(), check=True).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise NativeBackendUnavailable(f"{name} C11 compiler could not report its version.") from error
    payload = source.read_bytes() + source.with_suffix(".h").read_bytes()
    for path in (*extra_sources, *(p.with_suffix(".h") for p in extra_sources), *dependencies):
        contents = path.read_bytes()
        payload += b"\0dependency\0" + path.name.encode() + b"\0" + str(len(contents)).encode() + b"\0" + contents
    digest = hashlib.sha256(payload + version + "\0".join(flags).encode()).hexdigest()[:20]
    cache = Path(tempfile.gettempdir()) / "physics-agent-demo-native"
    cache.mkdir(parents=True, exist_ok=True)
    output = cache / (prefix + digest + (".dylib" if darwin else ".so"))
    compiled = 0.0
    for attempt in range(2):
        timeout()
        if str(output) in libraries:
            return libraries[str(output)], compiled, digest
        if not output.exists():
            # mkstemp is unique even for concurrent builds in the same process.
            descriptor, temporary_name = tempfile.mkstemp(prefix="." + prefix, suffix=".tmp", dir=cache)
            os.close(descriptor)
            temporary = Path(temporary_name)
            compile_start = time.monotonic()
            try:
                process = subprocess.run([compiler, *flags, str(source), *(str(p) for p in extra_sources), "-o", str(temporary)],
                                         capture_output=True, timeout=timeout(), check=False)
                if process.returncode:
                    raise NativeSimulationError(f"{name} C11 compilation failed: "
                                                + process.stderr.decode(errors="replace")[-1500:])
                os.replace(temporary, output)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise NativeBackendUnavailable(f"{name} native compiler failed or exhausted its deadline.") from error
            finally:
                temporary.unlink(missing_ok=True)
            compiled += time.monotonic() - compile_start
        try:
            library = C.CDLL(str(output))
            bind(library)
            libraries[str(output)] = library
            return library, compiled, digest
        except (OSError, AttributeError) as error:
            output.unlink(missing_ok=True)
            if attempt:
                raise NativeBackendUnavailable(f"{name} native library is not loadable.") from error
    raise NativeBackendUnavailable(f"{name} native library is unavailable.")
