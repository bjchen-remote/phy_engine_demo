# Toolbox 1.4.3: coupled runtime planning and contact counters

## Preserve the active 1.4.2 interface

This release carries forward the data and period interface already present in
the active 1.4.2 package. Period queries use same-direction reference crossings
from solver observations and report unavailable results when sampling or cycle
consistency is insufficient. Ideal single-pendulum references retain their
explicit assumptions. Numerical data replies require verified numerical results
and declared, usable measurements; unavailable data is not inferred from video.
The existing schema, manuals, automatic single-pendulum query and PCB data
summary behavior are preserved without changing their runtime implementation.

## Coupling fixes

Host-authorized unlimited runtime now bypasses only the coupled work estimate
ceiling of two billion work units. Finite-budget calls keep that ceiling, and a
scene cannot authorize unlimited execution itself. The 64 shared-substep limit
(including force-event headroom), 100,000 macro-step limit, mesh resource and
topology limits, stiffness/contact checks and numerical validation remain.

Coupled contact counts use an unsigned 64-bit counter. Checked additions reject
overflow instead of wrapping or saturating, preserving the geometry refresh
trigger across the old signed 32-bit boundary. C, header and Python bindings
move together to coupled ABI 2; the native cache compiles this matched source
set on demand. Unrelated prebuilt solver and video binaries are unchanged.

The 60-second simulated-duration capability, finite-budget API behavior,
host-owned unlimited runtime, existing scene meanings, numerical/media checks
and delivery-size limit remain. These changes extend the actual active 1.4.2
baseline; they do not restore intervening 1.3.4–1.3.7 changes.

## Verification

The isolated candidate passed the 50 existing targeted coupled and runtime
budget regressions, synthetic planning guards, native counter boundary tests
under UBSan, and before/after synthetic contact comparisons. Public regressions
cover host authorization, finite work limits, retained resource/geometry gates,
64-bit contact accumulation, geometry refresh and explicit overflow failure.
The generated package is checked against its source and manifest. No saved
user models, chat records or messaging-host implementation are included.
