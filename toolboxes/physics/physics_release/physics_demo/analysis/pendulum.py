"""Finite-window pendulum diagnostics from full-step position/speed samples."""
from __future__ import annotations

import math


def single_pendulum_reference(scene: dict, bob_id: str) -> dict | None:
    """Analytic periods only for an undamped, released-from-rest ideal single pendulum."""
    bodies, links, fields = scene.get("entities", []), scene.get("connections", []), scene.get("force_fields", [])
    if (len(bodies) != 2 or len(links) != 1 or len(fields) != 1
            or scene.get("colliders") or scene.get("interactions", {}).get("mutual_gravity")
            or scene.get("world", {}).get("gravity") != [0.0, 0.0, 0.0]):
        return None
    anchor, bob = bodies
    link, field = links[0], fields[0]
    if (anchor.get("type") != "point_mass" or bob.get("type") != "point_mass"
            or not anchor.get("fixed") or bob.get("fixed") or bob.get("id") != bob_id
            or link.get("type") != "rod" or link.get("entities") != [anchor.get("id"), bob_id]
            or field.get("type") != "uniform" or field.get("targets") != [bob_id]
            or field.get("start_time") != 0 or field.get("end_time", 0) < scene["world"]["duration"]
            or any(abs(v) > 1e-12 for v in bob.get("velocity", []))
            or any(abs(v) > 1e-12 for v in anchor.get("velocity", []))):
        return None
    acceleration = field.get("acceleration", [])
    if (len(acceleration) != 3 or acceleration[0] != 0 or acceleration[2] != 0
            or acceleration[1] >= 0):
        return None
    dx = bob["position"][0] - anchor["position"][0]
    dy = bob["position"][1] - anchor["position"][1]
    dz = bob["position"][2] - anchor["position"][2]
    length = link["rest_length"]
    if abs(dz) > 1e-10 or abs(math.hypot(dx, dy) - length) > max(1e-10, length * 1e-8):
        return None
    amplitude = abs(math.atan2(dx, -dy))
    if not 1e-9 < amplitude < math.pi:
        return None
    gravity = -acceleration[1]
    small = 2 * math.pi * math.sqrt(length / gravity)
    a, b = 1.0, math.cos(amplitude / 2)
    for _ in range(64):
        next_a, next_b = (a + b) / 2, math.sqrt(a * b)
        a, b = next_a, next_b
        if abs(a - b) <= 1e-15 * a:
            break
    return {"length_m": length, "gravity_m_s2": gravity,
            "release_angle_rad": amplitude,
            "release_side": "positive_x" if dx > 0 else "negative_x",
            "small_angle_period_s": small,
            "finite_amplitude_period_s": small / a,
            "assumptions": "Ideal point mass, massless rod, uniform gravity, zero initial speed and no damping; the finite-amplitude reference is one complete return to the release state."}


def pendulum_report(run) -> dict:
    """Energy and rod-length consistency for the registered ideal systems."""
    scene = run.scene
    bodies = scene["entities"]
    links = scene.get("connections", [])
    fields = scene.get("force_fields", [])
    n = len(links)
    expected_ids = ["anchor"] + [f"bob{i}" for i in range(1, n+1)]
    if (n not in (1, 2) or [e["id"] for e in bodies] != expected_ids
            or any(e["type"] != "point_mass" for e in bodies)
            or not bodies[0]["fixed"] or any(e["fixed"] for e in bodies[1:])
            or any(link["type"] != "rod" or link.get("entities") != expected_ids[i:i+2] for i,link in enumerate(links))
            or len(fields) != 1 or fields[0]["type"] != "uniform"
            or set(fields[0]["targets"]) != set(expected_ids[1:])
            or fields[0]["acceleration"][0] != 0 or fields[0]["acceleration"][2] != 0
            or fields[0]["acceleration"][1] >= 0 or fields[0]["start_time"] != 0
            or fields[0]["end_time"] < scene["world"]["duration"]
            or scene["interactions"]["mutual_gravity"] or "coupling" in scene):
        raise ValueError("Pendulum analysis requires a registered one/two-rod gravity-only system")
    gravity = -fields[0]["acceleration"][1]
    count = len(run.times)
    kinetic, potential = [0.]*count, [0.]*count
    positions = [tuple(bodies[0]["position"]) for _ in range(count)]
    angles, max_error = {}, 0.
    scale, reach = 0., 0.
    for body, link in zip(bodies[1:], links):
        ident, mass, length = body["id"], body["mass"], link["rest_length"]
        xyz = list(zip(*(run.metric(ident,"centroid",axis=a) for a in "xyz")))
        speeds = run.metric(ident,"speed")
        angles[ident] = [math.atan2(p[0]-q[0],-(p[1]-q[1])) for p,q in zip(xyz,positions)]
        max_error = max(max_error,max(abs(math.dist(p,q)-length) for p,q in zip(xyz,positions)))
        for i,(position,speed) in enumerate(zip(xyz,speeds)):
            kinetic[i] += .5*mass*speed*speed
            potential[i] += mass*gravity*(position[1]-bodies[0]["position"][1])
        reach += length
        scale += mass*gravity*reach
        positions = xyz
    energy = [a+b for a,b in zip(kinetic,potential)]
    drift = max(abs(e-energy[0]) for e in energy)
    return {"times_s":list(run.times), "angles_rad":angles,
            "model_parameters":{"masses_kg":[e["mass"] for e in bodies[1:]], "lengths_m":[e["rest_length"] for e in links], "gravity_m_s2":gravity},
            "kinetic_energy_J":kinetic, "potential_energy_J":potential, "total_energy_J":energy,
            "peak_energy_drift_J":drift, "gravitational_energy_scale_J":scale,
            "peak_energy_drift_fraction_of_scale":drift/scale,
            "maximum_rod_length_error_m":max_error,
            "source":"solver_macro_steps", "long_term_stability_proven":False,
            "interpretation":"Numerical consistency for ideal point masses and rods over this sampled window; not a proof of chaos or future stability."}


def compare_pendulums(first, second, *, entity: str = "bob2", separation_m: float = .1) -> dict:
    """Compare sampled sensitivity; this is not a Lyapunov-exponent estimate."""
    if not isinstance(separation_m,(int,float)) or isinstance(separation_m,bool) or not math.isfinite(separation_m) or separation_m <= 0:
        raise ValueError("separation_m must be finite and positive")
    a, b = pendulum_report(first), pendulum_report(second)
    if a["model_parameters"] != b["model_parameters"]:
        raise ValueError("Sensitivity comparison requires the same masses, lengths and gravity")
    if abs(first.times[0]-second.times[0])>1e-10 or abs(first.times[-1]-second.times[-1])>1e-10:
        raise ValueError("Trajectory comparison requires the same physical observation window")
    positions_a = list(zip(*(first.metric(entity,"centroid",axis=axis) for axis in "xyz")))
    positions_b = list(zip(*(second.metric(entity,"centroid",axis=axis) for axis in "xyz")))
    times, differences = [], []
    i = j = 0
    while i < len(first.times) and j < len(second.times):
        delta = first.times[i]-second.times[j]
        if abs(delta) <= 1e-10:
            times.append(first.times[i])
            differences.append(math.dist(positions_a[i],positions_b[j]))
            i += 1
            j += 1
        elif delta < 0:
            i += 1
        else:
            j += 1
    if len(times) < 2:
        raise ValueError("Trajectory comparison requires at least two matching sample times")
    crossing = next((i for i,d in enumerate(differences) if d>=separation_m),None)
    return {"entity":entity, "times_s":times, "separation_m":differences,
            "alignment":"matching macro-step timestamps only; no interpolation",
            "sample_counts":{"first":len(first.times),"second":len(second.times),"common":len(times)},
            "initial_separation_m":differences[0], "maximum_separation_m":max(differences),
            "threshold_m":separation_m,
            "first_sample_at_threshold_s":times[crossing] if crossing is not None else None,
            "threshold_status":"observed" if crossing is not None else "not_observed",
            "peak_energy_drift_fractions":[a["peak_energy_drift_fraction_of_scale"],b["peak_energy_drift_fraction_of_scale"]],
            "chaos_proven":False,
            "interpretation":"Observed finite-window trajectory separation. Check timestep convergence separately; numerical error can also grow in sensitive trajectories."}
