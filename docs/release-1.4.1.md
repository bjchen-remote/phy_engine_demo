# Toolbox 1.4.1: host-owned runtime authorization

This release builds on the active 1.4.0 behavior. It carries forward the local
OneBot path that allows modeling, solving, and video rendering to continue
without a fixed wall-clock deadline. A scene cannot grant itself that mode:
only the host's task limit can authorize it. Finite-budget API callers keep
their existing 1–300 second range.

Structured validation and execution errors may be corrected repeatedly when
each attempt changes the model. An unchanged failed model is not a new retry
candidate. The public instructions no longer impose an arbitrary count of
corrections.

The 60-second simulated physical-duration limit, numerical and media integrity
checks, and 16 MiB delivery bound remain in force. This release does not
include QQ bridge code, messages, identity data, or private runtime records.

Verification: the packaged engine build check passed for 162 managed files;
all 28 toolbox regressions and the selected runtime and video regressions passed.
The new tests cover unauthorized scene markers,
host-authorized validation and saved-result inspection, finite budget bounds,
and the simulated-duration bound.
