# Physics 1.5.0 / pipeline 0.4.0 source candidate

The source candidate adds bounded mesh-volume liquid initialization, stricter
simulation-mesh selection, runtime operation contracts and portable physical data.
It does not activate an installed registry or change existing task pins.

| Component | Version |
| --- | --- |
| Physics engine | 1.5.0 |
| Composed pipeline | 0.4.0 |
| Modeling / rendering | 0.4.0 / 0.4.0 |
| Simulation adapter | 0.2.1 |

## Geometry and initialization

[Candidate selection](../toolboxes/modeling_flow/README.md) applies the actual
pinned engine auditor inside each candidate attempt. Topology, volume, scale and
bounded bidirectional sampled distances are recorded; a rejected or exhausted
candidate does not prevent trying the next deterministic target. Raw geometry is
preserved. Finite samples do not certify Hausdorff distance, wall thickness,
apertures, dynamic self-collision or image fidelity. Neural latent reuse remains
explicitly unsupported.

When both the modeling module and engine advertise support,
`modeling_liquefy` accepts a current model reference or an authorized task import,
plus explicit SI extent and particle spacing. It derives a sealed `fluid_ref` for
whole-entity scene insertion or replacement. Removing the last mesh also removes
its now-inapplicable mesh settings in the suggested patch transaction.

The shared [volume sampler](../physics_demo/core/volume_sampling.py) uses bounded,
deterministic interior lattice sampling. Planning and initialization use the same
result; coarsening resamples and budget overflow never truncates particles. The
first implementation is an instant volume conversion to water-like particles at
1000 kg/m³. It requires at most 5% volume discrepancy, sampled connectivity and
finite source-surface coverage. Those checks can reject lost thin appendages but
do not prove arbitrary thin-wall or pore preservation. This is initialization,
not thermal melting or mass-conserving material conversion. Full-scene overlap,
solver and numerical validation remain separate gates.

## Contracts and data

The pinned [operation catalogue](../toolboxes/pipeline/operation_contracts.json)
validates calls before mutations. Capability discovery lists supported operations;
`help(topic="operation:<name>")` provides the exact parameter/result descriptor.
The [v2 contracts](pipeline-v2/README.md) remain a design draft.

New rendering modules opt into `pipeline-data/2`. Scientific values are retained
while private locators are projected to archive-relative values or omitted.
Source hashes bind the original private documents; fixed read-only verification
reconstructs the full inventory and deterministic ZIP from those sources rather
than trusting producer success or self-reported hashes. GLB metadata is decoded
before locator checks. The original result, model, receipts and checkpoints stay
unchanged. Legacy renderers retain `pipeline-data/1` and its exact-source contract;
that format can contain local paths.

The [host contract](../toolboxes/pipeline/README.md) requires a compatible host
validator before activation. Runtime installation, real image inference and
platform delivery are independent acceptance layers.

## Offline validation

The engine suite completed 483 tests with six skips; the toolbox suite completed
386 tests with one skip in the integration environment. Targeted native liquid
and portable-data tests additionally verified read-only source reconstruction.
Synthetic mechanical and PCB jobs produced real MP4/ZIP outputs, passed the
separate host verifier, and rerendered without changing solver responses or
canonical result bytes. These checks did not download weights, run neural
inference or send platform messages.

```sh
python3 toolboxes/build_physics.py --check
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s toolboxes/tests -v
python3 docs/pipeline-v2/validate_contracts.py
```

Optional test dependencies and the separate host SDK affect explicit skips. The
host smoke harness requires `--host-source` or `QQ_SIMULATOR_HOST_SOURCE`; a missing
SDK is a setup error. Numerical C sources are maintained in
[physics_demo/core/native](../physics_demo/core/native/README.md), with Python
bridges under `core/`. Release mirrors are generated; flat Python modules remain
compatibility imports.
