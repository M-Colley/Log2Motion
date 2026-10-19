# Figma plugin bug report and fixes

Date: 20 October 2026

This report covers the supplied `ba_louisa_mathes_figma_log2motion-v1.0.0`
project: its Figma plugin and the Python `Interface` bridge it depends on.
The original source files were not edited. Fixes are implemented in this
repository's `integration/` directory.

Findings were checked through source inspection, focused reproductions,
mocked Figma API tests, and real WebSocket-to-simulator tests. Interaction
inside Figma Desktop itself has not been manually exercised.

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

## Related simulator and package issues

These affect the Figma workflow but are separate from the original plugin:

- Repeated physical taps require a release movement. The migrated replay
  includes release/drag phases within one gesture result, so repeated targets
  do not become immediate successes while the finger remains in contact.
- Virtual start/rest targets must not be selected as real screen buttons.
  The screen-target lookup excludes them.
- MuJoCo 3.16 requires collision bounds to be refreshed after runtime geometry
  movement/resizing. This migration-specific compatibility issue is fixed.
- MyoSuite 3 ignored configured offscreen free-camera settings:
  [upstream PR #556](https://github.com/MyoHub/myosuite/pull/556).
- Native Python 3.15 installation required making unused ONNX Runtime optional:
  [upstream PR #557](https://github.com/MyoHub/myosuite/pull/557).

## Verification and applying the fixes

Regression coverage is in:

- [Plugin/Figma API tests](../integration/figma-plugin/tests/export.test.cjs)
- [Bridge/history/PDF tests](../tests/test_figma_integration.py)
- [Real WebSocket, CLI, and video tests](../tests/test_end_to_end.py)
- [Physical simulation tests](../tests/test_simulation.py)

See [validation notes](validation.md) for Python compatibility, CI, dependency
requirements, the spatial rollout report, and remaining manual-test limits.

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
