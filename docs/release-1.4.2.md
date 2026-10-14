# Toolbox 1.4.2: optional point-mass spring failure

This release builds on the active 1.4.1 behavior. A standalone point-mass
spring can now declare `break_tensile_strain`, a dimensionless extension divided
by rest length. The spring breaks irreversibly when sampled strain is strictly
above the threshold. Initial overstretch breaks at time zero; later failures
are sampled at completed native solver substeps. Failed springs exert no force
and store no elastic energy. Results record failure times and per-frame active
links, and videos omit failed links. Validation checks the reported break length
against the threshold and checks energy drift before a later failure; this
establishes internal consistency, not the provenance of an edited result file.

The feature is opt-in. Scenes without it retain their prior numerical output.
Mixed coupled connections, rods, ropes, continuum fracture, rigid-body fracture,
rain sources, and fluid-to-structure fracture remain unsupported. The point-mass
spring model has no collisions or calibrated material stress. This release does
not claim to resolve reports that require those capabilities or a particular
network topology and material law.

This version carries forward host-owned unlimited runtime for the local OneBot
path, the 60-second simulated physical-duration limit, numerical and media
integrity checks, and the 16 MiB video bound. It contains no QQ bridge code,
messages, identities, credentials, or private runtime records.

Cold native compiler version checks and rebuilds now use the caller's remaining
budget. The host-authorized unlimited path applies no compiler subprocess
timeout, removing the former 5-second and 30-second internal caps.

Verification: 428 engine tests passed (five optional skips), 28 toolbox tests
passed, and the generated package build check passed. Focused tests cover
connection and mixed-mode behavior, slow-motion video timing, false fracture
metadata, pre-fracture energy drift, and the native compiler budget. Native
ASan/UBSan checks and a rendered, inspected fracture video passed. A legacy
spring scene retained the same numerical output as active 1.4.1.
