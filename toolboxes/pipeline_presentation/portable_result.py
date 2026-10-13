"""Versioned, source-recomputable projection of saved numerical evidence.

``pipeline-data/2`` is a derived public representation, not byte-exact source
evidence and not a legacy engine restart input. The private source is never
written. Verification reconstructs every expected member from that source; ZIP
hash declarations alone are never accepted as proof.
"""
from __future__ import annotations

import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import struct
import zipfile

from .artifacts import (MAX_EXPORT_BYTES, PresentationError, _bad_number,
                        _data_manifest, _data_members, _unique_pairs, canonical,
                        digest, load_source, verify_unchanged)


PORTABLE_SCHEMA = "portable-physics-result/1"
PROJECTION_ALGORITHM = "saved-json-path-projection/1"
OMITTED_PATH = "[local-path-omitted]"
# URLs remain URLs, but filesystem URLs are local paths. Embedded diagnostics
# cover the normal Unix/Mac roots as well as Windows drive and UNC paths.
_LOCAL = re.compile(
    r'(?<![\w:/])(?:(?i:file:)[^\s\"\'<>;,)]*|'
    r'/(?:Users|home|private|tmp|var|Volumes|opt|Applications|Library|usr|etc|mnt|media|workspace)'
    r'(?:/[^\s\"\'<>;,)]*)?|[A-Za-z]:[\\/][^\s\"\'<>;,)]*|'
    r'\\\\[^\s\"\'<>;,)]*|~[/\\][^\s\"\'<>;,)]*)')
_POSIX = re.compile(r'''(?<![\w:/.])/(?:[^\s/"'<>;,)=\]\[{}:]+/)*[^\s/"'<>;,)=\]\[{}:]+''')
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_FILE_URI = re.compile(r"^file:", re.IGNORECASE)


def _fail(message: str) -> None:
    raise PresentationError(message, "invalid_portable_result")


def _safe_name(name: str) -> bool:
    return (isinstance(name, str) and bool(name) and "\\" not in name and "\0" not in name and ":" not in name
            and not PurePosixPath(name).is_absolute()
            and all(part not in ("", ".", "..") for part in name.split("/")))


def _local_string(value: str) -> bool:
    if value.startswith(("/", "~/", "~\\", "\\\\")) or _DRIVE.match(value) or _FILE_URI.match(value):
        return True
    return _LOCAL.search(value) is not None or _POSIX.search(value) is not None


def _has_local(value) -> bool:
    if isinstance(value, dict):
        return any(_local_string(key) or _has_local(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_local(item) for item in value)
    return isinstance(value, str) and _local_string(value)


def _json(payload: bytes) -> dict:
    try:
        value = json.loads(payload, object_pairs_hook=_unique_pairs, parse_constant=_bad_number)
        if not isinstance(value, dict):
            _fail("portable JSON members must be objects")
        canonical(value)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PresentationError("invalid JSON in portable source", "invalid_portable_result") from exc


def _archive_bytes(members: dict[str, bytes], compression: int) -> bytes:
    """Reproduce the fixed container format, including otherwise-hidden metadata."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, payload in sorted(members.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, payload, compress_type=compression,
                compresslevel=6 if compression == zipfile.ZIP_DEFLATED else None)
    return buffer.getvalue()


def _validate_glb_metadata(payload: bytes) -> None:
    """Inspect decoded glTF metadata without rewriting geometry or BIN bytes."""
    if len(payload) < 20:
        _fail("portable GLB has a truncated header")
    magic, version, length = struct.unpack_from("<4sII", payload)
    if magic != b"glTF" or version != 2 or length != len(payload):
        _fail("portable GLB must have an exact glTF v2 container length")
    offset, chunks = 12, []
    while offset < length:
        if offset + 8 > length:
            _fail("portable GLB has a truncated chunk header")
        size, kind = struct.unpack_from("<I4s", payload, offset)
        offset += 8
        if size % 4 or size > length - offset:
            _fail("portable GLB has an invalid chunk length")
        content = payload[offset:offset + size]
        offset += size
        chunks.append(kind)
        if kind == b"JSON" and len(chunks) == 1:
            document = _json(content)
            if _has_local(document):
                _fail("byte-exact GLB JSON metadata contains a local path")
        elif kind != b"BIN\0" or chunks != [b"JSON", b"BIN\0"]:
            _fail("portable GLB has duplicate, misplaced or unsupported chunks")
    if not chunks or chunks[0] != b"JSON" or offset != length:
        _fail("portable GLB is missing its first JSON chunk")


def portable_members_and_manifest(source: dict, members: dict[str, bytes],
                                  modeling_sources: list | None = None) -> tuple[dict, dict]:
    """Project validated inputs using a fixed algorithm, independent of ZIP claims.

    Scientific scenes, declared queries, observations and trajectory payloads
    other than diagnostic metadata are immutable. If they contain a local path,
    export fails instead of silently changing a query, ID or scientific value.
    Artifact descriptors are checked against actual source bytes before their
    hashes and lengths are rebound to the projected target. Cyclic hash-bound
    descriptors cannot have a finite byte projection and are rejected.
    """
    original = dict(members)
    if not all(_safe_name(name) for name in original):
        _fail("portable archive member names must be safe relative paths")
    # Map private paths only in memory. No private-path map goes into the ZIP.
    saved: dict[str, Path] = {"result.json": source["path"], "model.json": source["model_path"]}
    stage = source["stage"]
    if stage is not None:
        saved["source-stage/simulation-manifest.json"] = stage["path"]
        saved.update({"source-stage/" + name: path for name, path in stage["files"].items()})
    if source["domain"] == "physics" and source["measurements"] is not None:
        measurement_path = Path(source["result"]["artifacts"]["measurements"]).resolve(strict=True)
        saved["measurements.json"] = measurement_path
        # Use actual saved bytes as the projection input, even when the legacy
        # derived member canonicalizes the same measurement object.
        original["measurements.json"] = measurement_path.read_bytes()
    saved_hashes = {name: source["tracked"][path] for name, path in saved.items()}
    for path, name, expected_hash in modeling_sources or []:
        if name not in original or name in saved:
            _fail("modeling source map disagrees with the member inventory")
        saved[name] = path.resolve(strict=True)
        saved_hashes[name] = expected_hash
    raw_saved = {name: path.read_bytes() for name, path in saved.items()}
    for name, path in saved.items():
        if digest(raw_saved[name]) != saved_hashes[name]:
            _fail("private source changed before portable projection")
    aliases = {str(path): name for name, path in saved.items()}
    aliases.update({str(source["path"]): "result.json", str(source["model_path"]): "model.json"})
    if "measurements.json" in saved:
        aliases[str(saved["measurements.json"])] = "measurements.json"
    relative_aliases: dict[str, set[str]] = {}
    for name, path in saved.items():
        # The source model also uses task-relative work/... and inputs/...
        # references. Bind only suffixes of the explicit, hash-checked source
        # inventory, never paths obtained by walking the source filesystem.
        relative_aliases.setdefault(path.name, set()).add(name)
        for index, part in enumerate(path.parts):
            if part in {"work", "inputs", "modules"}:
                suffix = PurePosixPath(*path.parts[index:]).as_posix()
                relative_aliases.setdefault(suffix, set()).add(name)

    dictionary = _json(original["data-dictionary.json"])
    dictionary["schema"] = "pipeline-data-dictionary/2"
    dictionary["files"].update({
        "result.json": "Portable JSON projection of the saved result. Scientific values are preserved; paths and diagnostic text are projected. Not byte-exact original evidence or a legacy restart input.",
        "model.json": "Portable JSON projection of the supplied model document. The normalized scientific model is preserved.",
        "scene.normalized.json": "Unchanged normalized scientific model, in canonical JSON encoding.",
    })
    if stage is not None:
        dictionary["files"]["source-stage/"] = (
            "Portable projections of all declared simulation artifacts. Internal paths and hash/size descriptors resolve to projected members inside this directory. Source hashes remain in manifest.json; original private result/summary bytes are not included.")
    dictionary["portable_contract"] = {"schema": PORTABLE_SCHEMA, "algorithm": PROJECTION_ALGORITHM}
    original["data-dictionary.json"] = canonical(dictionary)
    documents = {name: _json(payload) for name, payload in original.items() if name.endswith(".json")}
    projected: dict[str, bytes] = {}
    visiting: set[str] = set()

    def private_alias(value: str) -> str | None:
        if value in aliases:
            return value
        # macOS /tmp and /var are aliases of /private/tmp and /private/var.
        # Resolve names only; never open a path discovered in document text.
        if value.startswith("/"):
            try:
                resolved = str(Path(value).resolve(strict=False))
            except (OSError, RuntimeError, ValueError):
                return None
            if resolved in aliases:
                return resolved
        return None

    def target_for(value: str, current: str) -> str | None:
        known = private_alias(value)
        if known is not None:
            if current.startswith("source-stage/") and stage is not None:
                # A stage document refers to its own stage copy, including the
                # duplicate model/result/measurements exported at ZIP root.
                for stage_name, path in saved.items():
                    if stage_name.startswith("source-stage/") and str(path) == known:
                        return stage_name
            return aliases[known]
        if not value or value.startswith("/") or _FILE_URI.match(value) or _DRIVE.match(value) or "\\" in value:
            return None
        relative = posixpath.normpath(posixpath.join(posixpath.dirname(current), value))
        if relative in original:
            return relative
        if value in original:
            return value
        candidates = relative_aliases.get(value, set())
        if not candidates:
            return None
        if len({saved[name] for name in candidates}) != 1:
            _fail("ambiguous task-relative modeling source reference")
        known = str(saved[next(iter(candidates))])
        return target_for(known, current)

    def path_to(target: str, current: str) -> str:
        return posixpath.relpath(target, posixpath.dirname(current) or ".")

    def string(value: str, current: str) -> str:
        if private_alias(value) is not None or value in relative_aliases or value in original:
            target = target_for(value, current)
            if target is not None:
                return path_to(target, current)
        if value.startswith(("/", "~/", "~\\", "\\\\")) or _DRIVE.match(value) or _FILE_URI.match(value):
            return OMITTED_PATH
        return _POSIX.sub(OMITTED_PATH, _LOCAL.sub(OMITTED_PATH, value))

    def project(value, current: str, protected: bool = False, trail: tuple = ()):
        # Protect model identity and solver observations, while allowing path
        # redaction in trajectory.diagnostics (its numeric values stay intact).
        if protected:
            if _has_local(value):
                _fail("local paths occur inside immutable scientific values")
            return value
        if isinstance(value, dict):
            descriptor_target = None
            if isinstance(value.get("path"), str) and any(key in value for key in ("sha256", "bytes", "size", "size_bytes")):
                descriptor_target = target_for(value["path"], current)
                if descriptor_target is not None:
                    evidence = raw_saved.get(descriptor_target, original[descriptor_target])
                    if "sha256" in value and value["sha256"] != digest(evidence):
                        _fail("source artifact descriptor disagrees with actual bytes")
                    for key in ("bytes", "size", "size_bytes"):
                        if key in value and (type(value[key]) is not int or value[key] != len(evidence)):
                            _fail("source artifact descriptor size disagrees with actual bytes")
            result = {}
            for key, item in value.items():
                public_key = string(key, current)
                if public_key in result:
                    _fail("path projection would collide JSON keys")
                immutable = ((key in {"scene", "model"} and item == source["model"])
                             or key in {"queries", "observations", "answers"}
                             or (trail == ("trajectory",) and key != "diagnostics"))
                result[public_key] = project(item, current, immutable, (*trail, key))
            if descriptor_target is not None:
                target_bytes = member(descriptor_target)
                result["path"] = path_to(descriptor_target, current)
                if "sha256" in value:
                    result["sha256"] = digest(target_bytes)
                for key in ("bytes", "size", "size_bytes"):
                    if key in value:
                        result[key] = len(target_bytes)
            return result
        if isinstance(value, list):
            return [project(item, current, trail=trail) for item in value]
        return string(value, current) if isinstance(value, str) else value

    def member(name: str) -> bytes:
        if name in projected:
            return projected[name]
        if name in visiting:
            _fail("cyclic hash-bound artifact descriptors cannot be projected")
        visiting.add(name)
        if name in documents:
            document = documents[name]
            if "portable_projection" in document:
                _fail("portable source is already a derived projection")
            is_scene = (name.endswith("scene.normalized.json") or document == source["model"])
            value = project(document, name, protected=is_scene)
            if name == "result.json" or name.endswith(("/result.json", "/summary.json")):
                value["portable_projection"] = {"schema": PORTABLE_SCHEMA,
                    "algorithm": PROJECTION_ALGORITHM, "source_sha256": digest(raw_saved.get(name, original[name])),
                    "local_paths": "archive_relative_or_omitted", "byte_exact_source": False,
                    "restart_checkpoint": False}
            payload = canonical(value)
            if _has_local(value):
                _fail("portable JSON still contains a local path")
        else:
            # Numerical CSV and geometry/binary assets stay byte-exact. They
            # cannot hide path-bearing metadata behind a claim of portability.
            payload = original[name]
            if name.endswith(".glb"):
                _validate_glb_metadata(payload)
            text = payload.decode("utf-8", errors="replace")
            if _LOCAL.search(text) or (name.endswith((".csv", ".txt", ".obj")) and _POSIX.search(text)):
                _fail("a byte-exact non-JSON member contains a local path")
        visiting.remove(name)
        projected[name] = payload
        return payload

    for name in sorted(original):
        member(name)
    quality = _json(projected["quality.json"])
    dictionary = _json(projected["data-dictionary.json"])
    if stage is not None:
        public_stage_hash = digest(projected["source-stage/simulation-manifest.json"])
        # This explicit stage reference is distinct from semantic scene and
        # measurement hashes, which must never be rewritten.
        for value in (quality, dictionary):
            stage_gate = value.get("quality_gate", {}).get("simulation_stage")
            if stage_gate is not None:
                stage_gate["source_manifest_sha256"] = stage["sha256"]
                stage_gate["manifest_sha256"] = public_stage_hash
        projected["quality.json"] = canonical(quality)
        projected["data-dictionary.json"] = canonical(dictionary)
    manifest = _data_manifest(source, projected)
    manifest.update(schema="pipeline-data/2", warnings=quality["warnings"], sampling=quality["sampling"])
    if stage is not None:
        manifest["source_stage"].update(manifest_sha256=public_stage_hash,
            source_manifest_sha256=stage["sha256"], representation="portable_projection")
    manifest["portable_projection"] = {"schema": PORTABLE_SCHEMA, "algorithm": PROJECTION_ALGORITHM,
        "byte_exact_original_result": False, "private_source_unchanged": True,
        "restart_checkpoint": False, "local_paths": "archive_relative_or_omitted",
        "scientific_values": "unchanged; local-path-bearing scientific values rejected",
        "members": [{"path": name, "source_kind": "saved_file" if name in saved else "derived_member",
            "source_sha256": digest(raw_saved.get(name, original[name])),
            "source_bytes": len(raw_saved.get(name, original[name])),
            "projected_sha256": digest(payload), "projected_bytes": len(payload)}
            for name, payload in sorted(projected.items())]}
    if _has_local(manifest):
        _fail("portable manifest contains a local path")
    return projected, manifest


def verify_portable_data(result_path: str | Path, model_path: str | Path,
                         archive_path: str | Path, domain: str = "physics",
                         max_bytes: int = 16 * 1024 * 1024,
                         modeling_assets: list | None = None) -> dict:
    """Verify a /2 ZIP by recomputing its full contents from the private source.

    Pass the same source, domain, asset inventory and budget as the exporter.
    Untrusted manifest hashes, projection markers and source-hash claims do not
    influence reconstruction. No archive member is extracted or executed.
    """
    if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_EXPORT_BYTES:
        _fail("invalid portable verification byte budget")
    source = load_source(result_path, model_path, domain)
    original, modeling_sources = _data_members(source, max_bytes, modeling_assets)
    expected, manifest = portable_members_and_manifest(source, original, modeling_sources)
    expected["manifest.json"] = canonical(manifest)
    path = Path(archive_path)
    try:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > max_bytes:
            _fail("portable ZIP must be a bounded regular file")
        payload = path.read_bytes()
        if len(payload) > max_bytes:
            _fail("portable ZIP exceeds its byte budget")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            info = archive.infolist()
            if archive.comment or any(entry.comment or entry.extra for entry in info):
                _fail("portable ZIP contains undeclared metadata")
            names = [entry.filename for entry in info]
            if len(names) != len(set(names)) or set(names) != set(expected):
                _fail("portable ZIP members differ from the source-derived inventory")
            if sum(entry.file_size for entry in info) > min(MAX_EXPORT_BYTES, max_bytes * 16):
                _fail("portable ZIP exceeds its expanded byte budget")
            for entry in info:
                wanted = expected[entry.filename]
                if (entry.is_dir() or entry.flag_bits & 1 or entry.file_size != len(wanted)
                        or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)):
                    _fail("portable ZIP member type, size or compression is invalid")
                if archive.read(entry) != wanted:
                    _fail("portable ZIP differs from independently recomputed source projection")
        # Matching member hashes cannot rule out a leaked original file hidden
        # before/after the ZIP or in container fields. Rebuild the deterministic
        # /2 container too, rather than trusting the inventory alone.
        rebuilt = _archive_bytes(expected, zipfile.ZIP_STORED)
        if len(rebuilt) > max_bytes:
            rebuilt = _archive_bytes(expected, zipfile.ZIP_DEFLATED)
        if payload != rebuilt:
            _fail("portable ZIP container differs from its deterministic source reconstruction")
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise PresentationError("cannot verify portable ZIP", "invalid_portable_result") from exc
    verify_unchanged(source)
    if any(path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != checksum
           for path, _, checksum in modeling_sources):
        _fail("modeling assets changed during portable verification")
    return {"schema": "pipeline-data/2", "verified": True, "source_result_sha256": source["source_result_sha256"],
            "source_model_sha256": source["source_model_sha256"],
            "projected_result_sha256": digest(expected["result.json"]),
            "sha256": digest(payload), "bytes": len(payload), "member_count": len(expected)}
