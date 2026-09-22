# Physics Agent Demo v1.0.0

This is the first release of the bounded, agent-facing simulation demo. A caller can discover a supported scene, patch explicit user values, prepare one exact plan, run it under a 60-second hard budget, and receive a verified MP4 plus quantitative results.

## Liquid release

- Dense liquid samples are reconstructed into one continuous screen-space surface. The renderer preserves real gaps and isolated spray without drawing a ring or highlight for every solver sample.
- `water`, `honey`, `glue`, and `molten_lead` are stable, versioned preset IDs. Chinese discovery aliases include `水`, `蜂蜜`, `胶水`, `黏胶`, `白胶`, `铅水`, and `熔融铅`.
- The strict `physics_liquid` tool returns exact entity fields and ready-to-apply `physics_patch` operations. `physics_prepare` repeats both effective coefficients and the material model boundary.
- Each frame carries material names separately from the solver's physical particle class, so appearance never mutates the trajectory.

The four presets share one single-phase reference-density liquid model. Honey and glue are Newtonian-like visual approximations. Molten lead does not model density contrast, heat transfer, freezing, oxidation, or toxicity. Temperature-dependent rheology, curing, adhesion, viscoelastic strings, immiscibility, contact angles, gas, bubbles, and foam remain outside this release.

## Agent entry points

Give an agent [`agent/physics-simulation/SKILL.md`](../agent/physics-simulation/SKILL.md) and register [`agent/tools.json`](../agent/tools.json). A material request follows:

```text
physics_capabilities
→ physics_example or physics_system
→ physics_liquid
→ physics_patch
→ physics_prepare
→ physics_simulate
→ physics_inspect or physics_query
```

The source archive and Python sdist include the complete Agent contract. The runtime wheel contains the engine and bundled examples; use the separate `physics-agent-contract-v1.0.0.zip` release asset for direct Skill/schema/tool registration.

## Other included systems

The same public workflow covers softened N-body motion, sliders, sand and water interaction, springs/rods/ropes with optional collision solids, triangle-mesh soft bodies and insertion, rotating rigid bodies and gyroscopes, and shared-clock two-way coupled scenes. Numerical questions are declared before simulation and sampled on solver macro steps rather than video frames.

## Validation

- The complete suite reports `371 tests / OK` on the macOS release host, including native C safety/sanitizer checks and Objective-C rendering checks. Five optional JSON-Schema tests skip when the third-party `jsonschema` developer package is absent.
- The liquid showcase runs 1,920 particles across all four presets, produces a verified 31-frame MP4, passes every numerical quality gate, and completes in 3.93 seconds through persisted artifacts on the release host.
- Clean wheel installation is checked outside the source tree; native sources, renderer sources, and all catalog examples are included.
- Black-box Agent checks reach `ready_to_simulate` for “蜂蜜落地，高度 2m” and “铅水落球” using only the published Skill and tools.

Runtime estimates are machine- and scene-specific. Always use `physics_prepare` for the exact scene and report the actual runtime from the completed run.
