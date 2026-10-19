# Figma plugin, Log2Motion, and upstream bug report

Date: 20 October 2026

This combined report covers the supplied
`ba_louisa_mathes_figma_log2motion-v1.0.0` project and the subsequent audit of
this repository's maintained plugin, bridge, simulator, and upstream libraries.
The downloaded original source files remain unchanged. The fixes are in this
repository and [PR #1](https://github.com/M-Colley/Log2Motion/pull/1).

The original-project findings below are attributed to the supplied source.
The follow-up audit used maintained-source baseline `5d8d997`; it also identifies
an upload-queue issue caught while reviewing the audit's own changes. Migration
regressions are identified separately from original defects.

Findings were checked through source inspection, focused reproductions, mocked
Figma/UI APIs, and real WebSocket-to-simulator tests. The verification and limits
sections distinguish automated coverage from remaining manual checks.

## Where the fixed code lives

| Component | Fixed source |
| --- | --- |
| Figma exports, coordinates, selections, connectors, and highlights | [code.ts](../integration/figma-plugin/code.ts) |
| Plugin controls, charts, comparisons, session handling, and reconnects | [ui.html](../integration/figma-plugin/ui.html) |
| WebSocket protocol, simulation subprocesses, and cancellation | [server.py](../integration/server.py) |
| Sessions, run identities, comparisons, outcomes, and saved charts | [history.py](../integration/history.py) |
| Export conversion and chart sampling/timestamps | [transformation.py](../integration/transformation.py), [preparation.py](../integration/preparation.py) |
| PDF generation and pagination | [reports.py](../integration/reports.py) |
| Gesture replay, release movements, video, and Android touch events | [log2motion.py](../environment/log2motion.py) |
| Environment resets, contact state, timing, and Android initialization | [log2motion_env.py](../environment/log2motion_env.py) |
| MyoSuite adapters and local fatigue/camera workarounds | [base_v0_custom.py](../environment/myosuite_custom/base_v0_custom.py), [env_base_custom.py](../environment/myosuite_custom/env_base_custom.py), [mj_renderer_custom.py](../environment/myosuite_custom/mj_renderer_custom.py) |
| CLI validation and framed success/error output | [log2motion_visualization.py](../log2motion_visualization.py) |

The upstream MyoSuite changes are linked in the upstream-fixes section. Build
and import this repository's [plugin manifest](../integration/figma-plugin/manifest.json)
to use the repaired plugin; updating Python packages alone does not replace the
original plugin code.

## Original project findings

### 1. Different selected targets became the same center tap

**Cause:** `code.ts` exported selected nodes and their `parentId`, but omitted
the ancestor frame geometry needed by `Interface/transformation.py`.
`normalize()` silently returned `(0.5, 0.5)` when it could not find the frame.
Consequently, different buttons could produce identical simulated taps.

**Fix:** Export each selected node's `normalizedCenter` directly from Figma,
using the node's absolute transform and the inverse screen-frame transform.
Send coordinates in Log2Motion's `[y, x]` order. Reject missing, singular,
zero-size, non-finite, or out-of-screen geometry with an actionable error.
The current screen convention is the outermost containing Figma frame.

**Implementation:** `integration/figma-plugin/code.ts::normalizedCenter()` and
`integration/transformation.py::normalize()`. Regression tests check distinct
targets, missing frames, and nested/rotated transforms.

### 2. Repeated taps on one node disappeared

**Cause:** `Interface/transformation.py` converted the selected sequence to a
dictionary keyed by node ID, then constructed the click path from that
dictionary. A path such as `A → B → A` became `A → B`.

**Fix:** Keep the ordered click sequence separately from the node lookup map.
A node identifies an element; an occurrence identifies a click. Preserve
every occurrence when exporting, simulating, and storing history.

**Implementation:** `collectSelectedNodes()` in the plugin and
`integration/transformation.py::build_click_flow()`. Tests verify repeated
selections in the export, simulation input, and saved history.

### 3. Connector cleanup could delete ordinary design elements

**Cause:** The original plugin recognized its own objects by names. Selection
filtering excluded ordinary names beginning with `Vector`, while cleanup
matched names such as `V1: ...` or any name containing `→`. A designer's own
node named `Buy → Checkout` could therefore be mistaken for a plugin arrow.
The cleanup predicate was not limited to vector nodes.

**Fix:** Mark generated connectors with
`setPluginData("log2motion-owned", "connector")` and clean up only marked
objects or tracked connector references. Do not infer ownership from names.

**Implementation:** `integration/figma-plugin/code.ts`. Tests verify that
ordinary vectors remain selectable and user design nodes survive cleanup.

### 4. Asynchronous arrow updates could overlap

**Cause:** Arrow creation, asynchronous vector-network updates, node lookups,
and selection changes could overlap. Some vector operations were not awaited.
A stale drawing operation could finish after the click path changed or after
selection mode ended, leaving duplicate or obsolete arrows.

**Fix:** Serialize drawing through a promise queue, await vector-network
updates, and render from a snapshot of the current path. Cleanup removes only
IDs confirmed missing instead of replacing the path with an older snapshot.

**Implementation:** `drawVectors()`, `renderVectors()`, and
`cleanupClickPath()` in `integration/figma-plugin/code.ts`. A deterministic
mocked Figma API regression changes the selection from `A` to `B` while a
lookup is pending: the original restores `A`, while the fixed version keeps
`B`. Live Figma timing behavior still needs manual acceptance testing.

### 5. Run IDs stopped being unique after history retention

**Cause:** `Interface/history.py` assigned IDs using the retained history
length. Once the ten-item retention limit was reached, later runs reused the
same ID. Selecting, comparing, or attaching charts to a particular run became
ambiguous.

**Fix:** Allocate IDs from the persistent, monotonically increasing
per-session run counter. Trimming old history must not reset identity.

**Implementation:** `integration/history.py::record_simulation_run()`.
Regression tests create more than ten runs and check uniqueness after trimming.

### 6. Chart snapshots could be stored on the wrong run

**Cause:** History entries stored their identifier as `id`, but
`save_charts_to_history()` searched for `run_id`. A failed lookup silently
fell back to the latest run, even when the request named a different run.

**Fix:** Match the requested identifier against the stored `id`, normalize
identifier types consistently, and reject a missing run instead of silently
choosing another one.

**Implementation:** `integration/history.py::save_charts_to_history()`.
The retention/chart regression verifies that snapshots attach to the requested
run rather than the newest entry.

### 7. Switching sessions could redirect results or delayed charts

**Cause:** The backend could resolve the active session when simulation
finished, and asynchronous chart export used the session selected at export
time. Switching sessions while simulation or thumbnail rendering was still
running could associate a run or its images with another session.

**Fix:** Capture the originating session before starting the simulation,
include `session_id` in the result, and use that captured ID for history and
chart updates.

**Implementation:** `integration/server.py`, `integration/history.py`, and
`integration/figma-plugin/ui.html::exportChartsToServer()`. Tests verify that
the explicitly captured originating session receives the history even when
another session is active.

### 8. Run comparison confused repeated click occurrences

**Cause:** Comparison logic treated a node ID as a unique click identity.
Comparing `A → B → A` with `A → B` could miss the removed final tap and report
a misleading reorder between the two occurrences of `A`.

**Fix:** Compare occurrences using `(node_id, occurrence_index)`. Include click
position/occurrence information in the comparison output and labels.

**Implementation:** `integration/history.py::compare_runs()` and comparison
rendering in `ui.html`/`reports.py`. Tests cover removal, addition, and
reordering of repeated clicks.

### 9. Moving a containing group could be absent from comparisons

**Cause:** Comparison checked node-local `x` and `y`. Moving or transforming a
parent can move the actual target while leaving those local values unchanged.
The old export also lacked the normalized screen coordinates needed to detect
this difference.

**Fix:** Compare exported `normalizedCenter` in addition to local position and
size. Show a separate `Screen position` change in the UI and PDF report.

**Implementation:** `integration/history.py::compare_runs()`, `ui.html`, and
`reports.py`. A regression keeps local coordinates unchanged while changing
the normalized target position.

### 10. Chart downsampling exceeded its limit and could lose contacts

**Cause:** `Interface/preparation.py` used
`len(samples) // max_points` as its stride. For example, 599 samples with a
300-point limit produced a stride of one and retained all 599 samples. Simple
striding could also omit the last sample and brief contact events.

**Fix:** Select at most the requested number of indices distributed across
the full trajectory, including both endpoints when more than one point is
retained. Map original button-contact events to the nearest retained point.

**Implementation:** `integration/preparation.py::reduce_data_for_figma()`.
Parameterized tests check size bounds, endpoints, and short contacts around
downsampling boundaries.

### 11. Wrong-screen touches appeared as successful button contacts

**Cause:** The reducer used the truthiness of `contact_information`. Both
`1` (target contact) and `2` (other screen contact) became the same chart marker,
although history metrics counted only `1`. During a swipe, other-screen
contact can also be an expected part of the drag path.

**Fix:** Add a button-contact marker only for `contact_information == 1`.
Keep other contact categories distinct; do not promote them to target hits
while downsampling.

**Implementation:** `integration/preparation.py`. Tests cover mixed values
`0`, `1`, and `2`, including long downsampled trajectories.

### 12. PDF reports could show invented dates and times

**Cause:** `Interface/createPDF.py` expected `date`/`time` fields, while history
stored `timestamp`. Missing fields fell back to hard-coded example dates and
times, such as `20.04.2026`.

**Fix:** Parse the recorded timestamp and use it consistently in single-run
and comparison reports. Missing or invalid timestamps must be shown as
unknown, never replaced with plausible-looking example dates.

**Implementation:** `integration/reports.py::run_date_time()`. Tests verify
the real recorded date and exercise PDF generation with chart snapshots.

### 13. Ordinary Figma names could break PDF generation

**Cause:** `Interface/createPDF.py` interpolated Figma element names into
ReportLab paragraph markup without escaping them. A legitimate name such as
`Button <A> & B` was interpreted as markup and could raise a `ValueError`
instead of generating a report.

**Fix:** XML-escape user-provided run and element names before inserting them
into ReportLab paragraphs. Keep intentional report formatting separate from
user content.

**Implementation:** `integration/reports.py`. The report regression generates
a PDF containing `Button <A> & B`; chart/geometry comparison reports are also
exercised.

### 14. Valid output could hide a failed process; Windows newlines broke parsing

**Cause:** The original server accepted a framed JSON result regardless of
the child exit code. Its delimiter splitting also required exact `\n`
newlines, although Windows Python output may use `\r\n`.

**Fix:** Validate both the process exit code and the framed JSON payload.
Accept LF/CRLF output, reject partial/missing/invalid results, and send a
structured `SIM_ERROR` while keeping the WebSocket connection usable.

**Implementation:** `integration/server.py::parse_simulation_output()`.
Tests cover CRLF, missing payloads, nonzero exits with apparently successful
JSON, and useful framed error messages. The error-message preservation issue
in our initial replacement parser was caught and fixed during this migration;
it is not attributed to the original implementation.

### 15. Completion did not establish whether each physical gesture succeeded

**Cause:** The original result contract did not expose per-gesture success
and timeout information to the plugin. A finished process and available
trajectory were insufficient evidence of a successful click. Comparisons
could favor shorter/lower-cost runs without accounting for failed gestures.

**Fix:** Carry `is_success`, termination, timeout, and phase metadata through
the CLI, bridge, saved history, UI, and PDFs. Preserve useful failed
trajectories, label failures explicitly, and prioritize success rate in run
comparisons. Treat legacy records without outcome metadata as unknown.

**Implementation:** `server.py`, `history.py`, `reports.py`, and `ui.html` under
`integration/`. Tests cover partial failure, timed-out gestures, unknown legacy
outcomes, and successful-versus-fast-failed comparisons. Grouped timeout flags
introduced with the new replay grouping also receive a separate regression.

### 16. Charts depended on a remote, unversioned Chart.js script

**Cause:** The UI loaded Chart.js from a CDN despite having a local npm
dependency. Offline use, a blocked CDN, or an incompatible future CDN version
could prevent chart rendering.

**Fix:** Bundle the locked Chart.js dependency into the generated UI using
esbuild. Keep optional web fonts separate from the chart runtime requirement.

**Implementation:** `integration/figma-plugin/build.mjs` and `package-lock.json`.
Plugin tests parse the generated scripts and verify that charts no longer
require the CDN.

## Portability and robustness improvements

These are additional fixes or safeguards, not claims that every original
installation failed:

- Resolve history storage independently of the current working directory;
  use `LOG2MOTION_DATA_DIR` and atomic file replacement. Launching the original
  server from another directory could otherwise read/write another history.
- Use the running Python interpreter by default, with an explicit
  `LOG2MOTION_PYTHON` override. The original preferred a specifically named
  Conda environment but already had a fallback when Conda was unavailable;
  the change makes interpreter selection more predictable.
- Pass actions in a temporary JSON file instead of a Python literal in a
  command-line argument. This avoids Windows command-line length limits and
  fragile argument serialization.
- Bound simulation duration and captured output, and terminate/reap timed-out
  children. Drain stdout and stderr concurrently. The original server already
  drained both streams concurrently; no classic sequential-pipe deadlock was
  established in it.
- Validate malformed exports/messages and return actionable errors without
  closing an otherwise healthy WebSocket connection.
- Make PDF fonts optional, with module-relative paths,
  `LOG2MOTION_FONT_DIR`, and built-in ReportLab fallbacks. The supplied original
  includes its font files and loads them successfully; this improvement
  supports deployments that do not include those files.

## Follow-up audit of the maintained integration

The following findings were reproduced against maintained-source baseline
`5d8d997`. They extend the original findings with distinct triggers, including
session-ID reuse, changing export state, delayed UI responses, and runtime
lifecycle failures. The retryable chart-save queue issue is explicitly marked
as a defect caught in this audit's own changes.

### Figma plugin lifecycle and UI

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

The [verification section](#verification) links the Figma and UI lifecycle
regressions. UI timing regressions also check real chart dataset coordinates
against nonuniform sample times and exact gesture durations.

### Bridge, history, and reports

#### Deleted sessions could receive somebody else's delayed results

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

#### Another client's session selection could redirect a simulation

Capturing the active session on the server protected a run from later changes,
but still chose the wrong session if another plugin client had already changed
that global active session. The request now carries the initiating client's
`session_id`; the bridge validates and honors it. Legacy clients retain the
active-session fallback.

The regression creates two sessions, leaves the second globally active, runs a
simulation explicitly for the first, and verifies that only the first receives
the history. A nonexistent explicit session is rejected before inference.

#### Disconnecting a client could monopolize the simulation lock

The handler awaited its simulation directly. If the client disconnected before
the next progress update, inference continued and retained the shared lock until
it finished or hit the 30-minute timeout. A reopened plugin could remain queued.

The handler now races each request against connection closure, cancels abandoned
work, and awaits its cleanup. Regression tests check that a subsequent client can
run and that a real abandoned subprocess is terminated and reaped.

This was an application lifecycle error, not a defect in the WebSockets library.

#### Long click paths could make both PDF exports fail

All selected elements were placed inside a single table row. A legitimate
90-element path with moderately long names made that row taller than a page;
ReportLab correctly raised `LayoutError` because it could not split the row.

The element lists now use multiple rows and permit splitting a tall row's
contents. The regression generates both single-run and comparison PDFs for the
same 90-element path. This fixes the report layout rather than modifying
ReportLab.

#### Downsampled chart times drifted from the actual simulation

Rounded downsampling indices are not uniformly spaced. Sending only their
average `dt`, then calculating the next gesture's offset as `count * dt`, shifted
contacts and inflated gesture durations. For example, six samples at 6 ms
intervals reduced to four samples have actual times `[.006, .018, .024, .036]`,
which cannot be reconstructed with one uniform interval.

Chart data now includes the retained `times` and actual `duration`. The UI uses
those values for time coordinates and gesture offsets, with a fallback for older
payloads. Python and UI regressions check the uneven intervals and the final
duration.

### Log2Motion simulation and CLI

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

## Related simulation migration fixes

These affect the Figma workflow but are separate from the original plugin:

- Repeated physical taps require a release movement. The migrated replay
  includes release/drag phases within one gesture result, so repeated targets
  do not become immediate successes while the finger remains in contact.
- Virtual start/rest targets must not be selected as real screen buttons.
  The screen-target lookup excludes them.
- Runtime target movement/resizing refreshes derived MuJoCo collision bounds.
  The scope section explains why this is application responsibility rather than
  evidence of a new MuJoCo defect.

## Upstream MyoSuite fixes

### Offscreen rendering ignored free-camera settings

MyoSuite 3 ignored configured offscreen free-camera settings. The fix is in
[upstream PR #556](https://github.com/MyoHub/myosuite/pull/556), with a local camera
workaround in `environment/myosuite_custom/mj_renderer_custom.py`.

### Native Python 3.15 installation required optional ONNX dependencies

Unused ONNX Runtime dependencies prevented installation on native Python 3.15.
[Upstream PR #557](https://github.com/MyoHub/myosuite/pull/557) makes them optional
there. The repository's lock pins the compatible source for Python 3.15; see the
[validation notes](validation.md) for dependency details and ONNX feature limits.

### Fatigue resets mutated the caller's configuration

`CumulativeFatigue.reset(fatigue_reset_vec=...)` retained the supplied NumPy array
as internal state. Advancing fatigue then changed the caller's reset vector.
Repeated episodes started from progressively different fatigue values; read-only
input could also fail when the simulation tried to write it.

[MyoSuite PR #558](https://github.com/MyoHub/myosuite/pull/558) copies the supplied
vector and tests input ownership, repeatable resets, and read-only arrays. The
new regression cases fail before the patch and pass afterward. Log2Motion also
copies the input at its own reset boundary while the upstream fix awaits release.
Its local regression verifies that a read-only fixed reset vector remains
unchanged across steps and repeated resets.

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

Regression source:

- [Plugin/Figma API tests](../integration/figma-plugin/tests/export.test.cjs)
- [Figma lifecycle tests](../integration/figma-plugin/tests/plugin_lifecycle.test.cjs)
- [UI lifecycle tests](../integration/figma-plugin/tests/ui_lifecycle.test.cjs)
- [Bridge/history/PDF tests](../tests/test_figma_integration.py)
- [Additional integration regressions](../tests/test_audit_regressions.py)
- [Real WebSocket, CLI, and video tests](../tests/test_end_to_end.py)
- [Physical simulation tests](../tests/test_simulation.py)

See [validation notes](validation.md) for Python compatibility, CI, dependency
requirements, and the spatial rollout report.

## Applying the fixes

Build the maintained plugin and import its manifest in Figma Desktop:

```powershell
cd integration/figma-plugin
npm ci
npm run build
```

Use `integration/figma-plugin/manifest.json` from this repository. Updating only
the Python packages does not repair the original plugin's missing coordinate
export or unsafe cleanup logic. Start the matching bridge from the repository
root with `uv run python -m integration.server`.

## Scope and remaining validation limits

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
