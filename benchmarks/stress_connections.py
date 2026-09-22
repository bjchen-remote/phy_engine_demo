"""Independent analytic sweep through the public prepare/simulate/inspect path."""
from copy import deepcopy
import argparse
import json
import math
from pathlib import Path

from physics_demo.catalog import example
from physics_demo.runner import inspect, simulate


def exact(t, mass, stiffness, damping):
    gamma = damping / (2 * mass)
    disc = stiffness / mass - gamma * gamma
    if abs(disc) < 1e-12:
        return .4 * math.exp(-gamma*t) * (1+gamma*t)
    frequency = math.sqrt(abs(disc))
    if disc > 0:
        return .4 * math.exp(-gamma*t) * (math.cos(frequency*t)+gamma/frequency*math.sin(frequency*t))
    a, b = -gamma+frequency, -gamma-frequency
    return .4 * (b*math.exp(a*t)-a*math.exp(b*t))/(b-a)


def sweep(output):
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for mass in (.1, 1., 10.):
        for stiffness in (4., 16., 64.):
            for damping in (0., .5, 4.):
                scene = deepcopy(example("spring_oscillator")["scene"])
                scene["world"].update(duration=2, dt=.01)
                scene["entities"][1]["mass"] = mass
                scene["connections"][0].update(stiffness=stiffness, damping=damping)
                directory = output / f"m{mass:g}-k{stiffness:g}-c{damping:g}"
                run = simulate(scene, directory, make_video=False)
                if not run["ok"] or not inspect(directory / "result.json")["ok"]:
                    raise RuntimeError(run)
                observations = json.loads((directory / "result.json").read_text())["trajectory"]["observations"]
                error = max(abs(x - 1 - exact(t, mass, stiffness, damping))
                            for t, x in zip(observations["times"], observations["columns"]["x"]))
                if error >= .002:
                    raise AssertionError((mass, stiffness, damping, error))
                records.append({"mass": mass, "stiffness": stiffness, "damping": damping,
                                "maximum_position_error_m": error, "threshold_m": .002,
                                "ok": True, "runtime_s": run["total_runtime_s"]})
    for angle in (.2, .6, 1.):
        scene = deepcopy(example("rod_pendulum")["scene"])
        scene["world"].update(duration=10)
        scene["entities"][1]["position"] = [1+1.5*math.sin(angle), 2-1.5*math.cos(angle), 0]
        scene["queries"].append({"id": "height", "type": "series", "metric": {"type": "centroid", "entity": "bob", "axis": "y"}})
        directory = output / f"pendulum-{angle:g}"
        run = simulate(scene, directory, make_video=False)
        if not run["ok"] or not inspect(directory / "result.json")["ok"]:
            raise RuntimeError(run)
        obs = json.loads((directory / "result.json").read_text())["trajectory"]["observations"]["columns"]
        energy = [.5*v*v + 9.81*(y-.5) for v, y in zip(obs["speed"], obs["height"])]
        error = max(abs(e-energy[0])/energy[0] for e in energy)
        if error >= 1e-4:
            raise AssertionError((angle, error))
        records.append({"angle_rad": angle, "peak_relative_energy_error": error,
                        "threshold": 1e-4, "ok": True, "runtime_s": run["total_runtime_s"]})
    (output / "stress-results.json").write_text(json.dumps(records, indent=2) + "\n")
    print(f"{len(records)} independent analytic public runs passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    sweep(parser.parse_args().out.resolve())
