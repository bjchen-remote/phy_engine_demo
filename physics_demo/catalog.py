"""Canonical starting scenes and stable patch points for agent workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_contract import choose_action, guided, tool_action
from .jsonio import loads as strict_json_loads
from .limits import MAX_SCENE_FILE_BYTES


EXAMPLE_CATALOG: dict[str, dict[str, Any]] = {
    "self_gravitating_liquid": {
        "file": "self_gravitating_liquid.json",
        "use_for": ["mutually attracting liquid blobs", "liquid merger with self-gravity"],
        "limitations": "Incompressible visual particles with one reference density and explicit scaled G; not stellar gas or point-mass/particle exchange.",
        "mechanism_boundary": {
            "modeled": ["particle_self_gravity", "single_pressure_phase"],
            "unavailable": ["particle_point_mass_gravity_exchange", "compressible_gas"],
        },
        "common_patches": ["/interactions/gravity_G", "/interactions/softening", "/interactions/gravity_theta", "/interactions/particle_gravity_density", "/world/duration"],
    },
    "three_body_queries": {
        "file": "three_body_queries.json",
        "use_for": ["finite-window three-body stability", "quantitative orbit data"],
        "limitations": "Sampled radius/separation criteria and conservation checks cannot prove long-term stability.",
        "common_patches": ["/world/duration", "/world/dt", "/queries/@orbit-bounds/max_radius", "/queries/@orbit-bounds/min_separation"],
    },
    "droplet_radius_query": {
        "file": "droplet_radius_query.json",
        "use_for": ["time to projected spread radius", "liquid radius time series"],
        "limitations": "Uses every particle center including airborne spray; a visual-model measurement, not calibrated wetting-footprint CFD.",
        "common_patches": ["/queries/@radius-1m/value", "/queries/@radius-1m/hold_for", "/world/dt", "/entities/@drop/shape/center/1"],
    },
    "sliders_separate": {
        "file": "sliders_separate.json",
        "use_for": ["slider separation time", "edge-gap time series", "1D friction and collision"],
        "limitations": "Standalone common-x-rail translation only, no rotation, rail end walls, or fluid coupling.",
        "common_patches": ["/entities/@left/velocity/0", "/entities/@right/velocity/0", "/queries/@separate-1m/value"],
    },
    "three_body": {
        "file": "three_body.json",
        "use_for": ["three-body orbit", "softened N-body gravity"],
        "limitations": "Point-mass model only; use self_gravitating_liquid for particle-only self-gravity. Point-mass/particle exchange is unavailable.",
        "common_patches": ["/world/duration", "/entities/@body-a/position", "/entities/@body-a/velocity"],
    },
    "droplet_ground": {
        "file": "droplet_ground.json",
        "use_for": ["millimetre water drop onto explicitly dry ground", "impact and spreading on a dry floor"],
        "limitations": "Single-phase visual liquid on a dry static floor; this model may spread without airborne splash. No calibrated wetting/contact angle or surrounding air.",
        "common_patches": ["/entities/@drop/shape/center/1", "/entities/@drop/shape/radius", "/budget/quality"],
    },
    "droplet_ground_splash": {
        "file": "droplet_ground_splash.json",
        "use_for": ["visible large water-drop splash onto dry ground", "ordinary unscaled water drop onto ground when a splash is wanted"],
        "inherited_assumptions": [
            "A 0.32 m radius water body starts 1.55 m above dry ground under Earth gravity.",
            "The dramatic large-scale splash is a visual-modeling choice, not a millimetre droplet prediction.",
            "The ±4 m horizontal collision walls stay outside the recorded 0.8 s impact window.",
        ],
        "limitations": "This 0.8 s macroscopic dry-floor scene shows early radial spray without recorded side-wall contact. It is a visual reference, not a millimetre-drop or experimentally calibrated prediction. Keep explicit user dimensions even if the result spreads less.",
        "common_patches": ["/entities/@drop/shape/center/1", "/entities/@drop/shape/radius", "/world/duration"],
    },
    "droplet_ground_macro_wet": {
        "file": "droplet_ground_macro_wet.json",
        "use_for": ["visible large water-drop splash onto a prewetted ground", "0.32 m radius drop over a 4 cm water film"],
        "inherited_assumptions": [
            "A 0.32 m radius water body starts 1.55 m above the ground under Earth gravity.",
            "A finite 1.0 m square, 4 cm deep water film already covers the impact area.",
            "The drop and film use surface_tension=0.055, a 10% increase over the 0.05 trial baseline.",
        ],
        "limitations": "The upward spray depends on a pre-existing 4 cm water film. Surface tension is an uncalibrated model coefficient, not a direct measurement of molecular attraction. This visual single-phase SPH scene has no resolved air, calibrated wetting, or validated splash threshold; do not describe it as a dry-ground result.",
        "common_patches": ["/entities/@drop/shape/center/1", "/entities/@drop/shape/radius", "/entities/@film/shape/size", "/entities/@drop/properties/surface_tension", "/entities/@film/properties/surface_tension", "/world/duration"],
    },
    "droplet_ground_micro_wet": {
        "file": "droplet_ground_micro_wet.json",
        "use_for": ["visible 3 mm water drop splash onto a thin prewetted film", "millimetre-scale splash when a wet surface is acceptable"],
        "inherited_assumptions": [
            "A 3 mm radius drop starts 25 mm above a 1.2 mm water film under Earth gravity.",
            "The wet film is a finite 10 mm square and is required for the visible upward droplets in this preset.",
        ],
        "limitations": "Visual single-phase SPH approximation; this example does not demonstrate a dry-ground microdroplet crown or calibrated contact angle.",
        "common_patches": ["/entities/@drop/shape/center/1", "/entities/@drop/shape/radius", "/entities/@film/shape/size", "/world/duration"],
    },
    "droplet_cone": {
        "file": "droplet_cone.json",
        "use_for": ["water drop onto an upright cone", "visible water flowing down a conical mesh"],
        "inherited_assumptions": [
            "A 0.09 m radius water body falls onto a static 0.6 m high cone with a 0.35 m base radius.",
            "The cone is copper colored to keep the blue water visible; the physical window ends at 0.8 s before most spray reaches the finite walls.",
        ],
        "limitations": "Fluid-mesh contact is qualitative: the mesh has collision response but no pressure-boundary density support, wetting adhesion, fluid-cone friction or calibrated contact angle. Jet heights and splash thresholds have not been validated against experiments; upward droplets alone are not evidence of numerical error. World bounds are collision walls, not camera framing.",
        "common_patches": ["/entities/@drop/shape/radius", "/entities/@drop/shape/center/1", "/entities/@cone/color", "/world/duration", "/world/bounds"],
    },
    "liquid_preset_showcase": {
        "file": "liquid_preset_showcase.json",
        "use_for": ["compare water, honey, glue, and molten lead presets", "continuous-liquid rendering acceptance"],
        "customer_prompts": [
            "Compare water, honey, white glue, and molten lead in one short drop test.",
            "用同一组落滴展示水、蜂蜜、胶水和铅水的差异。",
        ],
        "inherited_assumptions": [
            "Four equal-size finite drops start at staggered heights above one static floor.",
            "Every preset shares the engine's single fluid pressure phase and reference density.",
        ],
        "limitations": "Preset coefficients and appearance are visual models; density, temperature, phase change, adhesion, non-Newtonian rheology, and immiscibility are absent.",
        "physics_boundary": "Use this to compare supported visual behavior and rendering, not real material properties or process safety.",
        "mechanism_boundary": {
            "modeled": ["single_pressure_phase", "visual_liquid_presets"],
            "unavailable": ["density_contrast", "phase_change", "non_newtonian_rheology"],
        },
        "common_patches": [
            "/entities/@water-drop/shape/center/1",
            "/entities/@honey-drop/shape/center/1",
            "/entities/@glue-drop/shape/center/1",
            "/entities/@lead-drop/shape/center/1",
        ],
    },
    "droplet_pool": {
        "file": "droplet_pool.json",
        "use_for": ["drop into water", "pool impact"],
        "limitations": "Single-phase visual liquid; air is not resolved.",
        "common_patches": ["/entities/@drop/shape/center/1", "/entities/@pool/shape/size", "/budget/quality"],
    },
    "droplet_sphere": {
        "file": "droplet_sphere.json",
        "use_for": ["water drop onto a static ball", "liquid flowing around a spherical obstacle"],
        "limitations": "A 0.09 m water body meets a static 0.28 m sphere in a 0.8 s window. This is a sphere obstacle, not a dry-floor splash example; wetting, air and breakup are not calibrated.",
        "common_patches": ["/entities/@drop/shape/center/1", "/entities/@ball/shape/radius", "/budget/quality"],
    },
    "water_blob": {
        "file": "water_blob.json",
        "use_for": ["free water ball", "liquid blob motion"],
        "limitations": "Free liquid volume; there is no elastic membrane.",
        "common_patches": ["/entities/@water-blob/velocity", "/entities/@water-blob/shape/radius", "/world/gravity"],
    },
    "sandcastle_wash": {
        "file": "sandcastle_wash.json",
        "use_for": ["sandcastle washed by water", "qualitative erosion"],
        "limitations": "Visual wetting and drag proxy, not calibrated soil mechanics.",
        "common_patches": ["/interactions/water_sand_drag", "/interactions/wetting_rate", "/budget/quality"],
    },
    "high_detail_droplet_sphere": {
        "file": "high_detail_droplet_sphere.json",
        "use_for": ["high-detail water drop onto a static sphere", "sphere obstacle resolution benchmark"],
        "limitations": "A 0.12 m drop contacts a static 0.28 m sphere; use only for sphere requests. High quality uses a 120 second budget and is not calibrated for wetting or air-driven breakup.",
        "common_patches": ["/entities/@drop/shape/center/1", "/budget/wall_time_s"],
    },
    "geyser": {
        "file": "geyser.json",
        "use_for": ["timed upward water jet", "prescribed geyser"],
        "limitations": "The upward push is a prescribed field, not a resolved pump.",
        "mechanism_boundary": {
            "modeled": ["finite_initial_liquid", "timed_prescribed_force"],
            "unavailable": ["continuous_inflow", "resolved_pump", "discharge_rate"],
        },
        "common_patches": ["/force_fields/@upward-burst/acceleration/1", "/force_fields/@upward-burst/end_time"],
    },
    "sand_blast": {
        "file": "sand_blast.json",
        "use_for": ["radial blast through sand", "granular dispersal"],
        "limitations": "The blast is prescribed acceleration; gas and fracture are absent.",
        "common_patches": ["/force_fields/@blast-pulse/strength", "/force_fields/@blast-pulse/radius"],
    },
    "whirlpool": {
        "file": "whirlpool.json",
        "use_for": ["whirlpool in a tank", "vortex-driven liquid with a visible free surface"],
        "customer_prompts": [
            "Spin a shallow tank of water into a clear whirlpool, then let it coast.",
            "让浅水槽中的水形成明显旋涡，停止驱动后继续惯性旋转。",
        ],
        "inherited_assumptions": [
            "A 1.1 second analytic vortex pulse spins one finite shallow water volume.",
            "Four thick static box walls contain the water; the remaining motion is passive coast-down.",
        ],
        "limitations": "The vortex is prescribed acceleration, not a resolved impeller or turbulence model.",
        "physics_boundary": "Use the circulation and free-surface shape visually; do not infer torque, pump power, or calibrated mixing time.",
        "reference_prepare": {"budget_s": 55, "particles": 6272, "p50_s": 23.233, "p90_s": 41.653},
        "common_patches": ["/force_fields/@tank-vortex/strength", "/force_fields/@tank-vortex/inward_strength"],
    },
    "water_obstacle_course": {
        "file": "water_obstacle_course.json",
        "use_for": ["water around posts", "capsule obstacle course"],
        "limitations": "Capsules have rounded ends and all obstacles are static.",
        "common_patches": ["/entities/@water-charge/velocity", "/colliders/@post-middle/radius"],
    },
    "zero_g_droplet_collision": {
        "file": "zero_g_droplet_collision.json",
        "use_for": ["two liquid blobs colliding", "zero-gravity drops"],
        "limitations": "Visual surface tension without surrounding air.",
        "common_patches": ["/entities/@left-drop/velocity", "/entities/@right-drop/velocity"],
    },
    "product_pour_tumbler": {
        "file": "product_pour_tumbler.json",
        "use_for": ["beverage pour into a glass", "finite product-pour advertisement"],
        "customer_prompts": [
            "Pour a premium drink into a low tumbler and let it splash and settle.",
            "做一条饮品从左上方倒入玻璃杯并在杯中稳定下来的产品演示。",
        ],
        "inherited_assumptions": [
            "The liquid is a finite preloaded volume launched from an off-screen source.",
            "The tumbler is an open static box with a one-metre model width.",
        ],
        "limitations": "No continuous emitter, moving bottle, glass optics, bubbles, foam, or air phase.",
        "physics_boundary": "Use as a visual pour and containment shot, not a fill-rate or slosh-load measurement.",
        "mechanism_boundary": {
            "modeled": ["finite_initial_liquid", "static_container"],
            "unavailable": ["continuous_inflow", "moving_bottle", "fill_rate", "gas_phase"],
        },
        "reference_prepare": {"budget_s": 55, "particles": 1152, "p50_s": 7.618, "p90_s": 15.205},
        "common_patches": [
            "/entities/@product-liquid/velocity",
            "/entities/@product-liquid/properties/viscosity",
            "/entities/@product-liquid/shape/size",
        ],
    },
    "fountain_hoop": {
        "file": "fountain_hoop.json",
        "use_for": ["fountain through a decorative hoop", "event or plaza concept shot"],
        "customer_prompts": [
            "Launch a compact fountain burst through a horizontal metal ring and let it fall back.",
            "让一股喷泉穿过八边形装饰环，然后在重力下落回地面。",
        ],
        "inherited_assumptions": [
            "Eight static capsules approximate a horizontal circular hoop.",
            "A finite water charge receives one timed upward acceleration pulse.",
        ],
        "limitations": "No pump, continuous jet, nozzle pressure, atomized mist, or moving sculpture.",
        "physics_boundary": "The ring contact and ballistic arc are visual; the prescribed lift cannot estimate pump power.",
        "mechanism_boundary": {
            "modeled": ["finite_initial_liquid", "timed_prescribed_force", "static_hoop"],
            "unavailable": ["continuous_inflow", "nozzle_pressure", "pump_power"],
        },
        "reference_prepare": {"budget_s": 55, "particles": 1331, "p50_s": 10.067, "p90_s": 19.364},
        "common_patches": [
            "/force_fields/@fountain-pulse/acceleration/1",
            "/force_fields/@fountain-pulse/end_time",
            "/entities/@fountain-water/properties/surface_tension",
        ],
    },
    "flood_bridge_piers": {
        "file": "flood_bridge_piers.json",
        "use_for": ["dam-release concept", "flood water splitting around bridge piers"],
        "customer_prompts": [
            "Visualize a short flood release splitting around three bridge piers for a stakeholder presentation.",
            "做一个泄洪水体绕过三根桥墩的工程概念动画。",
        ],
        "inherited_assumptions": [
            "The release is one finite rectangular water slug in a straight channel.",
            "A half-second uniform push stands in for the omitted upstream reservoir head.",
        ],
        "limitations": "No free-surface calibration, turbulence model, scour, debris, structural response, or continuous inflow.",
        "physics_boundary": "Suitable for flow-path communication only; do not infer loads, discharge, flood level, or safety margins.",
        "mechanism_boundary": {
            "modeled": ["finite_initial_liquid", "static_piers", "timed_prescribed_force"],
            "unavailable": ["continuous_inflow", "discharge_rate", "structural_load", "scour"],
        },
        "reference_prepare": {"budget_s": 55, "particles": 4480, "p50_s": 24.509, "p90_s": 43.849},
        "common_patches": [
            "/entities/@flood-water/velocity/0",
            "/force_fields/@release-push/acceleration/0",
            "/colliders/@pier-center/radius",
        ],
    },
    "sand_sculpture_wave": {
        "file": "sand_sculpture_wave.json",
        "use_for": ["wave destroys a sand sculpture", "resort or educational erosion shot"],
        "customer_prompts": [
            "Make a resort ad shot where one wave knocks down a twin-tower sand sculpture.",
            "模拟一股海浪冲垮双塔沙雕，用于海边活动宣传。",
        ],
        "inherited_assumptions": [
            "The sculpture is assembled from three touching granular boxes.",
            "One finite water slug represents the incoming wave on a flat beach.",
        ],
        "limitations": "Wet-sand weakening and drag are qualitative; there is no sediment transport, surf, or calibrated soil law.",
        "physics_boundary": "Use for a destruction beat and relative visual tuning, not erosion rates or coastal design.",
        "mechanism_boundary": {
            "modeled": ["finite_initial_liquid", "qualitative_wetting_drag"],
            "unavailable": ["calibrated_erosion", "sediment_transport", "soil_strength"],
        },
        "reference_prepare": {"budget_s": 55, "particles": 4549, "p50_s": 21.121, "p90_s": 38.078},
        "common_patches": [
            "/entities/@wave/velocity/0",
            "/interactions/water_sand_drag",
            "/interactions/wetting_rate",
        ],
    },
    "microgravity_three_drops": {
        "file": "microgravity_three_drops.json",
        "use_for": ["three-drop coalescence", "microgravity liquid education or pharmaceutical concept"],
        "customer_prompts": [
            "Show three medication microdrops arriving from different directions and merging in microgravity.",
            "展示三颗液滴在微重力中依次碰撞并合并。",
        ],
        "inherited_assumptions": [
            "All drops are the same single-phase Newtonian visual liquid.",
            "Zero world gravity and the initial velocities define the entire approach motion.",
        ],
        "limitations": "No gas film, surfactant, contact-angle, breakup calibration, chemistry, or physical micrometre scale.",
        "physics_boundary": "The collision sequence is illustrative; it cannot predict pharmaceutical mixing or flight hardware behaviour.",
        "reference_prepare": {"budget_s": 55, "particles": 3624, "p50_s": 6.288, "p90_s": 12.905},
        "common_patches": [
            "/entities/@dose-left/velocity",
            "/entities/@dose-right/velocity",
            "/entities/@dose-top/velocity",
        ],
    },
    "cosmetic_hero_splash": {
        "file": "cosmetic_hero_splash.json",
        "use_for": ["cosmetic product splash", "droplet impact around a hero object"],
        "customer_prompts": [
            "Create a premium cosmetic shot where one glossy drop hits an orb above a reflecting pool.",
            "做一条大液滴撞击产品球体并落入浅水盘的美妆广告镜头。",
        ],
        "inherited_assumptions": [
            "A static sphere stands in for the product pack and a finite second volume forms the shallow pool.",
            "The liquid uses elevated visual surface tension; camera and material colours are renderer defaults.",
        ],
        "limitations": "No label, bottle geometry, glass refraction, air, bubbles, foam, or calibrated cosmetic rheology.",
        "physics_boundary": "Suitable for splash timing and silhouette ideation, not product viscosity measurement or packaging loads.",
        "reference_prepare": {"budget_s": 55, "particles": 6223, "p50_s": 22.667, "p90_s": 40.705},
        "common_patches": [
            "/entities/@hero-drop/shape/center/1",
            "/entities/@hero-drop/properties/surface_tension",
            "/colliders/@hero-orb/radius",
        ],
    },
    "vehicle_wading_splash": {
        "file": "vehicle_wading_splash.json",
        "use_for": ["vehicle wading splash concept", "water splitting around wheels and chassis"],
        "customer_prompts": [
            "Make an automotive concept shot of a shallow water front striking four wheels and a chassis.",
            "模拟浅水迎面撞上四个车轮和底盘的汽车涉水宣传镜头。",
        ],
        "inherited_assumptions": [
            "A finite moving water slug replaces vehicle translation through a continuous puddle.",
            "Static capsules and one box approximate the wheels and chassis.",
        ],
        "limitations": "No rotating tyres, moving vehicle, aerodynamics, spray droplets below particle scale, or structural coupling.",
        "physics_boundary": "Use only for visual flow paths and shot design; do not infer wading depth, ingress, drag, or safety.",
        "mechanism_boundary": {
            "modeled": ["moving_finite_liquid", "static_vehicle_proxy"],
            "unavailable": ["moving_vehicle", "wheel_rotation", "water_ingress", "vehicle_drag"],
        },
        "reference_prepare": {"budget_s": 55, "particles": 3645, "p50_s": 14.112, "p90_s": 26.205},
        "common_patches": [
            "/entities/@road-water/velocity/0",
            "/force_fields/@approach-momentum/acceleration/0",
            "/colliders/@vehicle-body/center/1",
        ],
    },
}


for _name, _target, _use in (
    ("mesh_soft_drop", "soft", "soft triangle-mesh drop and elastic deformation"),
    ("mesh_soft_collision", "left", "two deformable meshes colliding"),
    ("mesh_ring_insertion", "ring", "prescribed tapered plug opening a soft ring"),
    ("mesh_cloth_drape", "cloth", "open soft sheet draping over a triangle obstacle"),
    ("mesh_image_extrusion", "star", "image silhouette with explicitly imagined depth"),
    ("mesh_imagined_shape", "sculpture", "agent-authored irregular closed mesh falling onto a step"),
):
    EXAMPLE_CATALOG[_name] = {
        "file": _name + ".json", "use_for": [_use],
        "limitations": "Elastic triangle-surface proxy, not calibrated volumetric FEM. No self-collision, cutting, edge-edge CCD guarantee or liquid coupling; image depth is an authored assumption.",
        "common_patches": [f"/entities/@{_target}/mesh", f"/entities/@{_target}/position/1",
                           "/world/dt", "/world/duration", "/budget/quality"],
    }


for _name, _use in (
    ("spring_oscillator", "undamped spring period, force and first rest-length crossing"),
    ("damped_spring", "damped oscillation and decay"),
    ("coupled_springs", "coupled oscillator energy transfer"),
    ("rod_pendulum", "fixed-length pendulum"),
    ("rope_catch", "slack rope catching a falling point mass"),
    ("spring_chain", "elastic chain and travelling disturbance"),
    ("spring_pendulum", "coupled spring extension and pendulum motion"),
):
    EXAMPLE_CATALOG[_name] = {
        "file": _name + ".json", "use_for": [_use],
        "limitations": "Ideal massless links between point masses, explicit fields only. No node/link collisions, solid rotation, fracture, fluid or mesh coupling; ropes catch inelastically.",
        "common_patches": ["/world/duration", "/world/dt", "/connections/0/rest_length",
                           "/entities/1/mass", "/entities/1/position"],
    }


# Explicitly opted-in mixed examples preserve the existing discovery interface.
EXAMPLE_CATALOG.update({'gyroscope_precession': {'file': 'gyroscope_precession.json',
                          'use_for': ['gyroscope precession',
                                      'fixed-pivot spinning top',
                                      'physical quaternion rotation'],
                          'customer_prompts': ['Show a tilted spinning top precessing under '
                                               'gravity.',
                                               '让倾斜陀螺绕固定支点真实进动，并测量自旋和倾角。'],
                          'inherited_assumptions': ['A uniform cylinder spins about its local Y '
                                                    'axis with one fixed principal-axis pivot.',
                                                    'The initial COM velocity is consistent with '
                                                    'the supplied world angular velocity and '
                                                    'pivot.'],
                          'limitations': 'Rigid pivot torque and quaternion motion; no bearing '
                                         'model, rolling tip or calibrated friction. Editing '
                                         'pose/spin requires preserving pivot position and COM '
                                         'velocity consistency.',
                          'common_patches': ['/entities/@top/shape/radius',
                                             '/entities/@top/angular_velocity',
                                             '/entities/@top/shape/height',
                                             '/world/gravity/1',
                                             '/world/duration']},
 'torque_free_tumble': {'file': 'torque_free_tumble.json',
                        'use_for': ['asymmetric free rigid-body tumbling',
                                    'world angular momentum conservation',
                                    'rotational energy time series'],
                        'customer_prompts': ['Let an asymmetric box tumble freely and track its '
                                             'angular momentum.',
                                             '让长方体自由翻转，检查角动量和转动能量。'],
                        'limitations': 'A uniform rigid box in zero gravity without applied '
                                       'torque; invariant accuracy belongs to this configured '
                                       'isolated model.',
                        'common_patches': ['/entities/@body/shape/size',
                                           '/entities/@body/angular_velocity',
                                           '/world/dt',
                                           '/world/duration']},
 'spring_rigid_pendulum': {'file': 'spring_rigid_pendulum.json',
                           'use_for': ['spring attached off-centre to a rigid body',
                                       'pendulum with rigid-body torque',
                                       'rotating spring bob'],
                           'customer_prompts': ['Attach a spring off-centre to a box so it swings '
                                                'and rotates.',
                                                '弹簧偏心连接刚体，观察受力产生的摆动和转动。'],
                           'limitations': 'Ideal axial spring/damper and rigid local-point torque. '
                                          'A non-solid drawn helix is an annotation, not collision '
                                          'wire.',
                           'common_patches': ['/connections/@spring/stiffness',
                                              '/connections/@spring/damping',
                                              '/entities/@body/mass',
                                              '/world/duration']},
 'water_spring_reaction': {'file': 'water_spring_reaction.json',
                           'use_for': ['water pushes a spring-attached ball',
                                       'two-way liquid and spring reaction',
                                       'finite-mass contact response'],
                           'customer_prompts': ['Push a spring-mounted ball with water and show '
                                                'the reaction.',
                                                '水流撞击弹簧连接的小球，观察两向动量交换。'],
                           'limitations': 'Partitioned finite-radius impulses and point-mass '
                                          'translation; no point spin, dynamic boundary-pressure '
                                          'solve, calibrated drag or buoyancy.',
                           'common_patches': ['/entities/@water/velocity',
                                              '/entities/@bob/mass',
                                              '/entities/@bob/collision_radius',
                                              '/connections/@spring/stiffness']},
 'solid_spring_water': {'file': 'solid_spring_water.json',
                        'use_for': ['water contacts a spring with physical thickness',
                                    'straight elastic collision capsule',
                                    'spring mass and water reaction'],
                        'customer_prompts': ['Give the spring a real collision radius so it can '
                                             'block water.',
                                             '弹簧有真实厚度和质量，水不能直接穿过画出来的线。'],
                        'limitations': 'The solid is a straight capsule, not helical wire. '
                                       'Positive link mass is lumped half to each point endpoint; '
                                       'no distributed bending or coil contact.',
                        'common_patches': ['/connections/@tube/solid/radius',
                                           '/connections/@tube/solid/mass',
                                           '/connections/@tube/stiffness',
                                           '/entities/@water/velocity']},
 'ghost_spring_water': {'file': 'ghost_spring_water.json',
                        'use_for': ['non-colliding spring control scene',
                                    'compare visible coil with physical solid link'],
                        'customer_prompts': ['Compare the same water shot with the spring '
                                             'collision solid removed.',
                                             '对照组只保留弹簧示意线，验证它不会阻挡水。'],
                        'limitations': 'Control for solid_spring_water: the helix has no collision '
                                       'geometry. Water can subsequently hit world bounds, so do '
                                       'not treat final total momentum as a closed-system '
                                       'comparison.',
                        'common_patches': ['/connections/@tube/stiffness',
                                           '/entities/@water/velocity',
                                           '/world/duration',
                                           '/world/dt']},
 'water_soft_sheet': {'file': 'water_soft_sheet.json',
                      'use_for': ['water deforms a soft mesh',
                                  'liquid and cloth reaction',
                                  'two-way point-triangle contact'],
                      'customer_prompts': ['Drop water onto a soft sheet and show the sheet '
                                           'deforming.',
                                           '水撞击柔软网格薄片，薄片也要有受力形变。'],
                      'limitations': 'XPBD surface and barycentric contact reaction; no membrane '
                                     'pressure FSI, calibrated material stress or guaranteed '
                                     'watertightness.',
                      'common_patches': ['/entities/@sheet/mass',
                                         '/entities/@sheet/edge_compliance',
                                         '/entities/@water/velocity',
                                         '/world/dt']},
 'water_sheet_spring_rigid': {'file': 'water_sheet_spring_rigid.json',
                              'use_for': ['water, mesh, spring and rotating rigid body together',
                                          'coupled local attachments',
                                          'shared-clock multiphysics contact'],
                              'customer_prompts': ['Connect a soft sheet to a rotating body with a '
                                                   'spring, then hit it with water.',
                                                   '水、软体薄片、弹簧和旋转刚体在一个时钟内相互作用。'],
                              'limitations': 'Partitioned shared-clock contact and attachment '
                                             'impulses. Discrete sampled contact needs refinement; '
                                             'no complete fluid-structure pressure solve or '
                                             'calibrated force claims.',
                              'common_patches': ['/connections/@tether/stiffness',
                                                 '/entities/@body/mass',
                                                 '/entities/@sheet/edge_compliance',
                                                 '/coupling/iterations',
                                                 '/world/dt']}})


def catalog_summary() -> list[dict[str, Any]]:
    return [
        {"name": name, **{key: value for key, value in item.items() if key != "file"}}
        for name, item in EXAMPLE_CATALOG.items()
    ]


def _example_path(filename: str) -> Path:
    """Use checkout assets when present, otherwise the installed package copy."""
    package = Path(__file__).resolve().parent
    source = package.parent / "examples" / filename
    return source if source.is_file() else package / "data" / "examples" / filename


def example(name: str) -> dict[str, Any]:
    item = EXAMPLE_CATALOG.get(name)
    if item is None:
        return guided({
            "ok": False,
            "stage": "arguments",
            "errors": [{
                "code": "unknown_example",
                "path": "name",
                "message": f"Unknown example {name!r}.",
                "retryable": True,
                "suggestion": f"Choose one of: {', '.join(EXAMPLE_CATALOG)}.",
            }],
        }, "needs_example_choice", tool_action("physics_example", "Choose a listed canonical example."))
    path = _example_path(item["file"])
    if path.stat().st_size > MAX_SCENE_FILE_BYTES:
        raise ValueError("canonical example unexpectedly exceeds the scene input limit")
    scene = strict_json_loads(path.read_text(encoding="utf-8"))
    customer_metadata = {
        key: item[key]
        for key in ("customer_prompts", "inherited_assumptions", "physics_boundary", "reference_prepare", "mechanism_boundary")
        if key in item
    }
    return guided({
        "ok": True,
        "example": name,
        "use_for": item["use_for"],
        "limitations": item["limitations"],
        **customer_metadata,
        "assumption_rule": "Every canonical dimension, layout, velocity, material value, field value, duration, and camera bound not explicitly patched is an inherited modeling assumption; track and disclose the important ones even when validator assumptions is empty.",
        "common_patches": item["common_patches"],
        "scene": scene,
        "scene_json": json.dumps(scene, separators=(",", ":"), ensure_ascii=False),
    }, "example_loaded", choose_action(
        "Compare every explicit user value and requested mechanism with the example. Preserve a matching example's explicit fluid properties; do not reapply generic water defaults. Check mechanism_boundary before treating an example as a match.",
        [
            tool_action("physics_liquid", "Resolve a newly requested liquid material before patching; preserve an example's explicit material properties when the liquid is unchanged.", when="The prompt changes the example's named liquid or introduces a new fluid entity."),
            tool_action("physics_patch", "Patch all explicit differences atomically.", when="The prompt changes any example value."),
            tool_action("physics_prepare", "Validate and plan the unchanged example.", when="The example already matches the prompt."),
        ],
    ))
