"""Opt-in shared-clock contact coupling and rotating rigid-body contract."""
from __future__ import annotations

import math
from typing import Any

from physics_demo.core.math3d import finite_number, finite_vec
from physics_demo.core.rigid_body import inertia, rotate

RIGID_FIELDS = {"id", "type", "shape", "position", "velocity", "mass", "orientation",
                "angular_velocity", "fixed", "pivot", "friction", "restitution"}
CAPABILITIES = {
    "algorithm": "Shared C11 clock: existing DFSPH and XPBD steppers, pairwise spring impulses, finite-mass contact reactions and quaternion rigid rotation",
    "activation": "Set scene.coupling={} for mixed scenes; rigid_body selects this route automatically.",
    "contacts": ["particles-rigid_body", "particles-mesh", "particles-solid_connection", "particles-point_mass_sphere", "mesh-rigid_body", "mesh-point_mass_sphere"],
    "attachments": ["point_mass center", "mesh vertex", "rigid_body local point"],
    "limits": {"particles": 4096, "mesh_vertices": 2048, "rigid_bodies": 16, "connections": 128, "substeps": 64},
    "scope": "Partitioned two-way contact, not a monolithic fluid pressure/solid solve or calibrated buoyancy. Discrete cross-domain contacts require timestep refinement. No cutting/fracture or coil self-contact.",
}


def enabled(scene: dict) -> bool:
    return "coupling" in scene or any(isinstance(e, dict) and e.get("type") == "rigid_body" for e in scene.get("entities", []))


def normalize_rigid(entity: dict, path: str, errors: list[dict]) -> None:
    def issue(key, message):
        errors.append({"code": "rigid_body_parameter", "path": path+"."+key, "message": message})
    for key, default in (("position", [0., 1., 0.]), ("angular_velocity", [0., 0., 0.])):
        value = entity.setdefault(key, default)
        if not finite_vec(value) or any(abs(x)>10000 for x in value):
            issue(key, "Use a finite vec3 within ±10000.")
        else:
            entity[key] = [float(x) for x in value]
    q = entity.setdefault("orientation", [1., 0., 0., 0.])
    if (not isinstance(q, list) or len(q)!=4 or not all(finite_number(x) for x in q)
            or abs(sum(x*x for x in q)-1)>1e-6):
        issue("orientation", "Use a unit quaternion [w,x,y,z], mapping body coordinates to world coordinates.")
    else:
        norm = math.sqrt(sum(x*x for x in q)); entity["orientation"] = [float(x)/norm for x in q]
    for key, default, low, high in (("mass", 1., 1e-6, 1e6), ("friction", .2, 0., 5.), ("restitution", 0., 0., 1.)):
        value = entity.setdefault(key, default)
        if not finite_number(value) or not low<=value<=high:
            issue(key, f"Use a finite number in [{low:g},{high:g}].")
        else:
            entity[key] = float(value)
    fixed = entity.setdefault("fixed", False)
    if type(fixed) is not bool:
        issue("fixed", "Use a JSON boolean.")
    elif fixed and (any(entity["velocity"]) or (finite_vec(entity["angular_velocity"]) and any(entity["angular_velocity"]))):
        issue("fixed", "A fixed body must have zero linear and angular velocity.")
    shape = entity.get("shape")
    if not isinstance(shape, dict) or shape.get("type") not in ("sphere", "box", "cylinder"):
        issue("shape", "Use sphere, box or cylinder geometry centred at the COM; cylinders use local Y as their axis.")
        return
    fields = {"sphere": {"type","radius"}, "box": {"type","size"}, "cylinder": {"type","radius","height"}}[shape["type"]]
    if set(shape)!=fields:
        issue("shape", "Supply exactly the dimensions required for this shape.")
    values = shape.get("size", []) if shape["type"]=="box" else [shape.get("radius")] + ([shape.get("height")] if shape["type"]=="cylinder" else [])
    if not isinstance(values, list) or len(values)!=(3 if shape["type"]=="box" else 2 if shape["type"]=="cylinder" else 1) or any(not finite_number(x) or not 1e-4<=x<=100 for x in values):
        issue("shape", "All shape dimensions must lie in [0.0001,100] m.")
    if "pivot" in entity:
        pivot = entity["pivot"]
        if not isinstance(pivot, dict) or set(pivot)!={"point","local_point"} or not all(finite_vec(pivot.get(k)) for k in ("point","local_point")):
            issue("pivot", "Use {point:[world xyz],local_point:[COM-relative body xyz]}.")
        elif fixed or sum(abs(x)>1e-12 for x in pivot["local_point"])>1 or any(abs(x)>10000 for x in pivot["point"]) or math.hypot(*pivot["local_point"])>100:
            issue("pivot", "Pivot must be on a body principal axis, within 100 m of COM; fixed and pivot are mutually exclusive.")
        elif not errors:
            offset = rotate(entity["orientation"], pivot["local_point"])
            expected = [entity["position"][i]+offset[i] for i in range(3)]
            if math.dist(expected, pivot["point"])>1e-7:
                issue("pivot", "Position, quaternion and local pivot must already place the support at the stated world point.")
            omega = entity["angular_velocity"]
            velocity = [omega[1]*(-offset[2])-omega[2]*(-offset[1]), omega[2]*(-offset[0])-omega[0]*(-offset[2]), omega[0]*(-offset[1])-omega[1]*(-offset[0])]
            if math.dist(velocity, entity["velocity"])>1e-7*max(1,math.hypot(*velocity)):
                issue("velocity", "A pivoted body's COM velocity must equal angular_velocity × (COM−pivot).")


def endpoints(link: dict) -> list[dict]:
    return link.get("endpoints", [{"entity": ident} for ident in link.get("entities", [])])


def attachment_initial(endpoint: dict, entity: dict) -> tuple[list[float], list[float], float]:
    if entity["type"] == "mesh":
        vertex = endpoint["vertex"]
        p = [x+y for x,y in zip(entity["mesh"]["vertices"][vertex], entity["position"])]
        fixed = entity["motion"] != "soft" or vertex in entity["pinned_vertices"]
        v = [0.,0.,0.] if entity["motion"] == "static" or vertex in entity["pinned_vertices"] else entity["velocity"]
        return p, v, 0. if fixed else len(entity["mesh"]["vertices"])/entity["mass"]
    if entity["type"] == "rigid_body":
        r = rotate(entity["orientation"], endpoint.get("local_point", [0.,0.,0.]))
        w = entity["angular_velocity"]
        v = [w[1]*r[2]-w[2]*r[1],w[2]*r[0]-w[0]*r[2],w[0]*r[1]-w[1]*r[0]]
        lever = [endpoint.get("local_point", [0.,0.,0.])[i]-entity.get("pivot", {}).get("local_point", [0.,0.,0.])[i] for i in range(3)]
        inv = 0. if entity["fixed"] else (0. if entity.get("pivot") else 1/entity["mass"])+sum(x*x for x in lever)/min(inertia(entity))
        return [x+y for x,y in zip(entity["position"],r)], [x+y for x,y in zip(entity["velocity"],v)], inv
    return entity["position"], entity["velocity"], 0. if entity["fixed"] else 1/entity["mass"]


def validate_scene(scene: dict, errors: list[dict]) -> None:
    def issue(code, path, message):
        errors.append({"code":code,"path":path,"message":message})
    entities = {e["id"]:e for e in scene["entities"]}
    if scene["budget"]["backend"] == "python" or scene["interactions"]["mutual_gravity"]:
        issue("coupling_route", "coupling", "Coupled scenes require native/auto and mutual_gravity=false; there is no approximation fallback.")
    if scene["world"]["duration"] < 1e-4:
        issue("coupling_duration", "world.duration", "Coupled duration must be at least 0.0001 seconds.")
    if any(e["type"] not in {"point_mass","rigid_body","mesh","fluid","granular"} for e in entities.values()):
        issue("coupling_entity", "entities", "Use point_mass, rigid_body, mesh, fluid or granular; static geometry belongs in colliders.")
    if sum(e["type"]=="rigid_body" for e in entities.values())>16:
        issue("coupling_body_limit","entities","Use at most 16 rotating bodies.")
    settings = scene.setdefault("coupling", {})
    if not isinstance(settings, dict):
        issue("coupling_settings","coupling","Expected a settings object."); return
    for key in set(settings)-{"substeps","iterations","friction"}:
        issue("unknown_field","coupling."+key,"Unknown coupling setting.")
    defaults = {"preview":(4,3),"balanced":(8,5),"high":(12,8)}[scene["budget"]["quality"]]
    for key, default in zip(("substeps","iterations"),defaults):
        v=settings.setdefault(key,default)
        if type(v) is not int or not 1<=v<=(64 if key=="substeps" else 16):
            issue("coupling_settings","coupling."+key,"Substeps use integers 1–64; iterations use integers 1–16.")
    friction=settings.setdefault("friction",.2)
    if not finite_number(friction) or not 0<=friction<=5:
        issue("coupling_settings","coupling.friction","Use friction in [0,5].")
    for e in entities.values():
        if e["type"]=="point_mass":
            if e["mass"] < 1e-9:
                issue("coupling_point_mass","entities","Coupled point masses must be at least 1e-9 kg.")
            radius=e.setdefault("collision_radius",0.)
            if not finite_number(radius) or not (radius==0 or 1e-4<=radius<=10):
                issue("point_collision_radius","entities","Point collision_radius must be zero (no contact), or in [0.0001,10] m.")
            if e["fixed"] and any(e["velocity"]):
                issue("fixed_velocity","entities","Fixed point masses require zero velocity.")
    links=scene.setdefault("connections",[])
    if not isinstance(links,list) or len(links)>128:
        issue("connection_limit","connections","Use at most 128 coupled connections.");return
    seen=set()
    for i,link in enumerate(links):
        path=f"connections[{i}]"
        if not isinstance(link,dict):
            issue("connection_type",path,"Expected a connection object.");continue
        ident,kind=link.get("id"),link.get("type")
        if not isinstance(ident,str) or not ident.strip() or len(ident)>128 or ident in seen:
            issue("connection_id",path+".id","Connection IDs must be nonempty, unique strings, at most 128 characters.")
        else:seen.add(ident)
        if kind not in ("spring","rod","rope"):
            issue("connection_kind",path+".type","Use spring, rod or rope.");continue
        allowed={"id","type","entities","endpoints","rest_length","solid"}|({"stiffness","damping"} if kind=="spring" else set())
        if "break_tensile_strain" in link:
            issue("unsupported_connection_fracture",path+".break_tensile_strain",
                  "Mixed coupling has fixed connections; tensile spring failure is available only in standalone point-mass scenes.")
        for key in set(link)-allowed-{"break_tensile_strain"}:issue("unknown_field",path+"."+key,"Unknown connection field.")
        if ("entities" in link)==("endpoints" in link):
            issue("connection_target",path,"Specify exactly one of entities:[idA,idB] or endpoints:[{entity,...},{entity,...}].");continue
        if "entities" in link and (not isinstance(link["entities"],list) or any(not isinstance(x,str) for x in link["entities"])):
            issue("connection_target",path,"entities must contain IDs.");continue
        ends=endpoints(link)
        if not isinstance(ends,list) or len(ends)!=2 or any(not isinstance(e,dict) or not isinstance(e.get("entity"),str) or e["entity"] not in entities for e in ends):
            issue("connection_target",path,"Supply exactly two existing endpoint entities.");continue
        valid=True
        for j,end in enumerate(ends):
            e=entities[end["entity"]]; typ=e["type"]
            required={"entity","vertex"} if typ=="mesh" else {"entity"}
            optional={"local_point"} if typ=="rigid_body" else set()
            if not required<=set(end) or set(end)-required-optional or typ not in {"mesh","rigid_body","point_mass"}:
                issue("connection_attachment",path+f".endpoints[{j}]","Bind a point center, a mesh integer vertex or a rigid body's local_point.");valid=False
            elif typ=="mesh" and (type(end["vertex"]) is not int or not 0<=end["vertex"]<len(e["mesh"]["vertices"])):
                issue("connection_attachment",path,"Mesh vertex index is out of range.");valid=False
            elif typ=="rigid_body" and "local_point" in end and (not finite_vec(end["local_point"]) or math.hypot(*end["local_point"])>100):
                issue("connection_attachment",path,"local_point must be finite and within 100 m of the COM.");valid=False
        params={"rest_length":(1e-6,1000)}
        if kind=="spring":
            link.setdefault("damping",0.);params.update(stiffness=(1e-9,1e7),damping=(0,1e5))
        for key,(low,high) in params.items():
            v=link.get(key)
            if not finite_number(v) or not low<=v<=high:
                issue("connection_parameter",path+"."+key,f"Use a finite number in [{low},{high}].");valid=False
            else:link[key]=float(v)
        if "solid" in link:
            solid=link["solid"]
            if not isinstance(solid,dict) or set(solid)-{"radius","mass"} or not finite_number(solid.get("radius")) or not 1e-4<=solid["radius"]<=1:
                issue("solid_connection",path+".solid","Use {radius:meters,mass:kg}; a straight collision capsule, not resolved helical wire.");valid=False
            else:
                mass=solid.setdefault("mass",0.)
                if not finite_number(mass) or not 0<=mass<=1e3 or (mass>0 and any(entities[e["entity"]]["type"]!="point_mass" for e in ends)):
                    issue("solid_connection_mass",path+".solid.mass","Mass must be in [0,1000] kg. Positive link mass is lumped half to each point_mass endpoint; other attachments require mass=0.");valid=False
        if valid:
            pa,va,wa=attachment_initial(ends[0],entities[ends[0]["entity"]]);pb,vb,wb=attachment_initial(ends[1],entities[ends[1]["entity"]])
            distance=math.dist(pa,pb)
            if ends[0]["entity"] == ends[1]["entity"] and entities[ends[0]["entity"]]["type"] == "rigid_body":
                issue("connection_attachment",path,"A connection cannot join two points of the same rigid body.")
            if wa+wb==0 or ends[0]==ends[1] or (kind!="rope" and distance<=1e-9):
                issue("connection_attachment",path,"Need distinct separated endpoints with at least one movable degree of freedom.")
            tol=max(1e-9,1e-7*link["rest_length"])
            if (kind=="rod" and abs(distance-link["rest_length"])>tol) or (kind=="rope" and distance-link["rest_length"]>tol):
                issue("initial_connection_length",path,"Rod length must match initially; ropes may start slack, never overlong.")
            if kind=="rod" and distance>1e-9 and abs(sum((vb[k]-va[k])*(pb[k]-pa[k]) for k in range(3))/distance)>1e-7:
                issue("initial_connection_velocity",path,"Rod endpoints require initially zero axial relative speed.")
    if "connection_settings" in scene:
        issue("coupling_settings","connection_settings","Use one coupling settings block for the shared clock; standalone connection_settings does not apply.")


def make_plan(
    scene: dict,
    include_video: bool,
    *,
    legacy_video_estimate: bool = False,
) -> dict[str, Any]:
    from physics_demo.io.planning import make_plan as particle_plan
    from physics_demo.io.mesh_scene import make_mesh_plan
    from physics_demo.analysis.queries import observation_plan
    world=scene["world"];entities=scene["entities"];settings=scene["coupling"]
    particle_scene={**scene,"entities":[e for e in entities if e["type"] in {"fluid","granular"}],"queries":[]}
    particle_scene.pop("coupling",None);particle_scene.pop("connections",None)
    plan=particle_plan(
        particle_scene,
        include_video=include_video,
        particle_cap_override=4096,
        legacy_video_estimate=legacy_video_estimate,
    )
    plan.update(backend="coupled",threads=1,coupling_rigid_bodies=sum(e["type"]=="rigid_body" for e in entities),
                coupling_points=sum(e["type"]=="point_mass" for e in entities),connection_count=len(scene["connections"]))
    meshes=[e for e in entities if e["type"]=="mesh"]
    if meshes:
        mp=make_mesh_plan({**scene,"entities":meshes,"queries":[]},False)
        for key in ("mesh_vertices","mesh_triangles","mesh_frame_vertex_samples","mesh_substeps","mesh_iterations","mesh_fits_limits"):
            plan[key]=mp[key]
        plan["estimated_peak_memory_mb"]+=mp["estimated_peak_memory_mb"]
        if not mp["initial_bounds_feasible"]:
            plan["initial_bounds_feasible"]=False;plan["initial_bounds_violations"]+=mp["initial_bounds_violations"]
    by_id={e["id"]:e for e in entities};omega2=gamma=0.
    from physics_demo.core.mesh_solver import _topology
    mesh_inverse = {}
    for e in meshes:
        areas = _topology(e, 0)[1]
        total = sum(areas)
        mesh_inverse[e["id"]] = [total/(e["mass"]*area) if e["motion"] == "soft" and i not in e["pinned_vertices"] and area > 0 else 0. for i,area in enumerate(areas)]
    for link in scene["connections"]:
        inverse=sum(mesh_inverse[end["entity"]][end["vertex"]] if end["entity"] in mesh_inverse else attachment_initial(end,by_id[end["entity"]])[2] for end in endpoints(link))
        if link["type"]=="spring":
            omega2+=2*link["stiffness"]*inverse;gamma+=2*link["damping"]*inverse
    dt=world["dt"]
    substeps=max(settings["substeps"],plan.get("mesh_substeps",1),math.ceil(dt*math.sqrt(omega2)/.05),math.ceil(dt*gamma/.25))
    features = [1.]
    if plan["planned_particles"]:
        features.append(plan["effective_spacing"])
    features += [e["mesh"]["metadata"]["min_edge"] for e in meshes]
    features += [e["collision_radius"] for e in entities if e["type"]=="point_mass" and e["collision_radius"]>0]
    features += [link["solid"]["radius"] for link in scene["connections"] if "solid" in link]
    initial_speed = max(math.hypot(*e["velocity"]) for e in entities)
    if meshes or plan["planned_particles"]:
        for e in entities:
            if e["type"] == "rigid_body":
                shape = e["shape"]
                radius = .5*math.hypot(*shape["size"]) if shape["type"]=="box" else math.hypot(shape["radius"],.5*shape.get("height",0))
                initial_speed = max(initial_speed,math.hypot(*e["velocity"])+radius*math.hypot(*e["angular_velocity"]))
    cfl_steps = math.ceil(dt*max(initial_speed+math.hypot(*world["gravity"])*dt,1e-6)/(.2*min(features)))
    substeps = max(substeps,cfl_steps)
    plan["coupling_initial_cfl_substeps"] = cfl_steps
    events = {}
    for field in scene.get("force_fields", []):
        for time in (field["start_time"],field["end_time"]):
            if 0 < time < world["duration"]:
                step = math.floor(time/dt)
                if abs(time-step*dt)>1e-12 and abs(time-(step+1)*dt)>1e-12:
                    events.setdefault(step,set()).add(time)
    event_headroom = max((len(times) for times in events.values()),default=0)
    plan.update(coupling_substeps=substeps,coupling_event_headroom=event_headroom,coupling_iterations=settings["iterations"],max_substeps=64)
    vertices=plan.get("mesh_vertices",0);triangles=plan.get("mesh_triangles",0);particles=plan["planned_particles"]
    nodes, bodies, links = plan["coupling_points"], plan["coupling_rigid_bodies"], len(scene["connections"])
    rigid_samples = sum({"sphere":1,"box":8,"cylinder":24}[e["shape"]["type"]] for e in entities if e["type"]=="rigid_body")
    solid_links = sum("solid" in link for link in scene["connections"])
    colliders = len(scene["colliders"])
    contact_work = ((particles+vertices)*(bodies+nodes+links)*4
                    + (particles+nodes)*(1+math.log2(triangles+1))*12
                    + nodes*(nodes+bodies+links+colliders)*4
                    + rigid_samples*(triangles*12+(bodies+colliders)*8)
                    + 65*solid_links*(colliders+bodies)*8)
    step_work = particles*80 + vertices*plan.get("mesh_iterations",1)*6 + links*16 + bodies*48 + nodes*8
    cost = plan["steps"]*(substeps+event_headroom)*(step_work+settings["iterations"]*contact_work)
    plan["estimated_peak_memory_mb"] += plan["output_frames"]*(plan["coupling_points"]*3+plan["coupling_rigid_bodies"]*7)*128/1e6
    plan["coupling_fits_limits"]=substeps+event_headroom<=64 and vertices<=2048 and plan["steps"]<=100000 and cost<=2e9 and plan.get("mesh_fits_limits",True)
    plan["resolution_policy"]="One shared clock and paired reaction impulses; explicit mesh topology and solid/link materials are preserved. Particle spacing follows the existing explicit preparation adjustments."
    plan["coupling_work_units"]=cost
    plan["adjustments"] += ([f"Coupling substeps increased from {settings['substeps']} to {substeps} for spring/mesh resolution."] if substeps!=settings["substeps"] else [])
    if scene.get("queries"):
        plan["measurement_plan"]=observation_plan(scene,plan);plan["estimated_peak_memory_mb"]+=plan["measurement_plan"]["estimated_memory_mb"]
    solver=.05+cost*3e-8;video=.3+plan["output_frames"]*(particles+vertices+plan["coupling_rigid_bodies"]*64)*2e-5 if include_video else 0.
    p90=5+2*(solver+video)
    plan["timing_estimate"]={"physical_duration_s":world["duration"],"solver_p50_s":round(solver,3),"solver_p90_s":round(solver*2,3),"video_p50_s":round(video,3),"video_p90_s":round(video*2,3),"cold_compile_p90_s":5.,"total_p50_s":round(solver+video,3),"total_p90_s":round(p90,3),"hard_limit_s":scene["budget"]["wall_time_s"],"fits_budget":p90<=scene["budget"]["wall_time_s"],"hardware_profile":"provisional coupled work model","confidence":"provisional; benchmark target hardware","limiting_factor":"partitioned contacts and existing field solvers"}
    return plan


def result_checks(scene: dict, plan: dict, trajectory: dict) -> dict[str, bool]:
    """Verify mixed presentation channels and reuse each domain's quality checks."""
    d = trajectory["diagnostics"]
    points = [e for e in scene["entities"] if e["type"] == "point_mass"]
    rigids = [e for e in scene["entities"] if e["type"] == "rigid_body"]
    meshes = [e for e in scene["entities"] if e["type"] == "mesh"]
    if trajectory.get("gravity_body_ids") != [e["id"] for e in points] or trajectory.get("rigid_ids") != [e["id"] for e in rigids] or trajectory.get("rigid_shapes") != [e["shape"] for e in rigids]:
        raise ValueError("Coupled body IDs or shapes disagree with the prepared scene")
    masses = {e["id"]: float(e["mass"]) for e in points}
    for link in scene["connections"]:
        for end in endpoints(link):
            if end["entity"] in masses:
                masses[end["entity"]] += link.get("solid", {}).get("mass", 0.) / 2
    if trajectory.get("point_effective_masses") != masses:
        raise ValueError("Coupled effective point masses disagree with the solid spring contract")
    counts = {"p": min(plan["planned_particles"], plan["render_particle_limit"]), "g": len(points), "r": len(rigids), "m": plan.get("mesh_vertices", 0)}
    q_error = 0.
    for i, frame in enumerate(trajectory["frames"]):
        t = min(i / scene["world"]["output_fps"], scene["world"]["duration"])
        if not isinstance(frame, dict) or not finite_number(frame.get("t")) or abs(frame["t"]-t)>1e-7:
            raise ValueError("Coupled frame timestamps disagree with the prepared schedule")
        for key, count in counts.items():
            values = frame.get(key)
            if not isinstance(values, list) or len(values) != count or any(not finite_vec(p) for p in values):
                raise ValueError("Coupled frame channel "+key+" has an invalid population")
        qs = frame.get("q")
        if not isinstance(qs, list) or len(qs) != len(rigids) or any(not isinstance(q,list) or len(q)!=4 or not all(finite_number(x) for x in q) for q in qs):
            raise ValueError("Coupled quaternion channel is malformed")
        for j, entity in enumerate(points):
            if (i == 0 or entity["fixed"]) and math.dist(frame["g"][j], entity["position"])>1e-8:
                raise ValueError("Initial or fixed point positions disagree with the scene")
        for j, entity in enumerate(rigids):
            q = qs[j]
            q_error = max(q_error, abs(sum(x*x for x in q)-1))
            if i == 0 or entity["fixed"]:
                if math.dist(frame["r"][j],entity["position"])>1e-8 or min(math.dist(q,entity["orientation"]),math.dist(q,[-x for x in entity["orientation"]]))>1e-8:
                    raise ValueError("Initial or fixed rigid pose disagrees with the scene")
            if entity.get("pivot"):
                rotated = rotate(q, entity["pivot"]["local_point"])
                if math.dist([frame["r"][j][a]+rotated[a] for a in range(3)], entity["pivot"]["point"])>1e-8:
                    raise ValueError("Rigid pose violates its fixed pivot")
    values = ("max_penetration_m", "max_speed_m_s", "max_quaternion_error", "contact_impulse_norm", "attachment_impulse_norm", "max_link_constraint_error")
    if any(not finite_number(d.get(k)) or d[k]<0 for k in values) or type(d.get("contact_count")) is not int or d["contact_count"]<0 or any(not finite_vec(d.get(k)) for k in ("initial_momentum", "final_momentum", "support_impulse")):
        raise ValueError("Coupled contact/rotation diagnostics are malformed")
    checks = {"coupled_unit_quaternions": max(q_error,d["max_quaternion_error"])<1e-8,
              "coupled_substep_budget": d["max_substeps_used"]<=64,
              "coupled_link_constraints_within_tolerance": d["max_link_constraint_error"]<=.002}
    if meshes:
        from physics_demo.io.mesh_scene import result_checks as mesh_checks
        view = {**trajectory, "frames": [{**f,"p":[],"g":[],"r":[]} for f in trajectory["frames"]],
                "diagnostics": {**d, **d["mesh_diagnostics"]}}
        checks.update(mesh_checks({**scene,"entities":meshes},plan,view))
    return checks
