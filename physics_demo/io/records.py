"""Verified, read-only access to recorded macro-step measurements."""
from __future__ import annotations

import copy
from pathlib import Path

from physics_demo.analysis.queries import digest, MAX_MEASUREMENT_FILE_BYTES
from physics_demo.io.jsonio import loads
from physics_demo.limits import MAX_SCENE_FILE_BYTES


class RecordedRun:
    """Measured state, never velocity inferred from rendered video frames.

    These records are observations, not complete solver restart checkpoints.
    Public accessors return copies so callers cannot corrupt later reductions.
    """

    def __init__(self, scene: dict, observations: dict, summary: dict):
        self._scene = copy.deepcopy(scene)
        self._times = tuple(observations["times"])
        self._columns = {key: tuple(values) for key, values in observations["columns"].items()}
        self.summary = copy.deepcopy(summary)

    @property
    def scene(self) -> dict:
        return copy.deepcopy(self._scene)

    @property
    def times(self) -> tuple:
        return self._times

    def series(self, query_id: str) -> dict:
        if query_id not in self._columns:
            raise ValueError("No recorded scalar query with id " + repr(query_id))
        return {"times_s": list(self._times), "values": list(self._columns[query_id])}

    def metric(self, entity: str, kind: str, *, axis: str | None = None) -> tuple:
        target = {"type": kind, "entity": entity}
        if axis is not None:
            target["axis"] = axis
        for query in self._scene.get("queries", []):
            if query.get("metric") == target:
                return self._columns[query["id"]]
        raise ValueError("This measurement was not declared before running: " + repr(target))

    def state(self, entity: str, index: int = -1) -> dict:
        """Read a recorded body centre and speed at an exact macro-step index."""
        if type(index) is not int or not -len(self._times) <= index < len(self._times):
            raise ValueError("State index is outside the recorded macro-step samples")
        position = [self.metric(entity, "centroid", axis=axis)[index] for axis in "xyz"]
        return {"time_s": self._times[index], "entity": entity, "position_m": position,
                "speed_m_s": self.metric(entity, "speed")[index], "source": "solver_macro_steps"}


def _read(path: Path, limit: int):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Run data must be a bounded regular file: " + str(path))
    return loads(path.read_text(encoding="utf-8"))


def load_run(path: str | Path) -> RecordedRun:
    """Verify once, then bind scene/observations to the inspected digests."""
    from physics_demo.runner import inspect

    checked = inspect(path)
    if not checked.get("ok"):
        raise ValueError("Run did not pass inspection: " + repr(checked.get("errors", checked.get("quality_gate"))))
    root = Path(path).expanduser().resolve()
    if root.is_file():
        root = root.parent
    scene = _read(root / "scene.normalized.json", MAX_SCENE_FILE_BYTES)
    if digest(scene) != checked["scene_hash"]:
        raise ValueError("Saved scene does not match the inspected run")
    if not checked.get("measurements_sha256"):
        raise ValueError("No quantitative observations were declared for this run")
    measurements = _read(root / "measurements.json", MAX_MEASUREMENT_FILE_BYTES)
    if digest(measurements) != checked["measurements_sha256"]:
        raise ValueError("Saved measurements changed after inspection")
    return RecordedRun(scene, measurements["observations"], checked)
