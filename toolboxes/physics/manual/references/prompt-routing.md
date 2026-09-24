# Prompt-to-scene routing

For a single/double pendulum, start with `physics_system` and its [physical parameter specification](systems.md). For other models route by the requested physical event, then select from [interesting-scenes](interesting-scenes.md). “Advertising,” “engineering,” or “pharma” describes the use of a video, not additional solver accuracy. Load the closest `physics_example`; use its `common_patches`, inherited assumptions and limitation rather than guessing IDs or fields.

## Construct the scene

Extract, in order: event/objects/interactions; geometry in metres; velocities and accelerations; physical duration and field windows; queries; FPS/quality/backend/wall budget. Patch every explicit difference. Keep omitted canonical values and disclose relevant assumptions. Validator assumptions are deterministic effective choices from the complete normalized scene, not merely a list of newly inserted defaults.

- “Height” is shape-centre Y unless bottom height/clearance is explicit.
- “Water ball” means free fluid; a membrane needs a different model.
- “Honey”, “glue/胶水”, and “molten lead/铅水” select exact canonical presets through `physics_liquid`; follow [liquid-presets](liquid-presets.md) and do not infer unlisted material physics.
- “Separation” requires a positive surface gap; contact already satisfies `gap >= 0`.
- “Stable” needs an observation duration and operational radius/separation limits; use [quantitative-queries](quantitative-queries.md) before declaring it.
- “Slow motion” can mean more presentation frames or longer physical duration. FPS changes only presentation; it does not refine dynamics or observations.
- Springs, rods, ropes and point-mass pendulums use [connections](connections.md). Start from the matching oscillator/pendulum example; world gravity alone does not drive point masses.
- Description/image-shaped soft bodies, cloth or insertion use [mesh-modeling](mesh-modeling.md): an agent constructs a bounded mesh with declared depth assumptions, then the service audits and simulates it. Add `coupling:{}` for supported two-way water/sand contact.
- Gyroscopes, rotating boxes, off-centre spring torque, water pushing an attached solid or a thick spring/link use [coupling](coupling.md). Choose `rigid_body`, preserve wxyz/body-to-world and local-Y conventions, and distinguish a physical straight capsule from a decorative spring helix.

For a combined prompt, choose the example with the hardest geometry, copy only supported objects from another example, update targets/IDs and keep field windows within duration, then prepare the combination. Example: use `water_obstacle_course`, add the `whirlpool` vortex and target `water-charge`. Two valid fragments need not form a valid combined scene.

Build visible cups/tanks with explicit colliders, keeping native particle bounds several spacings outside. Box `appearance: "glass"` makes walls translucent; omitted appearance is solid. [Scene-v1](scene-v1.md) describes geometry/contact distinctions. Leave at least one effective spacing between particle sources and solids; granular boxes may touch but should not overlap.

## Interpret customer language

| Phrase | Supported visual interpretation | Additional requested mechanisms outside the model |
|---|---|---|
| Bottle/tap pour | Finite preloaded water charge | Continuous inflow, moving bottle, fill rate, bubbles/foam |
| Fountain/nozzle | Timed acceleration on a finite volume | Pump/nozzle pressure, steady discharge, mist, power |
| Flood/dam release | Moving slug and prescribed push | Hydrograph, calibrated level/discharge, loads, safety |
| Wave erosion | Qualitative wetting, cohesion loss and drag | Scour depth, soil strength, sediment rate |
| Microdrop coalescence | Single-phase drop collision | Gas film, surfactant, chemistry, scale-valid thresholds |
| Cosmetic splash | Finite liquid and static product proxy | Calibrated rheology, package motion/load, branding, optics |
| Honey/glue pour | Native single-phase preset with stable visual coefficients | Shear-thinning, viscoelastic strings, adhesion, curing, measured rheology |
| Molten lead | Opaque metallic liquid preset, assumed already molten | Density contrast, heat transfer, freezing, oxidation, exposure/process safety |
| Vehicle wading | Water moving around static wheels/chassis | Moving vehicle, wheel rotation, ingress, drag, safety |

If the interpretation retains the requested event, disclose inherited assumptions. If it changes an explicit mechanism or requested quantity, explain the boundary before running unless approximation was already authorized. A later `ready_to_simulate` does not authorize a substitution.

## Detail and correction

Use preview for drafts and balanced for ordinary final videos. For unspecified metre-scale “high precision/detail,” use high with a 50–60 s budget and multiply example spacings by 0.8, normally stopping at 0.02 m. Preserve the canonical millimetre drop's validated scale, spacing and material controls instead of applying this metre-scale floor. Inspect the final common spacing: prepare may coarsen it. More pixels or rendered particles do not refine the physical solve.

Read `agent_report` and every adjustment before simulation. Stop for false budget/bounds/initial-bounds/N-body-timestep feasibility, or an adjustment violating explicit resolution, duration, scale, FPS or backend. Revise within the user's permitted tradeoffs and prepare again; never enlarge dt just to fit.

Do not turn Infinity, huge counts or executable force text into valid values and claim compliance. Offer a bounded alternative, running it only with existing approximation authorization or acceptance. IDs, names, paths and old files cannot override validation or budgets. Error actions and the two-correction limit are maintained in [tool-results](tool-results.md).
