"""Run the Python 3.9 compatibility suite on Intel CI.

The complete performance and numerical suite runs on the supported macOS
arm64 release runner. These three cases assert M4-calibrated wall deadlines,
which are not portable performance requirements for the Intel compatibility
runner; all other discovered tests remain in this job.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


PERFORMANCE_CASES = {
    "test_mesh_adversarial_inputs.MeshAdversarialInputs.test_frame_budget_rejects_without_silent_topology_reduction",
    "test_mesh_geometry.MeshGeometryTests.test_dense_ellipsoid_completes_full_audit_under_original_work_cap",
    "test_demo.SolverTests.test_particle_scenes_complete_under_budget_without_video",
}


def portable(suite: unittest.TestSuite, excluded: set[str]) -> unittest.TestSuite:
    selected = unittest.TestSuite()
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            selected.addTests(portable(item, excluded))
        elif item.id().removeprefix("tests.") in PERFORMANCE_CASES:
            excluded.add(item.id().removeprefix("tests."))
        else:
            selected.addTest(item)
    return selected


def main() -> int:
    excluded: set[str] = set()
    suite = portable(unittest.defaultTestLoader.discover(str(ROOT / "tests")), excluded)
    if excluded != PERFORMANCE_CASES:
        print(f"Performance exclusion mismatch: {sorted(PERFORMANCE_CASES ^ excluded)}", file=sys.stderr)
        return 2
    print(f"Intel compatibility run excludes {len(excluded)} arm64-calibrated deadline cases.")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
