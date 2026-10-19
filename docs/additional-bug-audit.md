# Additional bug audit

Date: 20 October 2026

This is the second audit, following the [original Figma findings](figma-plugin-bugs.md).
The baseline is commit `5d8d997`. Findings below are additional defects reproduced
against that baseline or the upstream MyoSuite development source, plus an
explicitly identified queue-handling issue caught while reviewing this audit's
own changes. Tests
exercise the maintained plugin under `integration/`; the downloaded original
project remains unchanged.

## New upstream MyoSuite defects

### Fatigue resets mutated the caller's configuration

`CumulativeFatigue.reset(fatigue_reset_vec=...)` retained the supplied NumPy array
as internal state. Advancing fatigue then changed the caller's reset vector.
Repeated episodes started from progressively different fatigue values; read-only
input could also fail when the simulation tried to write it.

[MyoSuite PR #558](https://github.com/MyoHub/myosuite/pull/558) copies the supplied
vector and tests input ownership, repeatable resets, and read-only arrays. The
new regression cases fail before the patch and pass afterward. Log2Motion also
copies the input at its own reset boundary while the upstream fix awaits release.

### Runtime mesh edits left cached renderers stale or closed

`MyoGymnasiumEnv._upload_mesh_to_renderers()` closed only the most recently used
offscreen renderer, leaving its closed instance in the resolution cache and
leaving other cached renderers' mesh data unchanged. With two cached resolutions,
one render returned stale pixels and the other raised `render cannot be called
after close`. Bimanual object randomization uses this mesh-update path.

[MyoSuite PR #559](https://github.com/MyoHub/myosuite/pull/559) adds a renderer
invalidation method that closes and removes every cached offscreen context, then
creates fresh contexts on demand. Two real-rendering regressions fail before the
patch and pass afterward. Current Log2Motion edits primitive target geometry, so
this second defect does not require a local mesh-rendering workaround.

## Bridge, history, and reports

### Deleted sessions could receive somebody else's delayed results

Session IDs were allocated from the largest *remaining* ID. Deleting the highest
session, or deleting all sessions, allowed its ID to be reused. A pending run or
chart upload for the deleted session could then enter a new, unrelated session.

The overview now retains a monotonically increasing `last_session_id`, including
when no sessions remain. Existing overviews are migrated from their current IDs
before deletion. Late chart uploads to deleted sessions or expired runs fail
explicitly instead of receiving a misleading success acknowledgement.

Regression coverage: deleting/recreating the highest session, deleting everything,
migrating a legacy overview, delayed simulation results, and rejected delayed
charts in [test_audit_regressions.py](../tests/test_audit_regressions.py).

### Another client's session selection could redirect a simulation

Capturing the active session on the server protected a run from later changes,
but still chose the wrong session if another plugin client had already changed
that global active session. The request now carries the initiating client's
`session_id`; the bridge validates and honors it. Legacy clients retain the
active-session fallback.

The regression creates two sessions, leaves the second globally active, runs a
simulation explicitly for the first, and verifies that only the first receives
the history. A nonexistent explicit session is rejected before inference.

### Disconnecting a client could monopolize the simulation lock

The handler awaited its simulation directly. If the client disconnected before
the next progress update, inference continued and retained the shared lock until
it finished or hit the 30-minute timeout. A reopened plugin could remain queued.

The handler now races each request against connection closure, cancels abandoned
work, and awaits its cleanup. Regression tests check that a subsequent client can
run and that a real abandoned subprocess is terminated and reaped.

This was an application lifecycle error, not a defect in the WebSockets library.

### Long click paths could make both PDF exports fail

All selected elements were placed inside a single table row. A legitimate
90-element path with moderately long names made that row taller than a page;
ReportLab correctly raised `LayoutError` because it could not split the row.

The element lists now use multiple rows and permit splitting a tall row's
contents. The regression generates both single-run and comparison PDFs for the
same 90-element path. This fixes the report layout rather than modifying
ReportLab.

### Downsampled chart times drifted from the actual simulation

Rounded downsampling indices are not uniformly spaced. Sending only their
average `dt`, then calculating the next gesture's offset as `count * dt`, shifted
contacts and inflated gesture durations. For example, six samples at 6 ms
intervals reduced to four samples have actual times `[.006, .018, .024, .036]`,
which cannot be reconstructed with one uniform interval.

Chart data now includes the retained `times` and actual `duration`. The UI uses
those values for time coordinates and gesture offsets, with a fallback for older
payloads. Python and UI regressions check the uneven intervals and the final
duration.

## Log2Motion simulation and CLI

The corresponding regression coverage is in
[test_simulation.py](../tests/test_simulation.py).

| Defect | Cause and fix | Reproduction |
| --- | --- | --- |
| Starting a fresh replay retained old muscle activation | The low-level reset preserved activations for continuous gesture phases, including explicit posture resets. Fresh posture/seed/out-of-phone resets now clear activation, while phase resets retain continuity. | Step until muscles are active, reset to the starting posture, and compare with a fresh environment. |
| Reading an observation changed later contact classification | Contact history advanced on every observation read, even at the same simulation time. State transitions now derive from a snapshot for that timestep. | Repeated reads at fixed time previously advanced the counter past 20 and cleared the contact flag without a physics step. |
| Invalid timing configuration was accepted, and other XML timesteps produced incorrect limits | Fractional frame skips were truncated, nonpositive limits were accepted, and the default limit assumed a 2 ms XML timestep. Configuration is validated and the limit is derived from the loaded model's actual control timestep. | Fractional/bool/nonpositive settings are rejected; a real 1 ms model receives the correct time limit. |
| Completion markers appended a phantom episode | `STATUS_TASK_COMPLETE` triggered another rest movement even though the preceding gesture already included its release. The sentinel now ends replay without adding an episode. | A tap followed by completion retains one gesture result and no extra interaction. |
| Failed Android startup or renderer cleanup leaked emulator resources | Android was launched before model initialization; teardown could skip Android if renderer cleanup failed. Launch now follows model setup, failed launches trigger cleanup, and Android teardown runs in a `finally` block. | Controlled startup and renderer failures verify cleanup, including repeated close calls. |
| Android swipes pressed the touchscreen while the simulated finger was airborne | Swipe replay sent touch-down for every sample, regardless of contact, and could keep pressing across a lift. Down events now require screen contact, and contact loss immediately sends an up event. | Air-only, contact-then-lift, and non-target screen-contact trajectories verify the event sequence. |
| Cleanup and JSON serialization failures broke the CLI result protocol | An exception in `env.close()` or `json.dumps()` escaped before output framing. Both now produce framed error JSON and a nonzero exit, preserving the original error when cleanup also fails. | A failing environment close and non-finite output each produce a complete, parseable error payload. |

The additional local fatigue regression verifies that a read-only fixed reset
vector remains unchanged across steps and repeated resets. This protects the
application independently of whether upstream PR #558 has been released.

## Figma plugin lifecycle and UI

The new UI tests execute the actual inline UI script with controlled DOM,
WebSocket, chart, timer, and image stand-ins. The Figma tests execute the compiled
plugin against a controlled Figma API. They reproduce races deterministically
rather than relying on timing during manual clicking.

| Defect | Cause and fix |
| --- | --- |
| Simulation errors left controls unusable | Error paths restored the start button incompletely, leaving visualization disabled, navigation hidden, or animation running. A shared recovery path restores all simulation controls and pauses the animation. |
| Delayed chart export saved the next run's images | Export callbacks read global Chart objects after a newer result had replaced them. Each run now owns its chart snapshot; an earlier run is captured before its canvases are destroyed. Labels and selected elements are captured with the run too. |
| Charts were lost when the socket disconnected during thumbnail/export work | Upload was attempted once and silently skipped while disconnected. Run/session-keyed uploads are retained and retried after reconnect, with matching acknowledgements and terminal stale-run errors removing only the corresponding upload. |
| Reconnection changed the meaning of a pending simulation | Visualization and session settings could be read again after the user or another client changed them. Pending requests retain the starting options and explicit session identity. |
| Late history and diagram responses overwrote another selection | Run IDs can repeat across sessions, and a delayed response could arrive after another run/session was selected. Requests identify their session; responses are checked against the current session and run before rendering. The bridge echoes the required identity fields. |
| A late comparison displayed old differences under newly selected run headers | The comparison response did not identify its run pair or session. Responses now echo both ordered run IDs and the session; the UI rejects responses that no longer match the selection. |
| Retryable chart-save failures discarded newly queued snapshots | Caught during review of this audit's new upload queue: all chart-save errors were treated as terminal, including storage `OSError`s. The bridge now marks retryable failures; the UI retains those snapshots and retries with bounded delays, while stale session/run errors discard only their matching upload. |
| Asynchronous export followed a changing click path | The export loop read mutable selection state across awaited node/thumbnail operations. It now freezes the requested order, and each exported node's geometry is captured before awaiting its thumbnail. |
| Deleted selected nodes silently disappeared from the simulation | Missing nodes were skipped, shortening the requested path without explanation. Export now returns an actionable error asking the user to review the missing selection. |
| Highlights changed the design's auto-layout | Highlight rectangles were inserted into the selected node's container and could become layout children, moving or resizing real UI elements. They are now page-level overlays positioned from the node's absolute transform. |
| A pending highlight appeared after selection mode finished | A delayed node lookup could create its overlay after Finish had already removed highlights. Highlight requests now carry lifecycle identity and discard obsolete completions. |
| Unrelated creation events suppressed subsequent user selections | Creation handling treated unrelated document nodes as plugin drawing activity. Event filtering now distinguishes owned drawing operations from user-created nodes. |
| In-progress connectors were removed as orphans | Cleanup could see a newly created owned vector before asynchronous drawing had registered it as complete. Pending owned connectors are tracked through their drawing lifecycle. |

Regression coverage: [Figma lifecycle tests](../integration/figma-plugin/tests/plugin_lifecycle.test.cjs),
[existing Figma API tests](../integration/figma-plugin/tests/export.test.cjs),
and [UI lifecycle tests](../integration/figma-plugin/tests/ui_lifecycle.test.cjs).
The timing regression described above additionally checks real chart dataset
coordinates against nonuniform sample times and exact gesture durations.

## Verification

- Native Windows Python 3.12: **126 tests passed** (60 simulation, 61
  bridge/history/PDF, five end-to-end), with the existing Gymnasium legacy-seed
  warning. The JUnit result is `runtime/pytest-additional-audit312.xml`.
- Native Windows Python 3.15: the 60 simulator, 61 bridge/history/PDF, and five
  real end-to-end cases also pass across their corresponding test runs. The
  existing Python 3.12–3.15 Windows/Linux CI matrix exercises the
  expanded suite; hosted Windows excludes its two graphics-driver-dependent
  cases, which are covered by native Windows and Linux.
- Plugin: **36 tests passed**, including 26 new lifecycle/UI regressions.
  TypeScript checking/build, ESLint, `npm audit`, and whitespace checks pass.
- Upstream fatigue fix: 108 core/wrapper tests pass, one skips; the new
  regressions also pass on native Python 3.15. Renderer/geometry coverage for
  the mesh fix: 45 tests pass. Pre-commit hooks pass for both PRs.
- The broader upstream required suite produced 312 passes, two skips, four
  expected failures, and four existing physics-parity failures also present on
  unmodified upstream. Those failures are disclosed in the PRs; expected
  physics values were not changed to hide them.

The two new upstream PRs have their own CI. Their checks can still be running
when the local validation finishes; consult the PR status for current results.

## Scope and interpretation

The upstream library paths checked include MyoSuite, MuJoCo, Gymnasium,
Stable-Baselines3/PyTorch policy inference, WebSockets, ImageIO/FFmpeg video,
Pillow textures, ReportLab PDFs, and Chart.js. A failing use of a library is not
automatically an upstream bug; the report distinguishes demonstrated library
defects from mistakes in Log2Motion's integration.

In particular, a native MuJoCo 3.16 check confirmed that changing `geom_size`
requires updating derived fields: `mj_setConst()` refreshed both the bounding
box and radius. This support is explicitly listed in the official
[MuJoCo 3.16 release notes, Engine item 7](https://github.com/google-deepmind/mujoco/releases/tag/3.16.0).
The geometry refresh already in Log2Motion is therefore not evidence of a new
MuJoCo defect. No speculative MuJoCo PR was filed.

Manual interaction inside Figma Desktop and a real Android emulator remains
outside the completed validation. Android lifecycle and touch behavior are
tested using controlled stand-ins; native MuJoCo inference, subprocesses,
WebSockets, PDFs, and video decoding are exercised directly.
